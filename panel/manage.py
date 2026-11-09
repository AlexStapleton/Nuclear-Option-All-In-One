"""Add a dashboard and settings to Shockfront's command panel."""

import hashlib
import json
import math
import os
import re
import socket
import stat
import struct
from functools import wraps
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from time import monotonic, sleep
from uuid import uuid4
from zipfile import BadZipFile

from flask import abort, jsonify, make_response, render_template, request, url_for
from werkzeug.exceptions import RequestEntityTooLarge

import config
import after_action
import launch_settings
import mission_import
import mod_inventory
import panel_auth
import server_commands
import server_mods
import server_options
import steam_profiles
import workshop
from app import app, create_remote_commander

DATA_DIR = Path("/server")
CONFIG_PATH = DATA_DIR / "DedicatedServerConfig.json"
STOCK_MISSIONS = ("Altercation", "Confrontation", "Domination", "Escalation", "Terminal Control")
MISSION_GROUPS = ("BuiltIn", "User", "Workshop", "Default", "Tutorial")
CONFIG_LOCK = Lock()
MISSION_LOCK = Lock()
MOD_LOCK = Lock()
KICK_LOCK = Lock()
RESTART_LOCK = Lock()
STATUS_COMMANDS = ("get-mission", "get-mission-time", "get-player-list")
STATUS_LOCKS = {name: Lock() for name in STATUS_COMMANDS}
status_cache = {}
KICK_COMMANDS = {"kick_player", "unkick_player", "clear_kicked_players"}
STEAM_ID = re.compile(r"[0-9]{1,20}")
BAN_ENTRY = re.compile(r"^\s*([0-9]{1,20})(?=$|[\s,;|:])")
mod_plan = None
mod_job = {"running": False, "message": ""}
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
panel_auth.install_auth(app, DATA_DIR)


class StatusCommander(server_commands.RemoteCommander):
    def send_command(self, name):
        self.deadline = monotonic() + 1.5
        payload = json.dumps({"name": name, "arguments": []}).encode()
        try:
            with socket.create_connection((self.host, self.port), timeout=1.5) as sock:
                self.set_deadline(sock)
                sock.sendall(struct.pack("<i", len(payload)) + payload)
                return self._receive_response(sock)
        except OSError:
            return "NetworkError", None

    def set_deadline(self, sock):
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Game status request exceeded its deadline.")
        sock.settimeout(remaining)

    def _recv_n(self, sock, size):
        if size > 2 * 1024 * 1024:
            raise OSError("Game status response exceeds the size limit.")
        data = bytearray()
        while len(data) < size:
            self.set_deadline(sock)
            chunk = sock.recv(min(size - len(data), 65536))
            if not chunk:
                raise ConnectionResetError("Game closed the status connection.")
            data.extend(chunk)
        return data


def read_game_status(name):
    with STATUS_LOCKS[name]:
        instance = game_instance()
        cached = status_cache.get(name)
        if cached and cached[0] == instance and cached[1] > monotonic():
            return cached[2]
        commander = StatusCommander("127.0.0.1", config.SERVER_PORTS[0])
        result = commander.send_command(name)
        status_cache[name] = (instance, monotonic() + 3, result)
        return result


@app.get("/status/<name>")
def game_status(name):
    if name not in STATUS_COMMANDS:
        abort(404)
    code, result = read_game_status(name)
    return jsonify(status_code=code, response=result)


@app.get("/commands")
def moderation_page():
    return render_template("moderation.html")


def game_instance():
    marker = DATA_DIR / ".panel-logs" / "game-instance"
    try:
        with marker.open(encoding="ascii") as stream:
            return stream.read(64).strip()
    except (OSError, ValueError):
        return ""


def valid_steam_id(value):
    return isinstance(value, str) and STEAM_ID.fullmatch(value) and 0 < int(value) < 2**64


def tracked_kicks(instance):
    record = workshop.read_metadata(DATA_DIR / ".panel-kicks.json")
    ids = record.get("ids")
    return set(filter(valid_steam_id, ids)) if instance and record.get("instance") == instance and isinstance(ids, list) else set()


def track_kick_command(view):
    @wraps(view)
    def run():
        # Keep the recorded order consistent with the actual server commands.
        with KICK_LOCK:
            instance = game_instance()
            response = app.make_response(view())
            result = response.get_json(silent=True)
            if (instance and instance == game_instance() and 200 <= response.status_code < 300
                    and isinstance(result, dict) and result.get("status_code") == "Success"):
                save_kick_result(instance)
            return response
    return run


