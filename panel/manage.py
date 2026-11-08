"""Add a dashboard and settings to Shockfront's command panel."""

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock

from flask import abort, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

import config
import mission_import
import panel_auth
import server_commands
import server_options
import workshop
from app import app, create_remote_commander

DATA_DIR = Path("/server")
CONFIG_PATH = DATA_DIR / "DedicatedServerConfig.json"
STOCK_MISSIONS = ("Altercation", "Confrontation", "Domination", "Escalation", "Terminal Control")
MISSION_GROUPS = ("BuiltIn", "User", "Workshop", "Default", "Tutorial")
CONFIG_LOCK = Lock()
LOG_TAIL_BYTES = 64 * 1024
ACTIVE_PORTS = (int(os.environ.get("NO_GAME_PORT", 7777)), int(os.environ.get("NO_QUERY_PORT", 7778)))


def read_config():
    raw = CONFIG_PATH.read_bytes()
    settings = json.loads(raw)
    if not isinstance(settings, dict):
        raise ValueError("The server configuration must be a JSON object.")
    return settings, hashlib.sha256(raw).hexdigest()


def dashboard_page():
    try:
        settings, _ = read_config()
    except (OSError, ValueError):
        settings = {}
    return render_template("dashboard.html", server_name=settings.get("ServerName"),
                           max_players=settings.get("MaxPlayers"))


app.view_functions["index"] = dashboard_page
app.config["MAX_CONTENT_LENGTH"] = mission_import.UPLOAD_LIMIT + 64 * 1024
panel_auth.install_auth(app)


@app.get("/commands")
def moderation_page():
    return render_template("moderation.html")


@app.get("/logs")
def logs_page():
    return render_template("logs.html")


@app.get("/logs/tail")
def log_tail():
    log_dir = DATA_DIR / ".panel-logs"
    chunks = []
    remaining = LOG_TAIL_BYTES
    for name in ("server.log", "server.log.1"):
        try:
            with (log_dir / name).open("rb") as stream:
                end = stream.seek(0, os.SEEK_END)
                size = min(end, remaining)
                stream.seek(end - size)
                chunks.append(stream.read(size))
        except FileNotFoundError:
            continue
        except OSError:
            abort(503, "The server log could not be read.")
        remaining -= size
        if not remaining:
            break
    raw = b"".join(reversed(chunks))
    if not remaining:
        newline = raw.find(b"\n")
        if newline >= 0:
            raw = raw[newline + 1:]
    response = jsonify(text=raw.decode("utf-8", errors="replace"), available=bool(chunks))
    response.headers["Cache-Control"] = "no-store"
    return response


def mission_directory(settings):
    return (DATA_DIR / settings.get("MissionDirectory", "missions")).resolve()


def local_missions(settings):
    directory = mission_directory(settings)
    if not directory.is_relative_to(DATA_DIR.resolve()) or not directory.is_dir():
        return []
    names = []
    for folder in directory.iterdir():
        mission = folder / f"{folder.name}.json"
        if mission.is_file() and mission.resolve().is_relative_to(directory):
            names.append(folder.name)
    return sorted(names, key=str.casefold)


def configured_workshop_items(settings):
    rotation = settings.get("MissionRotation")
    items = {}
    for entry in rotation if isinstance(rotation, list) else []:
        key = entry.get("Key") if isinstance(entry, dict) else None
        if isinstance(key, dict) and key.get("Group") == "Workshop" and isinstance(key.get("Name"), str):
            try:
                items[workshop.item_id(key["Name"].strip())] = None
            except ValueError:
                continue
    return list(items)


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
        name = workshop.item_id(name)
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
def settings_page():
    try:
        settings, revision = read_config()
        custom = local_missions(settings)
    except (OSError, ValueError, TypeError):
        abort(503, "Check that the server data folder and its JSON config are readable.")
    return render_template("settings.html", stock=STOCK_MISSIONS, custom=custom,
                           workshop_items=configured_workshop_items(settings),
                           option_sections=server_options.SECTIONS,
                           option_values=server_options.form_values(settings),
                           mission_fields=mission_import.FIELDS,
                           groups=MISSION_GROUPS, initial={
                               "name": settings.get("ServerName", ""),
                               "password_set": bool(settings.get("Password")),
                               "rotation_type": settings.get("RotationType", 0),
                               "missions": settings.get("MissionRotation", []),
                               "revision": revision,
                           })


