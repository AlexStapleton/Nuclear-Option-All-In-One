"""Resolve NOMNOM packages and stage a dedicated server mod set."""

import hashlib
import json
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile, TemporaryDirectory
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

import workshop


BEPINEX = {
    "name": "BepInEx (Linux x64)", "version": "5.4.23.5",
    "file": "BepInEx_linux_x64_5.4.23.5.zip",
    "url": "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_linux_x64_5.4.23.5.zip",
    "hash": "sha256:e538560be65739f562519ab518a75f9c65b3f57f87457403ae7cde683c12dab7",
}
SET_ID = re.compile(r"[a-f0-9]{32}")
PACKAGE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,119}")
MOD_LIMIT = 48
ASSET_LIMIT = 2 * 1024**3
EXTRACT_LIMIT = 6 * 1024**3


def version_key(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d{1,6}(?:\.\d{1,6}){0,5}", version):
        return ()
    parts = [int(part) for part in version.split(".")]
    return tuple(parts + [0] * (6 - len(parts)))


def release_asset(package, artifact):
    file, url, digest = (artifact.get(key) for key in ("fileName", "downloadUrl", "hash"))
    if not all(isinstance(value, str) for value in (file, url, digest)):
        return None
    if (not re.fullmatch(r"[\w.+ -]{1,180}\.(zip|dll|nobp|nomap|nomapz)", file, re.I | re.ASCII)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest)
            or not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/releases/download/[^/?#]+/[^/?#]+", url, re.ASCII)):
        return None
    return {"file": file, "url": url, "hash": digest, "version": artifact["version"],
            "name": package.get("displayName") or package["id"], "game_version": artifact.get("gameVersion")}


def make_plan(identifier, overrides):
    details = workshop.item_details(identifier)
    mods = details.get("mods", {})
    requirements = mods.get("requirements", [])
    if not requirements or mods.get("catalog_unavailable"):
        raise ValueError("No dependency list is available. Check the Steam item and mod catalog first.")

    catalog = workshop.mod_catalog(int(time.monotonic() // 600))
    packages = {entry["id"].lower(): entry for entry in catalog
                if isinstance(entry.get("id"), str) and PACKAGE_ID.fullmatch(entry["id"].lower())}
    releases = {}
    for key, package in packages.items():
        if str(package.get("isClientOrServer", "")).casefold().startswith("client"):
            continue
        artifacts = package.get("artifacts")
        releases[key] = []
        for artifact in artifacts if isinstance(artifacts, list) else []:
            if not isinstance(artifact, dict) or artifact.get("category") != "release" or not version_key(artifact.get("version")):
                continue
            asset = release_asset(package, artifact)
            if asset:
                releases[key].append((asset, artifact))
        releases[key].sort(key=lambda item: version_key(item[0]["version"]), reverse=True)

    pinned = {}
    choices = []
    problems = []
    direct = []
    for requirement in requirements:
        key = requirement.get("package_id")
        requested = requirement["requested_version"]
        exact = requirement.get("matched_version")
        options = [asset["version"] for asset, _ in releases.get(key, [])]
        if not options:
            problems.append(f"No supported server release was found for {requirement['name']}.")
            continue
        if requested and exact not in options:
            choices.append({"id": key, "name": requirement["name"], "requested": requested,
                            "versions": list(dict.fromkeys(options))[:12]})
            exact = overrides.get(key)
            if exact not in choices[-1]["versions"]:
                problems.append(f"Choose an available version of {requirement['name']} to replace v{requested}.")
                continue
        if exact:
            pinned[key] = exact
        direct.append(key)

    resolved = {}
    visiting = set()

    def resolve(key, minimum=None):
        if key in visiting:
            raise ValueError(f"The catalog has a circular dependency involving {key}.")
        if minimum and not version_key(minimum):
            raise ValueError(f"The catalog has an unsupported version requirement for {key}.")
        if key in resolved:
            if minimum and version_key(resolved[key]["version"]) < version_key(minimum):
                raise ValueError(f"{key} requires at least v{minimum}, which conflicts with the selected version.")
            return
        if len(visiting) + len(resolved) >= MOD_LIMIT:
            raise ValueError("This mod set exceeds the supported package limit.")
        for asset, artifact in releases.get(key, []):
            if key in pinned and asset["version"] != pinned[key]:
                continue
            if minimum and version_key(asset["version"]) < version_key(minimum):
                continue
            break
        else:
            raise ValueError(f"No supported release satisfies the requirements for {key}.")
        visiting.add(key)
        try:
            parent = artifact.get("extends")
            dependencies = artifact.get("dependencies") or []
            if not isinstance(dependencies, list):
                raise ValueError(f"The catalog has invalid dependencies for {key}.")
            for dependency in [*dependencies, *([parent] if parent else [])]:
                if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
                    raise ValueError(f"The catalog has an invalid dependency for {key}.")
                resolve(dependency["id"].lower(), dependency.get("version"))
            resolved[key] = {**asset, "id": key, "parent": parent["id"].lower() if parent else None}
        finally:
            visiting.remove(key)

    for key in direct:
        try:
            resolve(key)
        except ValueError as error:
            if str(error) not in problems:
                problems.append(str(error))

    assets = [BEPINEX, *resolved.values()]
    plan = {"item": identifier, "title": details.get("title") or identifier, "choices": choices,
            "overrides": {choice["id"]: overrides[choice["id"]] for choice in choices if choice["id"] in overrides},
            "problems": problems, "assets": assets}
    plan["id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:32]
    return plan


def mod_root(data_dir):
    root = data_dir.resolve() / ".panel-mods"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("The panel mod folder must be a regular directory.")
    return root


def selected_set(data_dir):
    root = mod_root(data_dir)
    marker = root / "selected"
    if marker.is_symlink() or not marker.is_file():
        return None
    with marker.open(encoding="ascii") as stream:
        key = stream.read(34).strip()
    if not SET_ID.fullmatch(key):
        return None
    manifest = root / key / "panel-plan.json"
    if manifest.resolve() != manifest or not manifest.is_file():
        return None
    return json.loads(manifest.read_text(encoding="utf-8"))


def select_set(data_dir, key):
    root = mod_root(data_dir)
    root.mkdir(exist_ok=True)
    with NamedTemporaryFile(mode="w", encoding="ascii", dir=root, delete=False) as stream:
        path = Path(stream.name)
        stream.write(key + "\n")
    try:
        path.chmod(0o644)
        path.replace(root / "selected")
    finally:
        path.unlink(missing_ok=True)


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        address = urlsplit(newurl)
        if (address.scheme != "https" or address.netloc not in
                ("github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")):
            raise ValueError("A release download redirected outside GitHub.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(asset, path):
    digest = hashlib.sha256()
    total = 0
    deadline = time.monotonic() + 1200
    opener = build_opener(ReleaseRedirects())
    with opener.open(asset["url"], timeout=30) as response, path.open("xb") as stream:
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > ASSET_LIMIT or time.monotonic() > deadline:
                raise ValueError(f"{asset['name']} exceeded the download limit.")
            digest.update(chunk)
            stream.write(chunk)
    if f"sha256:{digest.hexdigest()}" != asset["hash"]:
        raise ValueError(f"The downloaded checksum for {asset['name']} did not match the catalog.")
    return total


def unpack_zip(path, target, budget, map_package=False):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 4096:
            raise ValueError("A mod archive has too many files.")
        for entry in entries:
            raw = entry.filename
            relative = PurePosixPath(raw)
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (not relative.parts or relative.is_absolute() or ".." in relative.parts or "\\" in raw
                    or ":" in raw or mode not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError("A mod archive contains an unsafe path or file type.")
            if entry.is_dir():
                continue
            budget -= entry.file_size
            if budget < 0:
                raise ValueError("The mod set exceeded the extracted size limit.")
            # A NOCustomMaps add-on may ship its bundle at the archive root or in a wrapper folder.
            destination = target / (relative.name if map_package and relative.suffix.lower() in (".nomap", ".nomapz") else relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
    return budget


def loader_ready(profile):
    return (profile / "BepInEx/core/BepInEx.Preloader.dll").is_file() and (profile / "libdoorstop.so").is_file()


def install_set(data_dir, plan, progress):
    if plan["problems"] or not SET_ID.fullmatch(plan["id"]):
        raise ValueError("Review a complete install plan first.")
    root = mod_root(data_dir)
    root.mkdir(exist_ok=True)
    destination = root / plan["id"]
    if destination.is_symlink():
        raise ValueError("The mod set folder must be a regular directory.")
    if not destination.exists():
        with TemporaryDirectory(dir=root, prefix="staging-") as temporary:
            stage = Path(temporary)
            profile = stage / "profile"
            profile.mkdir()
            payload = stage / "download"
            budget = EXTRACT_LIMIT
            assets = plan["assets"]
            plugin_dirs = {}
            for index, asset in enumerate(assets, 1):
                progress(f"Installing {index} of {len(assets)}: {asset['name']} v{asset['version']}…")
                size = download(asset, payload)
                key = asset.get("id")
                parent = asset.get("parent")
                map_package = parent == "nocustommaps"
                target = profile
                if key:
                    if map_package:
                        target = plugin_dirs[parent] / "maps"
                    elif parent:
                        target = plugin_dirs[parent] / "addons" / key
                    else:
                        folder = "NOCustomMaps" if key == "nocustommaps" else key
                        target = profile / "BepInEx" / "plugins" / folder
                    plugin_dirs[key] = target
                if asset["file"].lower().endswith(".zip"):
                    budget = unpack_zip(payload, target, budget, map_package)
                else:
                    budget -= size
                    if budget < 0:
                        raise ValueError("The mod set exceeded the extracted size limit.")
                    target.mkdir(parents=True, exist_ok=True)
                    payload.rename(target / asset["file"])
                payload.unlink(missing_ok=True)
            if not loader_ready(profile):
                raise ValueError("The staged set is missing the Linux mod loader.")
            config = profile / "BepInEx" / "config"
            config.mkdir(exist_ok=True)
            (config / "BepInEx.cfg").write_text(
                "[Chainloader]\nHideManagerGameObject = true\n\n[Logging.Console]\nEnabled = true\n", encoding="ascii"
            )
            (profile / "panel-plan.json").write_text(json.dumps(plan), encoding="utf-8")
            profile.rename(destination)
    if not loader_ready(destination):
        raise ValueError("The staged set is missing the Linux mod loader.")
    select_set(data_dir, plan["id"])
