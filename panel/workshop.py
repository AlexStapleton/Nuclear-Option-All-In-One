"""Workshop item identifiers, metadata, and the dedicated server's local cache."""

import json
import re
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import urlopen

APP_ID = 2168680
DETAILS_URL = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"


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
            "mod_required": "mod required" in tags, "custom_map": "nocustommaps" in tags}


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
