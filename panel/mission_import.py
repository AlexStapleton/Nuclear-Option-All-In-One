"""Read saved missions and publish edited copies in the game's mission directory."""

import json
import math
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

UPLOAD_LIMIT = 16 * 1024 * 1024
EXPANDED_LIMIT = 64 * 1024 * 1024
FIELDS = {
    "description": ("missionSettings", "Briefing", "textarea"),
    "allowRespawn": ("missionSettings", "Allow respawns", "checkbox"),
    "timeOfDay": ("environment", "Start time (hour, 0 to 24)", "number"),
    "weatherIntensity": ("environment", "Weather intensity (0 to 1)", "number"),
    "cloudAltitude": ("environment", "Cloud altitude (meters)", "number"),
}


def safe_component(name):
    return (bool(name) and name not in (".", "..") and not name.endswith((" ", "."))
            and not any(char in '<>:"/\\|?*' or ord(char) < 32 for char in name))


def parse_document(raw):
    def reject_constant(value):
        raise ValueError("Mission JSON must use finite numbers.")

    try:
        mission = json.loads(raw, parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError("The mission file must contain valid JSON.") from error
    if not isinstance(mission, dict) or not isinstance(mission.get("missionSettings"), dict):
        raise ValueError("Choose a mission JSON saved by the Nuclear Option Mission Editor.")
    return mission


def package_members(archive, stem):
    files = {}
    members = archive.infolist()
    if len(members) > 512 or sum(member.file_size for member in members) > EXPANDED_LIMIT:
        raise ValueError("The ZIP exceeds 512 entries or 64 MiB unpacked.")
    seen = set()
    for member in members:
        parts = member.orig_filename.rstrip("/").split("/")
        kind = stat.S_IFMT(member.external_attr >> 16)
        if (not all(safe_component(part) for part in parts) or "\0" in member.orig_filename
                or kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
            raise ValueError("The ZIP contains an unsafe path or a special file.")
        folded = tuple(part.casefold() for part in parts)
        if folded in seen:
            raise ValueError("The ZIP contains duplicate paths.")
        seen.add(folded)
        if not member.is_dir():
            files[tuple(parts)] = member

    # Windows ZIPs may contain the mission folder itself or its contents.
    wrapped = {path[0] for path in files}
    if len(wrapped) == 1 and all(len(path) > 1 for path in files):
        folder = wrapped.pop()
        files = {path[1:]: member for path, member in files.items()}
        filename = (f"{folder}.json",)
    else:
        filename = (f"{stem}.json",)
    metadata = files.get(("meta.json",))
    if metadata and metadata.file_size <= 64 * 1024:
        try:
            manifest = json.loads(archive.read(metadata))
        except (ValueError, RecursionError):
            manifest = None
        name = manifest.get("FileName") if isinstance(manifest, dict) else None
        if isinstance(name, str) and safe_component(name) and (f"{name}.json",) in files:
            filename = (f"{name}.json",)
    if filename not in files:
        candidates = [path for path in files if len(path) == 1 and path[0].lower().endswith(".json")
                      and path[0].lower() not in ("meta.json", "workshop.json", "objective-graph.layout.json")]
        if len(candidates) == 1:
            filename = candidates[0]
    if filename not in files:
        raise ValueError("The ZIP must contain a single mission folder with its matching JSON.")
    # The imported copy has its own name and no Steam publishing identity.
    for path in list(files):
        if path != filename and len(path) == 1 and path[0].lower() in ("meta.json", "workshop.json"):
            del files[path]
    return filename, files


@contextmanager
def read_upload(upload):
    path = PurePosixPath(upload.filename.replace("\\", "/"))
    if not safe_component(path.stem):
        raise ValueError("The mission filename is invalid.")
    if upload.stream.seek(0, 2) > UPLOAD_LIMIT:
        raise ValueError("Choose a mission JSON or ZIP up to 16 MiB.")
    upload.stream.seek(0)
    if path.suffix.lower() == ".json":
        yield path.stem, parse_document(upload.read()), None, {}
        return
    if path.suffix.lower() != ".zip":
        raise ValueError("Choose a saved mission JSON or ZIP file.")
    try:
        with ZipFile(upload.stream) as archive:
            filename, members = package_members(archive, path.stem)
            main = members.pop(filename)
            if main.file_size > UPLOAD_LIMIT:
                raise ValueError("The mission JSON exceeds 16 MiB.")
            yield PurePosixPath(filename[0]).stem, parse_document(archive.read(main)), archive, members
    except (BadZipFile, NotImplementedError, RuntimeError, EOFError) as error:
        raise ValueError("The ZIP could not be read. Use a standard ZIP without encryption.") from error


def editable_values(mission):
    values = {}
    for key, (section, _, control) in FIELDS.items():
        group = mission.get(section)
        value = group.get(key) if isinstance(group, dict) else None
        if (control == "textarea" and isinstance(value, str)
                or control == "checkbox" and type(value) is bool
                or control == "number" and type(value) in (int, float) and abs(value) < math.inf):
            values[key] = value
    return values


def save_copy(directory, name, mission, edits, archive, members):
    if (not safe_component(name) or name != name.strip() or name.startswith(".")
            or len(name.encode("utf-8")) > 180):
        raise ValueError("Enter a mission name up to 180 bytes without slashes or special filename characters.")
    current = editable_values(mission)
    if not isinstance(edits, dict) or edits.keys() - current.keys():
        raise ValueError("This mission does not support one of the requested edits.")
    for key, value in edits.items():
        section, label, control = FIELDS[key]
        if control == "number":
            limit = {"timeOfDay": 24, "weatherIntensity": 1}.get(key)
            valid = type(value) in (int, float) and abs(value) < math.inf
            if not valid or (limit is not None and not 0 <= value <= limit):
                raise ValueError(f"Enter a valid value for {label}.")
        elif type(value) is not type(current[key]):
            raise ValueError(f"Enter a valid value for {label}.")
        mission[section][key] = value

    target = directory / name
    if target.exists() or target.is_symlink():
        raise FileExistsError("That mission name already exists. Choose a new name.")
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{name}.json"
    if any(path[0].casefold() == filename.casefold() for path in members):
        raise ValueError("The new mission name conflicts with a file in the ZIP.")
    with TemporaryDirectory(prefix=".mission-import-", dir=directory) as staging:
        ready = Path(staging) / "ready"
        ready.mkdir()
        for path, member in members.items():
            destination = ready.joinpath(*path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output)
        with (ready / filename).open("x", encoding="utf-8") as output:
            json.dump(mission, output, ensure_ascii=True, allow_nan=False, indent=2)
            output.write("\n")
        ready.rename(target)
