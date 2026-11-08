"""Validated launcher preferences, config snapshots, and optional performance data."""

import json
import math
import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import time

from server_mods import mod_root, selected_set

PREFERENCES = ".panel-launch-settings"
PROFILES = ".panel-startup-profiles.json"
DEFAULTS = {"fps": 60, "log_mib": 1, "log_files": 2}
RANGES = {"fps": (10, 240), "log_mib": (1, 32), "log_files": (1, 10)}
INSTANCE = re.compile(r"[0-9a-f-]{36}")
PROFILE_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,60}")
PERF_FIELDS = {"JobWorkers": "workers", "Timing": "timing"}


def private_write(path, text):
    with NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", newline="\n", delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(text)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def small_file(path, limit):
    if path.is_symlink():
        raise ValueError("Expected a regular panel file.")
    if not path.is_file():
        return None
    with path.open(encoding="utf-8-sig") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError("The panel file exceeds its size limit.")
    return content


def preferences(data_dir):
    # The container launcher reads the same three integers without evaluating shell code.
    try:
        raw = small_file(data_dir / PREFERENCES, 64)
        values = raw.strip().split(" ") if raw else []
        if len(values) == len(DEFAULTS) and all(re.fullmatch(r"[1-9][0-9]{0,2}", value) for value in values):
            return validate_preferences(dict(zip(DEFAULTS, map(int, values))))
    except (OSError, ValueError):
        pass
    return DEFAULTS.copy()


def validate_preferences(values):
    for key, (minimum, maximum) in RANGES.items():
        value = values.get(key)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"Enter a whole number from {minimum} to {maximum} for {key.replace('_', ' ')}.")
    return {key: values[key] for key in DEFAULTS}


def save_preferences(data_dir, values):
    values = validate_preferences(values)
    private_write(data_dir / PREFERENCES, " ".join(str(values[key]) for key in DEFAULTS) + "\n")


def load_profiles(data_dir):
    content = small_file(data_dir / PROFILES, 8 * 1024 * 1024)
    saved = json.loads(content) if content else {}
    if not isinstance(saved, dict) or any(not PROFILE_NAME.fullmatch(name) or not isinstance(config, dict)
                                         for name, config in saved.items()):
        raise ValueError("The saved startup profiles are invalid.")
    return saved


def save_profiles(data_dir, profiles):
    raw = json.dumps(profiles, ensure_ascii=False)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("The startup profiles have reached their storage limit.")
    private_write(data_dir / PROFILES, raw)


def plugin_config(data_dir):
    selected = selected_set(data_dir)
    if not selected:
        return None, None, {}
    root = mod_root(data_dir) / selected["id"]
    bep = root / "BepInEx"
    if not any(dll.is_file() and dll.resolve().is_relative_to(bep)
               for dll in (bep / "plugins").rglob("NukeStats.dll")):
        return None, None, {}
    config = bep / "config" / "anz.nukestats.cfg"
    if not config.resolve().is_relative_to(bep):
        raise ValueError("The NukeStats configuration must stay within its mod set.")
    raw = small_file(config, 512 * 1024)
    if not raw:
        return config, raw, {}
    values = {}
    in_perf = False
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_perf = line == "[Perf]"
        if in_perf:
            key, _, value = line.partition("=")
            if key.strip() in (*PERF_FIELDS, "TargetFps", "FramePacer"):
                values[key.strip()] = value.strip()
    return config, raw, values


def performance_options(data_dir):
    path, _, values = plugin_config(data_dir)
    ready = all(key in values for key in PERF_FIELDS)
    workers = values.get("JobWorkers", "0")
    return {"installed": path is not None, "available": ready,
            "workers": int(workers) if re.fullmatch(r"[0-9]{1,3}", workers) else 0,
            "timing": values.get("Timing", "false").lower() == "true",
            "overrides_fps": any(values.get(key, "0") != "0" for key in ("TargetFps", "FramePacer"))}


def save_performance(data_dir, workers, timing):
    if type(workers) is not int or not 0 <= workers <= 64 or type(timing) is not bool:
        raise ValueError("Choose 0 to 64 physics workers and a measurement setting.")
    path, raw, values = plugin_config(data_dir)
    if not path or not all(key in values for key in PERF_FIELDS):
        raise ValueError("Start a mod set with a compatible NukeStats version first.")
    replacements = {"JobWorkers": str(workers), "Timing": str(timing).lower()}
    in_perf = False
    lines = []
    for line in raw.splitlines():
        if line.strip().startswith("["):
            in_perf = line.strip() == "[Perf]"
        key, _, _ = line.partition("=")
        key = key.strip()
        lines.append(f"{key} = {replacements[key]}" if in_perf and key in replacements else line)
    private_write(path, "\n".join(lines) + "\n")


def launcher_status(data_dir):
    logs = data_dir / ".panel-logs"
    try:
        raw = small_file(logs / "launch-active", 128)
        instance, *values = raw.strip().split(" ") if raw else ("",)
        if not INSTANCE.fullmatch(instance) or len(values) != len(DEFAULTS):
            return None, None
        active = validate_preferences(dict(zip(DEFAULTS, map(int, values))))
    except (OSError, ValueError):
        return None, None
    try:
        raw = small_file(logs / "performance-sample", 20000)
        token, timestamp, payload = raw.split(" ", 2) if raw else ("", "0", "{}")
        age = int(time()) - int(timestamp)
        if token != instance or not 0 <= age <= 150:
            return active, None
        data = json.loads(payload)
        if not isinstance(data, dict) or data.get("t") != "perf":
            return active, None
        def measure(key):
            value = data.get(key)
            return value if type(value) in (int, float) and 0 <= value <= 1_000_000 and math.isfinite(value) else None

        seconds, frames = measure("sec"), measure("frames")
        if not seconds or seconds < 1 or frames is None:
            return active, None
        return active, {"fps": round(frames / seconds, 1), "frame_ms": measure("ft_ms"),
                        "p95_ms": measure("ft_p95"), "workers": measure("workers"),
                        "workers_max": measure("workers_max"), "age": age}
    except (OSError, ValueError, RecursionError):
        return active, None