def save_kick_result(instance):
    command = request.endpoint
    steam_id = (request.get_json(silent=True) or {}).get("steam_id")
    if command != "clear_kicked_players" and not valid_steam_id(steam_id):
        return
    try:
        ids = tracked_kicks(instance)
        if command == "kick_player":
            ids.add(str(int(steam_id)))
        elif command == "unkick_player":
            ids.discard(str(int(steam_id)))
        else:
            ids.clear()
        workshop.write_metadata(DATA_DIR / ".panel-kicks.json", {"instance": instance, "ids": sorted(ids)})
    except OSError:
        app.logger.exception("Could not save the panel kick list.")


for endpoint in KICK_COMMANDS:
    app.view_functions[endpoint] = track_kick_command(app.view_functions[endpoint])


@app.get("/moderation/access")
def moderation_access():
    bans = set()
    incomplete = False
    root = DATA_DIR.resolve()
    try:
        settings, _ = read_config()
        paths = settings.get("BanListPaths", ["ban_list.txt"])
        if not isinstance(paths, list):
            raise ValueError("BanListPaths must be a list.")
        for name in paths:
            try:
                path = (root / name).resolve()
                if not path.is_relative_to(root):
                    incomplete = True
                    continue
                if not stat.S_ISREG(path.stat().st_mode):
                    incomplete = True
                    continue
                with path.open(encoding="utf-8-sig", errors="replace") as stream:
                    lines = stream.read(256 * 1024 + 1)
                if len(lines) > 256 * 1024:
                    incomplete = True
                    lines = lines[:256 * 1024].rpartition("\n")[0]
                for line in lines.splitlines():
                    match = BAN_ENTRY.match(line)
                    if match and valid_steam_id(match[1]):
                        bans.add(str(int(match[1])))
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, RuntimeError):
                incomplete = True
    except (OSError, ValueError):
        incomplete = True
    instance = game_instance()
    return jsonify(bans=sorted(bans), bans_incomplete=incomplete,
                   kicks=sorted(tracked_kicks(instance)), kick_tracking=bool(instance))


@app.get("/logs")
def logs_page():
    return render_template("logs.html")


@app.get("/reports")
def reports_page():
    root = DATA_DIR / ".panel-after-action"
    reports = after_action.recent_reports(root / "reports")
    selected = request.args.get("report")
    if selected is None:
        report = reports[0] if reports else None
    else:
        report = next((item for item in reports if item["id"] == selected), None)
        if report is None:
            abort(404)
    column = request.args.get("sort", "total")
    if column not in after_action.SORT_OPTIONS:
        column = "total"
    players = sorted(report["players"], key=lambda player: after_action.player_order(player, column)) if report else []
    highlights, timeline = after_action.summarize(report) if report else ([], [])
    return render_template("reports.html", reports=reports, report=report, players=players,
                           sort_options=after_action.SORT_OPTIONS, counters=after_action.COUNTERS,
                           column=column, enabled=(root / "enabled").is_file(), highlights=highlights, timeline=timeline)


@app.get("/reports/live")
def reports_live():
    root = DATA_DIR / ".panel-after-action"
    directory = root / "reports"
    enabled = (root / "enabled").is_file()
    report = after_action.live_report(directory, game_instance()) if enabled else None
    if report:
        report["players"].sort(key=lambda player: after_action.player_order(player, "total"))
    previous = request.args.get("previous")
    finished = url_for("reports_page", report=previous) if after_action.is_finished(directory, previous) else None
    return jsonify(report=report, enabled=enabled, finished=finished)


