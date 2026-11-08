"""Documented game settings shared by the panel and the Compose launcher."""

import json
import math
import sys
from pathlib import Path

DEFAULT_PORTS = {"Port": 7777, "QueryPort": 7778}
SECTIONS = {
    "Server and timing": {
        "MaxPlayers": ("Maximum players", "integer", 16, 1, None),
        "Hidden": ("Hide from the public server list", "checkbox", False, None, None),
        "ModdedServer": ("Mark as a modded server", "checkbox", False, None, None),
        "NoPlayerStopTime": ("Unload after last player leaves (seconds)", "number", 30, 0, None),
        "PostMissionDelay": ("Delay between missions (seconds)", "number", 30, 0, None),
        "MissionDirectory": ("Custom mission directory", "text", "/server/missions", None, None),
    },
    "UDP ports": {
        "Port": ("Game port override (default 7777)", "port", None, 1, 65535),
        "QueryPort": ("Steam query port override (default 7778)", "port", None, 1, 65535),
    },
    "Moderation and access lists": {
        "BanListPaths": ("Ban list paths (one per line)", "lines", [], None, None),
        "DisableErrorKick": ("Disable automatic kicks for player errors", "checkbox", False, None, None),
        "ErrorKickImmuneListPaths": ("Error kick exemption lists (one per line)", "lines", [], None, None),
    },
    "Vote kick": {
        "VoteKick.Enabled": ("Enable vote kick", "checkbox", True, None, None),
        "VoteKick.PassRatio": ("Share of eligible votes to pass (0 to 1)", "number", 0.6, 0, 1),
        "VoteKick.MinVotes": ("Minimum votes", "integer", 3, 1, None),
        "VoteKick.AutoBanThreshold": ("Successful vote kicks before auto-ban", "integer", 3, 0, None),
        "VoteKick.VoteDuration": ("Voting duration (seconds)", "number", 45, 0, None),
        "VoteKick.ResolutionDisplayTime": ("Show vote result for (seconds)", "number", 20, 0, None),
        "VoteKick.NewVoteLockout": ("Wait before another vote (seconds)", "number", 10, 0, None),
        "VoteKick.RequesterCooldown": ("Vote requester cooldown (seconds)", "number", 300, 0, None),
    },
}
FIELDS = {key: field for section in SECTIONS.values() for key, field in section.items()}


def get_value(config, key, default):
    parent, dot, child = key.partition(".")
    value = config.get(parent, default)
    return (value.get(child, default) if isinstance(value, dict) else default) if dot else value


def form_values(config):
    values = {}
    for key, (_, kind, default, _, _) in FIELDS.items():
        value = get_value(config, key, default)
        if kind == "port":
            value = value.get("Value") if isinstance(value, dict) and value.get("IsOverride") else None
        if kind == "lines":
            value = "\n".join(value) if isinstance(value, list) and all(isinstance(line, str) for line in value) else ""
        values[key] = value
    return values


def effective_ports(config):
    ports = []
    for key, default in DEFAULT_PORTS.items():
        setting = config.get(key)
        if isinstance(setting, dict) and setting.get("IsOverride"):
            value = setting.get("Value")
            if type(value) is not int or not 1 <= value <= 65535:
                raise ValueError(f"{key} must be an integer from 1 to 65535.")
            ports.append(value)
        else:
            ports.append(default)
    if ports[0] == ports[1]:
        raise ValueError("The game and Steam query ports must be different.")
    return tuple(ports)


def apply_options(config, edits, data_dir):
    if not isinstance(edits, dict) or edits.keys() - FIELDS.keys():
        raise ValueError("The request contains an unknown server option.")
    for key, value in edits.items():
        label, kind, _, minimum, maximum = FIELDS[key]
        if kind == "port" and value is None:
            override = config.get(key)
            config[key] = {**(override if isinstance(override, dict) else {}), "IsOverride": False, "Value": 0}
            continue
        if kind in ("integer", "number", "port"):
            types = (int, float) if kind == "number" else (int,)
            if (type(value) not in types or not -math.inf < value < math.inf
                    or value < minimum or (maximum is not None and value > maximum)):
                raise ValueError(f"Enter a valid value for {label.lower()}.")
        if kind == "checkbox" and type(value) is not bool:
            raise ValueError(f"Choose whether to {label.lower()}.")
        if kind == "lines":
            if not isinstance(value, str) or "\0" in value:
                raise ValueError(f"Enter valid {label.lower()}.")
            value = [line.strip() for line in value.splitlines() if line.strip()]
        if kind == "text":
            if not isinstance(value, str) or not value.strip() or any(char in value for char in "\0\r\n"):
                raise ValueError("Enter an absolute mission directory under /server.")
            value = value.strip()
            path = Path(value)
            resolved = path.resolve()
            root = data_dir.resolve()
            if (not path.is_absolute() or resolved == root or not resolved.is_relative_to(root)
                    or (resolved.exists() and not resolved.is_dir())):
                raise ValueError("Enter a mission directory that is a subfolder of /server.")
        parent, dot, child = key.partition(".")
        if dot:
            existing = config.get(parent)
            config[parent] = {**(existing if isinstance(existing, dict) else {}), child: value}
        elif kind == "port":
            existing = config.get(key)
            config[key] = {**(existing if isinstance(existing, dict) else {}), "IsOverride": True, "Value": value}
        else:
            config[key] = value
    return effective_ports(config)


if __name__ == "__main__":
    try:
        with open(sys.argv[1], encoding="utf-8-sig") as stream:
            settings = json.load(stream)
        if not isinstance(settings, dict):
            raise ValueError("Expected a JSON object.")
        print(*effective_ports(settings))
    except (OSError, ValueError) as error:
        sys.exit(f"Could not read game ports from DedicatedServerConfig.json: {error}")
