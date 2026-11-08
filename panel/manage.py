"""Add a dashboard and settings to Shockfront's command panel."""

import hashlib
import json
import math
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock, Thread
from zipfile import BadZipFile

from flask import abort, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

import config
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
MOD_LOCK = Lock()
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
    label = key["Name"]
    if key["Group"] == "Workshop":
        label = resolve_workshop_titles([label], refresh=False).get(label, label)
    return jsonify(message=f"Queued {label} for after the current mission.")


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
