"""Workshop item identifiers, metadata, and the dedicated server's local cache."""

import json
import re
from functools import lru_cache
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic, time
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

APP_ID = 2168680
DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
CATALOG_URL = "https://raw.githubusercontent.com/KopterBuzz/NOMNOM/main/manifest/manifest.json"
CACHE_LOCK = Lock()
CACHE_TTL = 1800

# The mission author and the catalog sometimes use different names for the same package.
MOD_ALIASES = {
    "alps": "nocustommaps.swissalps",
    "f99shrike": "aryx.f99",
    "mc260chimera": "aryx.mc260",
    "f16mkingviper": "aryx.f16m",
    "f22estrikeraptor": "aryx.f22e",
    "fs41eclipse": "aryx.fs41",
}


def normalized_name(value):
    return re.sub(r"[\W_]+", "", str(value or "").casefold())


@lru_cache(maxsize=1)
def mod_catalog(_time_slot):
    with urlopen(CATALOG_URL, timeout=4) as response:
        payload = json.loads(response.read(4 * 1024 * 1024))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Unexpected mod catalog format")
    return [package for package in payload if isinstance(package, dict)]


def listed_requirements(description):
    # Workshop authors can put an explicit list in the description; ordinary prose is not a dependency list.
    lines = str(description or "")[:24000].splitlines()
    heading = re.compile(r"\s*(?:\[h[1-6]\]|\[b\]|#+\s*)?(?:required mods|mod requirements|mod dependencies|dependencies)\s*:?\s*(?:\[/h[1-6]\]|\[/b\])?\s*:?\s*", re.I)
    start = next((index for index, line in enumerate(lines) if heading.fullmatch(line)), None)
    if start is None:
        return []
    requirements = []
    for line in lines[start + 1:]:
        line = re.sub(r"^(?:\[\*\]|[-*•]\s+|\d+[.)]\s+)", "", line.strip()).strip()
        if not line or line.lower() == "[list]":
            continue
        line = re.sub(r"\s+\([^()]*\)$", "", line)
        match = re.fullmatch(r"([\w][\w .&'’+/_-]{0,79}?)(?:\s+v?(\d+(?:\.\d+)+[a-z0-9-]*))?", line, re.I)
        if not match or line.endswith(".") or len(requirements) >= 24:
            break
        requirements.append((match[1].strip(), match[2]))
    return requirements


def package_link(package, version):
    owner, repo = (package.get(key) for key in ("githubOwner", "githubRepoName"))
    if not all(isinstance(part, str) and re.fullmatch(r"[\w.-]+", part, re.ASCII) and part not in (".", "..")
               for part in (owner, repo)):
        return None, None, None
    base = f"https://github.com/{owner}/{repo}/releases"
    artifacts = package.get("artifacts")
    for artifact in artifacts if isinstance(artifacts, list) else []:
        if not isinstance(artifact, dict) or not version or artifact.get("version") != version:
            continue
        url = str(artifact.get("downloadUrl", ""))
        # Link to the release notes rather than handing the browser a direct download.
        release = re.fullmatch(re.escape(base) + r"/download/([\w.+%-]+)/[^/?#]+", url, re.ASCII | re.I)
        tag = release[1] if release else ""
        link = f"{base}/tag/{tag}" if tag and "%" not in tag and tag not in (".", "..") else base
        return link, version, artifact.get("gameVersion")
    return base, None, None