@app.post("/settings")
def save_settings():
    data = request.get_json()
    try:
        if not isinstance(data, dict):
            raise ValueError("Expected a settings object.")
        name = data.get("name")
        rotation_type = data.get("rotation_type")
        missions = data.get("missions")
        password_mode = data.get("password_mode", "keep")
        password = data.get("password")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Enter a server name.")
        if type(rotation_type) is not int or rotation_type not in (0, 1, 2):
            raise ValueError("Choose a rotation order.")
        if not isinstance(missions, list) or not missions:
            raise ValueError("Add at least one mission to the rotation.")
        if password_mode not in ("keep", "set", "clear"):
            raise ValueError("Choose how to handle the join password.")
        if password_mode == "set" and (not isinstance(password, str) or not password.strip()
                                       or any(char in password for char in "\r\n\0")):
            raise ValueError("Enter a join password on a single line.")
        rotation = [parse_mission(mission) for mission in missions]
    except ValueError as error:
        return jsonify(message=str(error)), 400

    with CONFIG_LOCK:
        try:
            settings, revision = read_config()
            if revision != data.get("revision"):
                return jsonify(message="The config changed. Refresh this page before saving."), 409
            settings.update(ServerName=name.strip(), RotationType=rotation_type, MissionRotation=rotation)
            if password_mode != "keep":
                settings["Password"] = password if password_mode == "set" else ""
            try:
                ports = server_options.apply_options(settings, data.get("options", {}), DATA_DIR)
            except ValueError as error:
                return jsonify(message=str(error)), 400
            revision = write_config(settings)
            restart_required = ports != ACTIVE_PORTS
            if restart_required:
                message = "Saved. Run sh start.sh on the Linux host to apply the UDP ports and reload the settings."
            else:
                status, _ = server_commands.reload_config(create_remote_commander(), str(CONFIG_PATH))
                message = "Saved and reloaded. Restart the server if the game browser still shows the previous settings."
                if status != "Success":
                    message = f"Saved. Reload returned {status}. Run sh start.sh or restart the game server to apply the changes."
        except (OSError, ValueError):
            return jsonify(message="Could not write the server config. Check data folder permissions."), 503

    try:
        local = local_missions(settings)
    except OSError:
        local = []
    return jsonify(message=message, revision=revision, password_set=bool(settings.get("Password")),
                   local_missions=local, workshop_items=configured_workshop_items(settings))


def workshop_status(settings, identifier):
    if identifier not in configured_workshop_items(settings):
        return "unsaved", "Add this item to the rotation to save it and start the Steam download."
    if server_options.effective_ports(settings) != ACTIVE_PORTS:
        return "restart", "Run sh start.sh on the Linux host to apply the saved settings and start the Steam download."
    if workshop.has_mission_file(DATA_DIR, identifier, mission_import.UPLOAD_LIMIT):
        return "cached", "Mission file found in the server’s Workshop cache. You can queue it for the next round."
    return "waiting", "Waiting for Steam to download a mission file. See Server logs if it does not appear."


@app.get("/settings/workshop")
def check_workshop():
    try:
        identifier = workshop.item_id(request.args.get("item", "").strip())
    except ValueError as error:
        return jsonify(message=str(error)), 400
    try:
        settings, _ = read_config()
        state, message = workshop_status(settings, identifier)
    except (OSError, ValueError, TypeError):
        return jsonify(message="Could not read the Workshop state. Check the server configuration."), 503
    details = workshop.item_details(identifier) if request.args.get("details") == "1" else {}
    response = jsonify(id=identifier, state=state, message=message, details=details)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/settings/workshop")
