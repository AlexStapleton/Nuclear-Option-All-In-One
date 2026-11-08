"""Look up public display names without using the name as a player identifier."""

import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from time import monotonic
from urllib.request import urlopen
from xml.etree import ElementTree


PROFILE_ID = re.compile(r"[0-9]{17}")
LOOKUPS = ThreadPoolExecutor(max_workers=8)


@lru_cache(maxsize=2048)
def display_name(identifier, _time_slot):
    try:
        url = f"https://steamcommunity.com/profiles/{identifier}?xml=1"
        with urlopen(url, timeout=3) as response:
            raw = response.read(128 * 1024)
        if b"<!DOCTYPE" in raw or b"<!ENTITY" in raw:
            return None
        profile = ElementTree.fromstring(raw)
        if profile.tag == "profile" and profile.findtext("steamID64") == identifier:
            return (profile.findtext("steamID") or "").strip()[:128] or None
    except (OSError, ValueError, ElementTree.ParseError):
        pass
    return None


def names_for(identifiers):
    slot = int(monotonic() // 300)
    keys = list(dict.fromkeys(key for key in identifiers if PROFILE_ID.fullmatch(key)))[:64]
    names = LOOKUPS.map(lambda key: display_name(key, slot), keys)
    return {key: name for key, name in zip(keys, names) if name}
