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
LOADER_ID = "panel-bepinex"
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


def server_releases(catalog):
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
    return releases


def dependency_requirements(key, artifact):
    dependencies = artifact.get("dependencies") or []
    parent = artifact.get("extends")
    if not isinstance(dependencies, list):
        raise ValueError(f"The catalog has invalid dependencies for {key}.")
    requirements = []
    for dependency in [*dependencies, *([parent] if parent else [])]:
        if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
            raise ValueError(f"The catalog has an invalid dependency for {key}.")
        target = dependency["id"].lower()
        minimum = dependency.get("version")
        if not PACKAGE_ID.fullmatch(target) or (minimum and not version_key(minimum)):
            raise ValueError(f"The catalog has an unsupported dependency requirement for {key}.")
        requirements.append((target, minimum))
    return requirements


def incompatibilities(artifact):
    entries = artifact.get("incompatibilities")
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            yield entry["id"].lower(), entry.get("version")


def catalog_conflicts(chosen):
    for key, (asset, artifact) in chosen.items():
        for target, maximum in incompatibilities(artifact):
            if target not in chosen or target == key:
                continue
            other = chosen[target][0]
            if not version_key(maximum) or version_key(other["version"]) <= version_key(maximum):
                limit = f" through v{maximum}" if version_key(maximum) else ""
                yield f"The catalog marks {asset['name']} v{asset['version']} as incompatible with {other['name']}{limit} (set uses v{other['version']})."


def identify_plan(plan):
    plan["id"] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:32]
    return plan