@app.get("/logs/tail")
def log_tail():
    source = request.args.get("source", "game")
    if source == "game":
        log_dir = DATA_DIR / ".panel-logs"
        paths = [log_dir / "server.log", *(log_dir / f"server.log.{index}" for index in range(1, 10))]
    elif source == "mods":
        paths = []
        try:
            selected = server_mods.selected_set(DATA_DIR)
            if selected:
                profile = server_mods.mod_root(DATA_DIR) / selected["id"]
                path = (profile / "BepInEx" / "LogOutput.log").resolve()
                if path.is_relative_to(profile) and path.is_file():
                    paths.append(path)
        except (OSError, ValueError, RuntimeError):
            abort(503, "The mod loader log could not be read.")
    else:
        abort(400, "Unknown log source.")
    candidates = []
    total = 0
    for path in paths:
        try:
            info = path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            abort(503, "The server log could not be read.")
        if not stat.S_ISREG(info.st_mode):
            abort(503, "The server log could not be read.")
        candidates.append((path, info))
        total += info.st_size
        if total >= LOG_TAIL_BYTES:
            break
    versions = [(path.name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                for path, info in candidates]
    etag = hashlib.sha256(repr((source, versions)).encode()).hexdigest()
    if request.if_none_match.contains_weak(etag):
        response = make_response("", 304)
        response.set_etag(etag, weak=True)
        return response
    chunks = []
    remaining = LOG_TAIL_BYTES
    for path, _ in candidates:
        try:
            with path.open("rb") as stream:
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
    response.set_etag(etag, weak=True)
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


@app.get("/workshop/titles")
def workshop_titles():
    return jsonify(titles=resolve_workshop_titles(request.args.getlist("id")[:96]))


def resolve_workshop_titles(keys, refresh=True):
    identifiers = {}
    for key in keys:
        try:
            identifiers[key] = workshop.item_id(key)
        except ValueError:
            continue
    items = workshop.cached_items(DATA_DIR, identifiers.values(), refresh=refresh)
    titles = {key: items[value]["title"] for key, value in identifiers.items()
              if value in items and items[value].get("title")}
    try:
        selected = server_mods.selected_set(DATA_DIR)
        if selected:
            for key, identifier in identifiers.items():
                if identifier == selected.get("item") and selected.get("title"):
                    titles.setdefault(key, selected["title"])
    except (OSError, ValueError):
        pass
    return titles


@app.get("/players/names")
def player_names():
    return jsonify(names=steam_profiles.names_for(request.args.getlist("id")[:64]))


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


@app.get("/settings/launch")
def launch_status():
    try:
        saved = launch_settings.preferences(DATA_DIR)
        active, sample = launch_settings.launcher_status(DATA_DIR)
        restart = launch_settings.restart_status(DATA_DIR, game_instance())
        if request.args.get("sample") == "1":
            return jsonify(saved=saved, active=active, sample=sample, restart=restart)
        settings, _ = read_config()
        profiles = launch_settings.load_profiles(DATA_DIR)
        plugin = launch_settings.performance_options(DATA_DIR)
        entries = [{"name": name, "server_name": snapshot.get("ServerName", ""),
                    "matches": profile_config(snapshot, settings) == settings}
                   for name, snapshot in sorted(profiles.items(), key=lambda item: item[0].casefold())]
        return jsonify(saved=saved, active=active, sample=sample, restart=restart, plugin=plugin, profiles=entries)
    except (OSError, ValueError, RuntimeError):
        return jsonify(message="Could not read launch settings. Check the panel files in the server data directory."), 503


@app.get("/settings/performance-history")
def launch_history():
    try:
        return jsonify(samples=list(reversed(launch_settings.performance_history(DATA_DIR))))
    except (OSError, ValueError):
        return jsonify(message="Could not read performance history."), 503


def record_performance_sample(previous=None):
    _, sample = launch_settings.launcher_status(DATA_DIR)
    if not sample:
        return previous
    identity = sample["instance"], sample["timestamp"]
    if identity == previous:
        return previous
    records = launch_settings.performance_history(DATA_DIR)
    if records and all(records[-1].get(key) == sample[key] for key in ("instance", "timestamp")):
        return identity
    mission_status, missions = read_game_status("get-mission")
    time_status, timing = read_game_status("get-mission-time")
    mission = (missions or {}).get("currentMission") if mission_status == "Success" else None
    elapsed = (timing or {}).get("currentTime") if time_status == "Success" else None
    key = (mission or {}).get("Key") or {}
    # Only label a sample when its full window fits within the current mission.
    same_mission = (type(elapsed) in (int, float) and elapsed >= sample["age"] + sample["seconds"] + 5
                    and game_instance() == sample["instance"])
    name = key.get("Name") if same_mission else None
    if isinstance(name, str) and key.get("Group") == "Workshop":
        name = resolve_workshop_titles([name], refresh=False).get(name, name)
    sample["mission"] = name[:240] if isinstance(name, str) else None
    records.append(sample)
    launch_settings.private_write(DATA_DIR / launch_settings.HISTORY,
                                  json.dumps(records[-launch_settings.HISTORY_LIMIT:]))
    return identity


def monitor_performance():
    previous = None
    while True:
        try:
            previous = record_performance_sample(previous)
        except (OSError, ValueError, RuntimeError, AttributeError):
            app.logger.warning("Could not save the latest performance sample.", exc_info=True)
        sleep(5)


def profile_config(snapshot, current):
    # The Docker UDP mapping belongs to this instance across all profiles.
    config = dict(snapshot)
    for key in server_options.DEFAULT_PORTS:
        if key in current:
            config[key] = current[key]
        else:
            config.pop(key, None)
    return config


@app.post("/settings/launch")
def save_launch():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(message="Choose a launch setting."), 400
    action = data.get("action")
    try:
        if action == "preferences":
            launch_settings.save_preferences(DATA_DIR, data)
            message = "Launch settings saved. Restart the game to apply them."
        elif action == "restart":
            mode = data.get("mode")
            if mode not in ("current", "update"):
                raise ValueError("Choose a restart mode.")
            with RESTART_LOCK, MOD_LOCK, CONFIG_LOCK:
                instance = game_instance()
                restart = launch_settings.restart_status(DATA_DIR, instance)
                if restart.get("pending") or mod_job["running"]:
                    return jsonify(message="A restart or mod installation is already in progress."), 409
                if not restart["available"] or not launch_settings.INSTANCE.fullmatch(instance):
                    return jsonify(message="Restart control is unavailable. Start the updated launcher first."), 409
                settings, _ = read_config()
                if server_options.effective_ports(settings) != ACTIVE_PORTS:
                    return jsonify(message="Run sh start.sh on the Linux host to apply the saved UDP ports."), 409
                launch_settings.private_write(DATA_DIR / launch_settings.RESTART_REQUEST,
                                              f"{uuid4().hex} {mode} {instance}\n")
            return jsonify(message="Restart requested."), 202
        elif action == "performance":
            with MOD_LOCK:
                if mod_job["running"]:
                    return jsonify(message="Wait for the mod installation to finish."), 409
                launch_settings.save_performance(DATA_DIR, data.get("workers"), data.get("timing"))
            message = "NukeStats settings saved. Restart the game to apply them."
        elif action in ("save-profile", "apply-profile", "delete-profile"):
            name = data.get("name")
            if not isinstance(name, str) or not launch_settings.PROFILE_NAME.fullmatch(name.strip()):
                raise ValueError("Enter a profile name from 1 to 60 characters.")
            name = name.strip()
            with CONFIG_LOCK:
                profiles = launch_settings.load_profiles(DATA_DIR)
                if action == "save-profile":
                    if any(name.casefold() == key.casefold() for key in profiles):
                        return jsonify(message="A startup profile already has that name."), 409
                    if len(profiles) >= 32:
                        raise ValueError("Delete a startup profile before saving a new one (maximum 32).")
                    profiles[name], _ = read_config()
                    launch_settings.save_profiles(DATA_DIR, profiles)
                    message = f"Saved startup profile {name}."
                elif name not in profiles:
                    return jsonify(message="That startup profile no longer exists."), 404
                elif action == "delete-profile":
                    del profiles[name]
                    launch_settings.save_profiles(DATA_DIR, profiles)
                    message = f"Deleted startup profile {name}."
                else:
                    current, _ = read_config()
                    write_config(profile_config(profiles[name], current))
                    message = f"Restored {name} to the saved config. Restart the game to apply it."
            return jsonify(message=message, reload=action == "apply-profile")
        else:
            raise ValueError("Choose a launch setting.")
    except ValueError as error:
        return jsonify(message=str(error)), 400
    except (OSError, RuntimeError):
        return jsonify(message="Could not write launch settings. Check data folder permissions."), 503
    return jsonify(message=message)


@app.get("/settings")
def settings_page():
    try:
        settings, revision = read_config()
        custom = local_missions(settings)
    except (OSError, ValueError, TypeError):
        abort(503, "Check that the server data folder and its JSON config are readable.")
    items = configured_workshop_items(settings)
    return render_template("settings.html", stock=STOCK_MISSIONS, custom=custom,
                           workshop_items=items,
                           option_sections=server_options.SECTIONS,
                           option_values=server_options.form_values(settings),
                           mission_fields=mission_import.FIELDS,
                           groups=MISSION_GROUPS, initial={
                               "name": settings.get("ServerName", ""),
                               "password_set": bool(settings.get("Password")),
                               "rotation_type": settings.get("RotationType", 0),
                               "workshop_titles": resolve_workshop_titles(items, refresh=False),
                               "missions": settings.get("MissionRotation", []),
                               "revision": revision,
                           })


@app.post("/settings")
def save_settings():
    data = request.get_json()
    try:
        if not isinstance(data, dict):
            raise ValueError("Expected a settings object.")
        section = data.get("section", "all")
        if section not in ("all", "rotation", "server"):
            raise ValueError("Choose which settings to save.")
        changes = {}
        if section != "server":
            rotation_type = data.get("rotation_type")
            missions = data.get("missions")
            if type(rotation_type) is not int or rotation_type not in (0, 1, 2):
                raise ValueError("Choose a rotation order.")
            if not isinstance(missions, list) or not missions:
                raise ValueError("Add at least one mission to the rotation.")
            changes.update(RotationType=rotation_type, MissionRotation=[parse_mission(mission) for mission in missions])
        if section != "rotation":
            name = data.get("name")
            password_mode = data.get("password_mode", "keep")
            password = data.get("password")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Enter a server name.")
            if password_mode not in ("keep", "set", "clear"):
                raise ValueError("Choose how to handle the join password.")
            if password_mode == "set" and (not isinstance(password, str) or not password.strip()
                                           or any(char in password for char in "\r\n\0")):
                raise ValueError("Enter a join password on a single line.")
            changes["ServerName"] = name.strip()
            if password_mode != "keep":
                changes["Password"] = password if password_mode == "set" else ""
    except ValueError as error:
        return jsonify(message=str(error)), 400

    with CONFIG_LOCK:
        try:
            settings, revision = read_config()
            if revision != data.get("revision"):
                return jsonify(message="The config changed. Refresh this page before saving."), 409
            settings.update(changes)
            try:
                ports = (server_options.effective_ports(settings) if section == "rotation"
                         else server_options.apply_options(settings, data.get("options", {}), DATA_DIR))
            except ValueError as error:
                return jsonify(message=str(error)), 400
            revision = write_config(settings)
            restart_required = ports != ACTIVE_PORTS
            if restart_required:
                message = "Saved. Run sh start.sh on the Linux host to apply the UDP ports and reload the settings."
            else:
                status, _ = server_commands.reload_config(create_remote_commander(), str(CONFIG_PATH))
                message = ("Rotation saved and reloaded." if section == "rotation"
                           else "Saved and reloaded. Restart the server if the game browser still shows the previous settings.")
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


def workshop_status(settings, identifier, downloaded):
    if identifier not in configured_workshop_items(settings):
        if not downloaded:
            return "unsaved", "Add this item to the rotation to save it and start the Steam download."
        if server_options.effective_ports(settings) != ACTIVE_PORTS:
            return "restart", "Run sh start.sh on the Linux host to apply the saved settings before queueing."
        return "downloaded", "This mission is downloaded and available to queue without saving it to the rotation."
    if server_options.effective_ports(settings) != ACTIVE_PORTS:
        return "restart", "Run sh start.sh on the Linux host to apply the saved settings and start the Steam download."
    if downloaded:
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
        cached = workshop.cached_mission_file(DATA_DIR, identifier, mission_import.UPLOAD_LIMIT)
        state, message = workshop_status(settings, identifier, cached is not None)
    except (OSError, ValueError, TypeError):
        return jsonify(message="Could not read the Workshop state. Check the server configuration."), 503
    details = workshop.item_details(identifier) if request.args.get("details") == "1" else {}
    response = jsonify(id=identifier, state=state, message=message, details=details)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/settings/workshop/downloaded")
def downloaded_workshop():
    try:
        settings, _ = read_config()
        in_rotation = set(configured_workshop_items(settings))
        downloaded = workshop.downloaded_missions(DATA_DIR, mission_import.UPLOAD_LIMIT)
        titles = resolve_workshop_titles(downloaded, refresh=False)
        local = local_missions(settings)
    except (OSError, ValueError, TypeError):
        return jsonify(message="Could not read the server’s Workshop downloads."), 503
    missions = [{"id": identifier, "title": titles.get(identifier, filename), "in_rotation": identifier in in_rotation}
                for identifier, filename in downloaded.items()]
    missions.sort(key=lambda mission: (mission["title"].casefold(), mission["id"]))
    return jsonify(missions=missions, built_in=STOCK_MISSIONS, local=local)


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
            cached = workshop.cached_mission_file(DATA_DIR, identifier, mission_import.UPLOAD_LIMIT)
            state, message = workshop_status(settings, identifier, cached is not None)
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


@app.get("/settings/workshop/mods")
def mod_status():
    try:
        with MOD_LOCK:
            selected = server_mods.selected_set(DATA_DIR)
            return jsonify(selected=selected["title"] if selected else None,
                           selected_id=selected["id"] if selected else None, sets=server_mods.saved_sets(DATA_DIR), **mod_job)
    except (OSError, ValueError, KeyError):
        return jsonify(message="Could not read the selected mod set."), 503


@app.get("/mods")
def mods_page():
    return render_template("mods.html", item=request.args.get("item", "")[:240])


@app.get("/mods/inventory")
def mod_inventory_data():
    try:
        settings, _ = read_config()
        ids = configured_workshop_items(settings)
        local = local_missions(settings)
        rotation = settings.get("MissionRotation")
        local_rotation = set()
        for entry in rotation if isinstance(rotation, list) else []:
            key = entry.get("Key") if isinstance(entry, dict) else None
            if isinstance(key, dict) and key.get("Group") == "User" and isinstance(key.get("Name"), str):
                local_rotation.add(key["Name"])
        selected = server_mods.selected_set(DATA_DIR)
        selected_id = selected.get("item") if selected else None
        lookup_ids = [*ids[:96], *([selected_id] if selected_id else [])]
        items = workshop.cached_items(DATA_DIR, lookup_ids)
        declarations = workshop.read_metadata(DATA_DIR / ".panel-mission-mods.json")
        declarations = {name: declarations[name] for name in local if name in declarations}
        packages, available = mod_inventory.catalog(refresh=request.args.get("check") == "1")
        return jsonify(mod_inventory.build(items, ids, local, local_rotation, declarations, selected, packages, available))
    except (OSError, ValueError, TypeError, KeyError):
        return jsonify(message="Could not read the mission and mod inventory."), 503


@app.post("/mods/local")
def record_local_mod():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(message="Choose a local scenario and catalog package."), 400
    name, key, action = (data.get(field) for field in ("mission", "package", "action"))
    if (not isinstance(name, str) or not isinstance(key, str) or not server_mods.PACKAGE_ID.fullmatch(key)
            or action not in ("add", "remove")):
        return jsonify(message="Choose a local scenario and catalog package."), 400
    if action == "add":
        packages, available = mod_inventory.catalog()
        if not available:
            return jsonify(message="The mod catalog is unavailable. Try again."), 503
        if key not in mod_inventory.package_index(packages):
            return jsonify(message="Choose a package listed in the catalog."), 400
    with CONFIG_LOCK:
        try:
            settings, _ = read_config()
            if name not in local_missions(settings):
                return jsonify(message="That scenario is no longer in the local mission folder."), 404
            path = DATA_DIR / ".panel-mission-mods.json"
            declarations = workshop.read_metadata(path)
            saved = declarations.get(name, [])
            keys = {value for value in saved if isinstance(value, str)} if isinstance(saved, list) else set()
            if action == "add":
                keys.add(key)
                if len(keys) > server_mods.MOD_LIMIT:
                    return jsonify(message="This scenario has reached the package limit."), 400
            else:
                keys.discard(key)
            if keys:
                declarations[name] = sorted(keys)
            else:
                declarations.pop(name, None)
            workshop.write_metadata(path, declarations)
        except (OSError, ValueError, TypeError):
            return jsonify(message="Could not save the scenario's requirements."), 503
    return jsonify(message="Saved the local scenario's requirements.")


def install_mod_set(plan, activate=True):
    def progress(message):
        with MOD_LOCK:
            mod_job["message"] = message

    message = "Install stopped unexpectedly."
    try:
        if activate:
            server_mods.install_set(DATA_DIR, plan, progress)
            message = "Mod set installed. The server is switching to it. Check Server logs before queueing the mission."
        else:
            server_mods.install_set(DATA_DIR, plan, progress, activate=False)
            message = "Mod set saved. Select it under Saved mod sets when you are ready to switch."
    except ValueError as error:
        message = f"Install stopped. {error}"
    except (OSError, RuntimeError, BadZipFile) as error:
        app.logger.warning("Could not install a Workshop mod set: %s", type(error).__name__)
        message = "Install stopped. Check release availability, free disk space, and server data folder permissions, then retry."
    finally:
        with MOD_LOCK:
            mod_job.update(running=False, message=message)


@app.post("/settings/workshop/mods")
def manage_mods():
    global mod_plan
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(message="Choose a mod action."), 400
    action = data.get("action")
    if action in ("preview", "preview_update", "preview_version"):
        try:
            if action != "preview":
                package, version = data.get("package"), data.get("version")
                if not isinstance(package, str) or not server_mods.PACKAGE_ID.fullmatch(package) or not server_mods.version_key(version):
                    raise ValueError("Choose a valid mod update.")
                selected = server_mods.selected_set(DATA_DIR)
                if not selected:
                    raise ValueError("Enable a server mod set before reviewing updates.")
                entries, available = mod_inventory.catalog()
                if not available:
                    return jsonify(message="Could not read the mod catalog. Try again."), 503
                plan = server_mods.update_plan(selected, package, version, entries, exact=action == "preview_version")
            else:
                identifier = workshop.item_id(data["item"].strip())
                overrides = data.get("overrides", {})
                if not isinstance(overrides, dict) or len(overrides) > 24:
                    raise ValueError("Choose valid replacement versions.")
                if any(not server_mods.PACKAGE_ID.fullmatch(key) or not server_mods.version_key(value)
                       for key, value in overrides.items()):
                    raise ValueError("Choose valid replacement versions.")
                plan = server_mods.make_plan(identifier, overrides)
        except (ValueError, KeyError, AttributeError) as error:
            return jsonify(message=str(error) if isinstance(error, ValueError) else "Choose a Workshop mission."), 400
        except OSError:
            return jsonify(message="Could not read the mod catalog. Try again."), 503
        with MOD_LOCK:
            mod_plan = plan
        assets = [{key: asset.get(key) for key in ("name", "version", "game_version")} for asset in plan["assets"]]
        return jsonify(id=plan["id"], assets=assets, choices=plan["choices"],
                       problems=plan["problems"], overrides=plan["overrides"],
                       changes=plan.get("changes", []), requirements=plan.get("requirements", []))

    with MOD_LOCK:
        if mod_job["running"]:
            return jsonify(message="A mod set is already installing."), 409
        if action in ("activate", "pin"):
            try:
                key = data.get("set")
                if action == "pin":
                    package, pinned = data.get("package"), data.get("pinned")
                    if not isinstance(package, str) or type(pinned) is not bool:
                        raise ValueError("Choose a package and pin state.")
                    server_mods.pin_version(DATA_DIR, key, package, pinned)
                    message = "Version pinned for this set." if pinned else "Version pin removed."
                else:
                    current = server_mods.selected_set(DATA_DIR)
                    if current and key == current["id"]:
                        return jsonify(message="This mod set is already selected.")
                    server_mods.activate_saved_set(DATA_DIR, key)
                    message = "Saved mod set selected. Check Server logs for the restart."
            except (ValueError, OSError, KeyError) as error:
                return jsonify(message=str(error) if isinstance(error, ValueError) else "Could not change the saved mod set."), 409
            mod_job["message"] = message
            return jsonify(message=message)
        if action == "vanilla":
            try:
                server_mods.select_set(DATA_DIR, "vanilla")
            except (OSError, ValueError):
                return jsonify(message="Could not select vanilla mode. Check the server data folder."), 503
            mod_job["message"] = "Vanilla mode selected. Check Server logs for the restart."
            return jsonify(message=mod_job["message"])
        if action == "clean_copy":
            try:
                plan = server_mods.clean_copy_plan(DATA_DIR, data.get("set"))
            except (ValueError, OSError):
                return jsonify(message="Could not read that saved mod set for a clean install."), 409
            mod_job.update(running=True, message="Downloading a clean copy of the mod set…")
            Thread(target=install_mod_set, args=(plan, False), daemon=True).start()
            return jsonify(message=mod_job["message"]), 202
        if action in ("install", "save_set") and mod_plan and data.get("plan") == mod_plan["id"] and not mod_plan["problems"]:
            try:
                selected = server_mods.selected_set(DATA_DIR)
            except (OSError, ValueError):
                return jsonify(message="Could not read the selected mod set."), 503
            if selected and selected.get("id") == mod_plan["id"]:
                return jsonify(message="This mod set is already selected. Check Server logs for its startup.")
            if mod_plan.get("base_id") and (not selected or selected.get("id") != mod_plan["base_id"]
                                           or selected.get("pins", {}) != mod_plan.get("base_pins", {})):
                return jsonify(message="The server mod set changed. Check for updates and review the new plan."), 409
            mod_job.update(running=True, message="Preparing the mod set…")
            Thread(target=install_mod_set, args=(mod_plan, action == "install"), daemon=True).start()
            return jsonify(message=mod_job["message"]), 202
    return jsonify(message="Review a complete, current mod plan before installing."), 409


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
    return select_next_mission()


@app.post("/settings/switch-mission")
def switch_mission():
    return select_next_mission(switch=True)


def select_next_mission(switch=False):
    data = request.get_json(silent=True)
    try:
        mission = parse_mission(data)
        delay = data.get("delay", 0) if switch else 0
        announcement = data.get("announcement", "") if switch else ""
        if type(delay) is not int or not 0 <= delay <= 3600:
            raise ValueError("Enter a whole delay from 0 to 3600 seconds.")
        if not isinstance(announcement, str) or len(announcement) > 240 or any(c in announcement for c in "\r\n\0"):
            raise ValueError("Enter an announcement of up to 240 characters on one line.")
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
                cached = workshop.cached_mission_file(DATA_DIR, key["Name"], mission_import.UPLOAD_LIMIT)
                state, message = workshop_status(settings, key["Name"], cached is not None)
                if state not in ("cached", "downloaded"):
                    return jsonify(message=message), 409
        except (OSError, ValueError, TypeError):
            return jsonify(message="Could not verify the mission in the server data folder."), 503
    label = key["Name"]
    if key["Group"] == "Workshop":
        label = resolve_workshop_titles([label], refresh=False).get(label) or cached.stem
    warning = switch_mod_warning(key) if switch else ""

    with MISSION_LOCK:
        commander = create_remote_commander()
        status, _ = server_commands.set_next_mission(commander, key["Group"], key["Name"], mission["MaxTime"])
        if status != "Success":
            return jsonify(message=f"Could not queue the mission. The server returned {status}."), 502
        if not switch:
            return jsonify(message=f"Queued {label} for after the current mission.")

        status, timing = server_commands.get_mission_time(commander)
        max_time = timing.get("maxTime") if isinstance(timing, dict) else None
        if status != "Success" or type(max_time) not in (int, float) or not math.isfinite(max_time):
            return jsonify(message=f"Queued {label}, but the server did not report its mission timer. Check Overview before retrying."), 502
        if max_time <= 0:
            return jsonify(message=f"Queued {label}. The server has no active mission timer and will use this choice the next time it loads a mission."), 202

        status, missions = server_commands.get_mission(commander)
        upcoming = missions.get("nextMission") if isinstance(missions, dict) else None
        next_key = upcoming.get("Key") if isinstance(upcoming, dict) else None
        if status != "Success" or not isinstance(next_key, dict) or any(next_key.get(field) != key[field] for field in ("Group", "Name")):
            return jsonify(message=f"The server did not confirm {label} as next. Check Overview before retrying."), 502

        if announcement.strip() or warning or delay:
            safe_label = " ".join(label.split()).replace("<", "‹").replace(">", "›")[:100]
            when = f" in {delay} seconds" if delay else " now"
            message = " ".join(part for part in (f"Switching to {safe_label}{when}.", warning, announcement.strip()) if part)
            status, _ = server_commands.send_chat_message(commander, message)
            if status != "Success":
                return jsonify(message=f"Queued {label}, but the announcement failed ({status}). The timer was left alone."), 502
        status, _ = server_commands.set_time_remaining(commander, float(max(1, delay)))
        if status != "Success":
            return jsonify(message=f"Queued {label}, but the server did not confirm the timer change ({status}). Check Overview before retrying."), 502
        timing_text = f" The current round ends in {delay} seconds." if delay else ""
        return jsonify(message=f"Switch requested. {label} is next.{timing_text} The server’s normal transition delay still applies."), 202


def switch_mod_warning(key):
    name = key["Name"]
    if key["Group"] == "Workshop":
        try:
            details = workshop.cached_items(DATA_DIR, [name]).get(name, {})
            metadata = workshop.describe_item(details, [])
            if metadata.get("mods", {}).get("required"):
                return "This mission requires mods. Match the server’s mod versions before joining."
            selected = server_mods.selected_set(DATA_DIR)
            if selected and selected.get("item") == name:
                return "The server has a mod set for this mission. Check your client mods before joining."
            if "title" in metadata:
                return ""
        except (OSError, ValueError, TypeError):
            pass
        return "Check the Workshop mission for required client mods."
    elif key["Group"] == "User":
        if workshop.read_metadata(DATA_DIR / ".panel-mission-mods.json").get(name):
            return "This scenario requires mods. Match the server’s mod versions before joining."
    return ""


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
    Thread(target=monitor_performance, daemon=True).start()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT, ssl_context=ssl_context)