def add_workshop():
    data = request.get_json()
    try:
        if not isinstance(data, dict):
            raise ValueError("Choose a Steam Workshop mission.")
        mission = parse_mission(data.get("mission"))
        if mission["Key"]["Group"] != "Workshop":
            raise ValueError("Choose a Steam Workshop mission.")
    except ValueError as error:
        return jsonify(message=str(error)), 400

    identifier = mission["Key"]["Name"]
    with CONFIG_LOCK:
        try:
            settings, revision = read_config()
            if revision != data.get("revision"):
                return jsonify(message="The saved configuration changed. Refresh the page before adding this item."), 409
            rotation = settings.get("MissionRotation", [])
            if not isinstance(rotation, list):
                return jsonify(message="The saved mission rotation is invalid. Fix it before adding this item."), 409
            ports = server_options.effective_ports(settings)
            added = identifier not in configured_workshop_items(settings)
            if added:
                settings["MissionRotation"] = [*rotation, mission]
                revision = write_config(settings)
            state, message = workshop_status(settings, identifier)
            if ports == ACTIVE_PORTS and (added or state == "waiting"):
                status, _ = server_commands.reload_config(create_remote_commander(), str(CONFIG_PATH))
                if status != "Success":
                    state = "reload_failed"
                    message = f"The server could not reload its configuration ({status}). Try again or restart the server."
        except (OSError, ValueError, TypeError):
            return jsonify(message="Could not update the Workshop rotation. Check the saved configuration."), 503
    prefix = "Saved to the mission rotation." if added else "Already saved in the mission rotation."
    return jsonify(mission=mission, revision=revision, workshop_items=configured_workshop_items(settings),
                   state=state, message=f"{prefix} {message}")


@app.post("/settings/import")
def import_mission():
    # The upload uses multipart data, which cross-origin forms can otherwise submit.
    if request.headers.get("X-Mission-Upload") != "1":
        abort(403)
    try:
        upload = request.files.get("mission")
        if not upload:
            raise ValueError("Choose a mission JSON or ZIP up to 16 MiB.")
        with mission_import.read_upload(upload) as (source, mission, archive, members):
            if "name" not in request.form:
                return jsonify(name=source, fields=mission_import.editable_values(mission))
            name = request.form["name"]
            edits = json.loads(request.form.get("edits", "{}"))
            with CONFIG_LOCK:
                settings, _ = read_config()
                directory = mission_directory(settings)
                if directory == DATA_DIR.resolve() or not directory.is_relative_to(DATA_DIR.resolve()):
                    return jsonify(message="Set MissionDirectory to a subfolder under /server before importing."), 409
                mission_import.save_copy(directory, name, mission, edits, archive, members)
    except RequestEntityTooLarge:
        return jsonify(message="Choose a mission JSON or ZIP up to 16 MiB."), 413
    except FileExistsError as error:
        return jsonify(message=str(error)), 409
    except (ValueError, RecursionError) as error:
        return jsonify(message=str(error)), 400
    except (OSError, TypeError):
        return jsonify(message="Could not save the mission. Check the mission folder permissions."), 503
    return jsonify(name=name, message=f"Saved {name} as a local mission."), 201


@app.post("/settings/next")
def queue_mission():
    try:
        mission = parse_mission(request.get_json())
    except ValueError as error:
        return jsonify(message=str(error)), 400
    key = mission["Key"]
    if key["Group"] in ("User", "Workshop"):
        try:
            settings, _ = read_config()
            if key["Group"] == "User" and key["Name"] not in local_missions(settings):
                return jsonify(message="The local mission is missing. Import it or check the saved mission directory."), 409
            if key["Group"] == "User" and server_options.effective_ports(settings) != ACTIVE_PORTS:
                return jsonify(message="Run sh start.sh on the Linux host to apply the saved settings before queueing."), 409
            if key["Group"] == "Workshop":
                state, message = workshop_status(settings, key["Name"])
                if state != "cached":
                    return jsonify(message=message), 409
        except (OSError, ValueError, TypeError):
            return jsonify(message="Could not verify the mission in the server data folder."), 503
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