def make_plan(identifier, overrides):
    details = workshop.item_details(identifier)
    mods = details.get("mods", {})
    requirements = mods.get("requirements", [])
    if not requirements or mods.get("catalog_unavailable"):
        raise ValueError("No dependency list is available. Check the Steam item and mod catalog first.")

    releases = server_releases(workshop.mod_catalog(int(time.monotonic() // 600)))

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
    chosen = {}
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
            for target, minimum in dependency_requirements(key, artifact):
                resolve(target, minimum)
            resolved[key] = {**asset, "id": key, "parent": parent["id"].lower() if parent else None}
            chosen[key] = (asset, artifact)
        finally:
            visiting.remove(key)

    for key in direct:
        try:
            resolve(key)
        except ValueError as error:
            if str(error) not in problems:
                problems.append(str(error))

    problems.extend(dict.fromkeys(catalog_conflicts(chosen)))
    assets = [BEPINEX, *resolved.values()]
    plan = {"item": identifier, "title": details.get("title") or identifier, "choices": choices,
            "overrides": {choice["id"]: overrides[choice["id"]] for choice in choices if choice["id"] in overrides},
            "problems": problems, "assets": assets}
    return identify_plan(plan)


def update_plan(selected, package, version, catalog, *, exact=False):
    """Preview one update and reconcile its dependencies across the whole server set."""
    releases = server_releases(catalog)
    current = {asset["id"]: asset for asset in selected["assets"] if "id" in asset}
    loader = next((asset for asset in selected["assets"] if "id" not in asset), BEPINEX)
    offered = releases.get(package, [])
    pins = selected.get("pins", {})
    target_release = next((release for release in offered if release[0]["version"] == version), None)
    if package not in current or not target_release:
        raise ValueError("That version is unavailable for this server set. Check the catalog again.")
    if not exact and (package in pins or offered[0][0]["version"] != version
                      or version_key(version) <= version_key(current[package]["version"])):
        raise ValueError("That update is no longer available for this server set. Check for updates again.")
    if exact and version == current[package]["version"]:
        raise ValueError("This version is already in the server set. Use Pin current version to keep it.")

    chosen = {}
    for key, asset in current.items():
        if key == package:
            chosen[key] = target_release
            continue
        match = next((release for release in releases.get(key, [])
                      if release[0]["version"] == asset["version"] and release[0]["hash"] == asset.get("hash")), None)
        if not match:
            raise ValueError(f"Cannot verify the current release for {asset['name']}. Review a fresh mission install plan.")
        chosen[key] = match

    # Recheck every package after a dependency changes. Versions only increase,
    # and an explicit update target remains pinned to the version reviewed.
    for _ in range(MOD_LIMIT * 2):
        changed = False
        for key, (_, artifact) in list(chosen.items()):
            for target, minimum in dependency_requirements(key, artifact):
                previous = chosen.get(target)
                if previous and (not minimum or version_key(previous[0]["version"]) >= version_key(minimum)):
                    continue
                if target == package:
                    raise ValueError(f"{chosen[key][0]['name']} requires {chosen[target][0]['name']} v{minimum} or newer. You chose v{version}.")
                if target in pins:
                    raise ValueError(f"{chosen[key][0]['name']} requires {chosen[target][0]['name']} v{minimum} or newer. This set pins v{pins[target]}.")
                options = releases.get(target, [])
                replacement = next((item for item in reversed(options)
                                    if version_key(item[0]["version"]) >= version_key(minimum)), None) if minimum else next(iter(options), None)
                if not replacement:
                    required = f" v{minimum} or newer" if minimum else ""
                    raise ValueError(f"{chosen[key][0]['name']} requires {target}{required}, which the catalog cannot supply.")
                chosen[target] = replacement
                changed = True
                if len(chosen) > MOD_LIMIT:
                    raise ValueError("This mod set exceeds the supported package limit.")
        if not changed:
            break
    else:
        raise ValueError("The catalog dependencies could not be resolved into one mod set.")

    conflict = next(catalog_conflicts(chosen), None)
    if conflict:
        raise ValueError(conflict)

    ordered = {}
    visiting = set()

    def order(key):
        if key in ordered:
            return
        if key in visiting:
            raise ValueError(f"The catalog has a circular dependency involving {key}.")
        visiting.add(key)
        asset, artifact = chosen[key]
        for target, _ in dependency_requirements(key, artifact):
            order(target)
        parent = artifact.get("extends")
        ordered[key] = {**asset, "id": key, "parent": parent["id"].lower() if parent else None}
        visiting.remove(key)

    for key in chosen:
        order(key)
    changes = []
    for key, asset in ordered.items():
        old = current.get(key, {}).get("version")
        if old != asset["version"]:
            changes.append({"id": key, "name": asset["name"], "from": old, "to": asset["version"],
                            "requested": key == package, "game_version": asset["game_version"]})
    requirements = [{"name": ordered[target]["name"], "minimum": minimum, "version": ordered[target]["version"]}
                    for target, minimum in dependency_requirements(package, target_release[1])]
    return identify_plan({"item": selected["item"], "title": selected["title"], "base_id": selected["id"],
                          "base_pins": pins, "pins": {**pins, **({package: version} if exact else {})},
                          "choices": [], "overrides": selected.get("overrides", {}), "problems": [],
                          "assets": [loader, *ordered.values()], "changes": changes, "requirements": requirements})


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
    return read_set(root, key)


def read_set(root, key):
    if not isinstance(key, str) or not SET_ID.fullmatch(key):
        return None
    manifest = root / key / "panel-plan.json"
    if manifest.resolve() != manifest or not manifest.is_file():
        return None
    with manifest.open("rb") as stream:
        plan = json.loads(stream.read(512 * 1024))
    if not isinstance(plan, dict) or plan.get("id") != key:
        return None
    pin_file = manifest.parent / "panel-pins.json"
    if pin_file.is_file() and not pin_file.is_symlink():
        override = workshop.read_metadata(pin_file).get("pins")
        if isinstance(override, dict):
            plan["pins"] = override
    return plan


def pin_version(data_dir, key, package, pinned):
    selected = selected_set(data_dir)
    if not selected or selected["id"] != key:
        raise ValueError("The server mod set changed. Refresh Mod management and try again.")
    asset = next((asset for asset in selected["assets"] if asset.get("id") == package), None)
    if not asset:
        raise ValueError("This package is absent from the server mod set.")
    pins = dict(selected.get("pins", {}))
    if pinned:
        pins[package] = asset["version"]
    else:
        pins.pop(package, None)
    workshop.write_metadata(mod_root(data_dir) / key / "panel-pins.json", {"pins": pins})


def saved_sets(data_dir):
    root = mod_root(data_dir)
    if not root.is_dir():
        return []
    sets = []
    for folder in root.iterdir():
        try:
            if folder.is_symlink() or not SET_ID.fullmatch(folder.name) or not loader_ready(folder):
                continue
            plan = read_set(root, folder.name)
            if plan:
                pins = plan.get("pins", {})
                versions = [{"name": asset["name"], "version": asset["version"], "pinned": asset.get("id") in pins}
                            for asset in plan["assets"]]
                sets.append({"id": plan["id"], "title": plan["title"], "versions": versions})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(sets, key=lambda item: (item["title"].casefold(), item["id"]))


def activate_saved_set(data_dir, key):
    root = mod_root(data_dir)
    if not read_set(root, key) or not loader_ready(root / key):
        raise ValueError("That saved mod set is unavailable.")
    select_set(data_dir, key)


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


def install_set(data_dir, plan, progress, *, activate=True):
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
    if activate:
        select_set(data_dir, plan["id"])
