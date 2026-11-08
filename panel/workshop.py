"""Workshop item identifiers, metadata, and the dedicated server's local cache."""

import json
import re
from functools import lru_cache
from time import monotonic
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

APP_ID = 2168680
DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
CATALOG_URL = "https://raw.githubusercontent.com/KopterBuzz/NOMNOM/main/manifest/manifest.json"

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


def find_mod_requirements(tags, description):
    listed = listed_requirements(description)
    custom_map = "nocustommaps" in tags
    if not (listed or custom_map or "mod required" in tags):
        return {}
    try:
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
            entry.update(package=package.get("displayName") or package_id, url=link,
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


def item_details(identifier):
    body = urlencode({"itemcount": 1, "publishedfileids[0]": identifier}).encode()
    try:
        with urlopen(DETAILS_URL, data=body, timeout=4) as response:
            details = json.loads(response.read(512 * 1024))["response"]["publishedfiledetails"][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return {"message": "Steam could not return item details. Check the link or try again."}
    if not isinstance(details, dict) or details.get("result") != 1:
        return {"message": "Steam did not return a public item. Check its visibility and ID."}
    if str(details.get("consumer_app_id")) != str(APP_ID):
        return {"message": "This Workshop item is for another game.", "incompatible": True}
    tags = {str(entry.get("tag", "")).casefold() for entry in details.get("tags") or [] if isinstance(entry, dict)}
    if tags & {"livery", "liveries", "skin", "skins", "mod", "mods", "collection"} and not tags & {"mission", "missions"}:
        return {"message": "Steam identifies this item as something other than a mission.", "incompatible": True}
    return {"title": str(details.get("title", "")),
            "mod_required": "mod required" in tags, "custom_map": "nocustommaps" in tags,
            "mods": find_mod_requirements(tags, details.get("description"))}


def has_mission_file(data_dir, identifier, limit):
    cache = data_dir / "steamapps" / "workshop" / "content" / str(APP_ID) / identifier
    cache = cache.resolve()
    if not cache.is_relative_to(data_dir.resolve()) or not cache.is_dir():
        return False
    for file in cache.rglob("*"):
        if (file.suffix.lower() != ".json" or file.name.lower() == "meta.json"
                or not file.is_file() or not file.resolve().is_relative_to(cache)):
            continue
        try:
            with file.open("rb") as stream:
                raw = stream.read(limit + 1)
            if len(raw) <= limit:
                mission = json.loads(raw)
                if isinstance(mission, dict) and isinstance(mission.get("missionSettings"), dict):
                    return True
        except (OSError, ValueError, RecursionError):
            continue
    return False
