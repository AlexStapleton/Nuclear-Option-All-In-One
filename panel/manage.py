"""Add settings and mission selection to Shockfront's command panel."""

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from urllib.parse import parse_qs, urlparse

from flask import abort, jsonify, render_template, request

import config
import server_commands
from app import app, create_remote_commander, requires_auth

DATA_DIR = Path("/server")
CONFIG_PATH = DATA_DIR / "DedicatedServerConfig.json"
STOCK_MISSIONS = ("Altercation", "Confrontation", "Domination", "Escalation", "Terminal Control")
MISSION_GROUPS = ("BuiltIn", "User", "Workshop", "Default", "Tutorial")
CONFIG_LOCK = Lock()


def read_config():
    raw = CONFIG_PATH.read_bytes()
    settings = json.loads(raw)
    if not isinstance(settings, dict):
        raise ValueError("The server configuration must be a JSON object.")
    return settings, hashlib.sha256(raw).hexdigest()


def local_missions(settings):
    directory = (DATA_DIR / settings.get("MissionDirectory", "missions")).resolve()
    if not directory.is_relative_to(DATA_DIR) or not directory.is_dir():
        return []
    names = []
    for folder in directory.iterdir():
        mission = folder / f"{folder.name}.json"
        if mission.is_file() and mission.resolve().is_relative_to(directory):
            names.append(folder.name)
    return sorted(names, key=str.casefold)


def parse_mission(item):
    if not isinstance(item, dict) or not isinstance(item.get("Key"), dict):
        raise ValueError("Each mission needs a group, name, and time limit.")
    group, name = (item["Key"].get(key) for key in ("Group", "Name"))
    seconds = item.get("MaxTime", 7200)
    if group not in MISSION_GROUPS or not isinstance(name, str) or not name.strip():
        raise ValueError("Choose a mission group and enter its name or Workshop ID.")
    if type(seconds) not in (int, float) or not 0 < seconds < math.inf:
        raise ValueError("Each mission needs a positive time limit in seconds.")
    name = name.strip()
    if group == "Workshop":
        if not re.fullmatch(r"[0-9]+", name):
            url = urlparse(name)
            if (url.scheme in ("http", "https") and url.hostname == "steamcommunity.com"
                    and url.path.rstrip("/") in ("/sharedfiles/filedetails", "/workshop/filedetails")):
                name = parse_qs(url.query).get("id", [""])[0]
        if not re.fullmatch(r"[0-9]+", name) or not name.strip("0"):
            raise ValueError("Enter a Steam Workshop item ID or its Steam Community link.")
    return {**item, "Key": {**item["Key"], "Group": group, "Name": name}, "MaxTime": seconds}


def write_config(settings):
    mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
    raw = (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with NamedTemporaryFile(dir=DATA_DIR, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(raw)
            file.close()
            temporary.chmod(mode)
            temporary.replace(CONFIG_PATH)
        finally:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


@app.get("/settings")
@requires_auth
def settings_page():
    try:
        settings, revision = read_config()
        custom = local_missions(settings)
    except (OSError, ValueError, TypeError):
        abort(503, "Check that the server data folder and its JSON config are readable.")
    return render_template("settings.html", stock=STOCK_MISSIONS, custom=custom,
                           groups=MISSION_GROUPS, initial={
                               "name": settings.get("ServerName", ""),
                               "rotation_type": settings.get("RotationType", 0),
                               "missions": settings.get("MissionRotation", []),
                               "revision": revision,
                           })


@app.post("/settings")
@requires_auth
def save_settings():
    data = request.get_json()
    try:
        if not isinstance(data, dict):
            raise ValueError("Expected a settings object.")
        name = data.get("name")
        rotation_type = data.get("rotation_type")
        missions = data.get("missions")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Enter a server name.")
        if type(rotation_type) is not int or rotation_type not in (0, 1, 2):
            raise ValueError("Choose a rotation order.")
        if not isinstance(missions, list) or not missions:
            raise ValueError("Add at least one mission to the rotation.")
        rotation = [parse_mission(mission) for mission in missions]
    except ValueError as error:
        return jsonify(message=str(error)), 400

    with CONFIG_LOCK:
        try:
            settings, revision = read_config()
            if revision != data.get("revision"):
                return jsonify(message="The config changed. Refresh this page before saving."), 409
            settings.update(ServerName=name.strip(), RotationType=rotation_type, MissionRotation=rotation)
            revision = write_config(settings)
        except (OSError, ValueError):
            return jsonify(message="Could not write the server config. Check data folder permissions."), 503

    status, _ = server_commands.reload_config(create_remote_commander())
    message = "Saved and reloaded. If the server list shows the old name, restart the server."
    if status != "Success":
        message = f"Saved. Reload returned {status}. Restart the server to apply the changes."
    return jsonify(message=message, revision=revision)


@app.post("/settings/next")
@requires_auth
def queue_mission():
    try:
        mission = parse_mission(request.get_json())
    except ValueError as error:
        return jsonify(message=str(error)), 400
    key = mission["Key"]
    status, _ = server_commands.set_next_mission(
        create_remote_commander(), key["Group"], key["Name"], mission["MaxTime"],
    )
    if status != "Success":
        return jsonify(message=f"Could not queue the mission. The server returned {status}."), 502
    return jsonify(message=f"Queued {key['Name']} for after the current mission.")


if __name__ == "__main__":
    # The launcher creates the data directory as the operator. Match its owner
    # so the panel can save config files on bind mounts owned by different UIDs.
    if os.geteuid() == 0:
        owner = DATA_DIR.stat()
        os.setgroups([])
        os.setgid(owner.st_gid)
        os.setuid(owner.st_uid)
    ssl_context = None
    if config.SSL_CERT_PATH and config.SSL_KEY_PATH:
        ssl_context = (config.SSL_CERT_PATH, config.SSL_KEY_PATH)
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, ssl_context=ssl_context)