def find_mod_requirements(tags, description, catalog=None):
    listed = listed_requirements(description)
    custom_map = "nocustommaps" in tags
    if not (listed or custom_map or "mod required" in tags):
        return {}
    try:
        if catalog is None:
            catalog = mod_catalog(int(monotonic() // 600)) if listed or custom_map else []
        unavailable = False
    except (OSError, ValueError, RecursionError):
        catalog, unavailable = [], True

    aliases = {}
    by_id = {}
    for package in catalog:
        package_id = str(package.get("id") or "").casefold()
        by_id[package_id] = package
        names = [package_id, package.get("githubRepoName"), *str(package.get("displayName") or "").split(" / ")]
        for name in {normalized_name(name) for name in names} - {""}:
            aliases.setdefault(name, []).append(package_id)

    if custom_map:
        listed.append(("NOCustomMaps", None))
        for tag in sorted(tags):
            for package_id in aliases.get(normalized_name(tag), []):
                package_tags = by_id[package_id].get("tags")
                if (package_id.startswith("nocustommaps.") and isinstance(package_tags, list)
                        and any(str(tag).casefold() == "map" for tag in package_tags)):
                    listed.append((by_id[package_id]["displayName"], None))

    requirements = []
    seen = set()
    for name, version in listed:
        key = normalized_name(name)
        matches = [MOD_ALIASES[key]] if key in MOD_ALIASES else aliases.get(key, [])
        package_id = matches[0] if len(matches) == 1 else key
        if package_id in seen:
            continue
        seen.add(package_id)
        entry = {"name": name, "requested_version": version}
        package = by_id.get(package_id)
        if package:
            link, matched_version, game_version = package_link(package, version)
            if (not matched_version and package_id == "nocustommaps.swissalps" and version
                    and re.fullmatch(r"[1-9]\d*\.\d+", version)):
                # The Swiss Alps author publishes the 4.1-style shorthand as 0.4.1.
                link, matched_version, game_version = package_link(package, "0." + version)
            entry.update(package=package.get("displayName") or package_id, package_id=package_id, url=link,
                         matched_version=matched_version, game_version=game_version)
        requirements.append(entry)
    return {"required": True, "requirements": requirements, "catalog_unavailable": unavailable}


def item_id(value):
    if value.lower().startswith(("steamcommunity.com/", "www.steamcommunity.com/")):
        value = "https://" + value
    if re.fullmatch(r"[0-9]{1,20}", value):
        identifier = value
    else:
        url = urlparse(value)
        if (url.scheme not in ("http", "https") or url.hostname not in ("steamcommunity.com", "www.steamcommunity.com")
                or url.username is not None or url.path.rstrip("/") not in ("/sharedfiles/filedetails", "/workshop/filedetails")):
            raise ValueError("Enter a Steam Workshop item ID or its Steam Community link.")
        identifiers = parse_qs(url.query).get("id", [])
        identifier = identifiers[0] if len(identifiers) == 1 else ""
    if not re.fullmatch(r"[0-9]{1,20}", identifier) or not 0 < int(identifier) < 2**64:
        raise ValueError("Enter a valid Steam Workshop item ID.")
    return str(int(identifier))


def fetch_items(identifiers):
    fields = {f"publishedfileids[{index}]": identifier for index, identifier in enumerate(identifiers)}
    body = urlencode({"itemcount": len(identifiers), **fields}).encode()
    try:
        with urlopen(DETAILS_URL, data=body, timeout=4) as response:
            items = json.loads(response.read(2 * 1024 * 1024))["response"]["publishedfiledetails"]
            return items if isinstance(items, list) else None
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        return None


def describe_item(details, catalog=None):
    if not isinstance(details, dict) or details.get("result") != 1:
        return {"message": "Steam did not return a public item. Check its visibility and ID."}
    if str(details.get("consumer_app_id")) != str(APP_ID):
        return {"message": "This Workshop item is for another game.", "incompatible": True}
    tags = {str(entry.get("tag", "")).casefold() for entry in details.get("tags") or [] if isinstance(entry, dict)}
    if tags & {"livery", "liveries", "skin", "skins", "mod", "mods", "collection"} and not tags & {"mission", "missions"}:
        return {"message": "Steam identifies this item as something other than a mission.", "incompatible": True}
    return {"title": str(details.get("title", "")),
            "mod_required": "mod required" in tags, "custom_map": "nocustommaps" in tags,
            "mods": find_mod_requirements(tags, details.get("description"), catalog)}


def read_metadata(path):
    try:
        if not path.is_symlink():
            with path.open("rb") as stream:
                value = json.loads(stream.read(16 * 1024 * 1024))
                if isinstance(value, dict):
                    return value
    except (OSError, ValueError, RecursionError):
        pass
    return {}


def write_metadata(path, data):
    stream = NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False)
    temporary = path.parent / stream.name
    try:
        with stream:
            json.dump(data, stream)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cached_items(data_dir, identifiers, refresh=True):
    """Keep display metadata separate from the numeric IDs in the game config."""
    path = data_dir / ".panel-workshop.json"
    with CACHE_LOCK:
        saved = read_metadata(path)
    now = time()
    wanted = list(dict.fromkeys(identifiers))
    cached = {key: saved[key] for key in wanted if isinstance(saved.get(key), dict)}
    due = [key for key in wanted if now - cached.get(key, {}).get("checked", 0) >= CACHE_TTL] if refresh else []
    updates = {}
    for offset in range(0, len(due), 24):
        group = due[offset:offset + 24]
        response = fetch_items(group)
        if response is None:
            break
        for item in response:
            if not isinstance(item, dict) or str(item.get("publishedfileid")) not in group:
                continue
            key = str(item["publishedfileid"])
            if item.get("result") != 1 or str(item.get("consumer_app_id")) != str(APP_ID):
                continue
            updates[key] = {"checked": now, "result": 1, "consumer_app_id": APP_ID,
                            "title": str(item.get("title", ""))[:250],
                            "description": str(item.get("description") or "")[:24000],
                            "tags": item["tags"][:60] if isinstance(item.get("tags"), list) else []}
    if updates:
        with CACHE_LOCK:
            saved = {key: value for key, value in read_metadata(path).items() if isinstance(value, dict)}
            saved.update(updates)
            recent = sorted(saved, key=lambda key: saved[key].get("checked", 0), reverse=True)[:256]
            try:
                write_metadata(path, {key: saved[key] for key in recent})
            except OSError:
                pass  # Steam metadata is still usable when the cache cannot be written.
        cached.update(updates)
    return cached


def item_details(identifier):
    items = fetch_items([identifier])
    if items is None:
        return {"message": "Steam could not return item details. Check the link or try again."}
    return describe_item(items[0] if items else None)


def cache_root(data_dir):
    root = (data_dir / "steamapps" / "workshop" / "content" / str(APP_ID)).resolve()
    return root if root.is_relative_to(data_dir.resolve()) and root.is_dir() else None


@lru_cache(maxsize=1024)
def valid_mission_file(file, limit, _version):
    with file.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        return False
    try:
        mission = json.loads(raw)
    except (ValueError, RecursionError):
        return False
    return isinstance(mission, dict) and isinstance(mission.get("missionSettings"), dict)


def find_mission_file(cache, limit):
    if cache.resolve() != cache or not cache.is_dir():
        return None
    for file in cache.rglob("*"):
        if (file.suffix.lower() != ".json" or file.name.lower() == "meta.json"
                or not file.is_file() or not file.resolve().is_relative_to(cache)):
            continue
        try:
            info = file.stat()
            # Steam can overwrite a file in place or replace it with a different inode.
            version = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if info.st_size <= limit and valid_mission_file(file, limit, version):
                return file
        except OSError:
            continue
    return None


def cached_mission_file(data_dir, identifier, limit):
    root = cache_root(data_dir)
    if root is None:
        return None
    cache = root / identifier
    return find_mission_file(cache, limit) if cache.parent == root else None


def downloaded_missions(data_dir, limit):
    root = cache_root(data_dir)
    if root is None:
        return {}
    missions = {}
    for folder in root.iterdir():
        try:
            if folder.name != item_id(folder.name):
                continue
            mission = find_mission_file(folder, limit)
        except (OSError, ValueError):
            continue
        if mission:
            missions[folder.name] = mission.stem
    return missions
