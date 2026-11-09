"""Read live and completed reports written atomically by the server collector."""

import csv
import io
import json
import re
from datetime import datetime, timezone


KILL_FIELDS = ("player_aircraft", "ai_aircraft", "buildings", "vehicles", "ships", "missiles")
COUNTERS = (*KILL_FIELDS, "assists", "deaths")
SORT_OPTIONS = {
    "total": "Total kills and interceptions",
    "player_aircraft": "Player aircraft",
    "ai_aircraft": "AI aircraft",
    "buildings": "Buildings",
    "vehicles": "Vehicles",
    "ships": "Ships",
    "missiles": "Missiles intercepted",
    "assists": "Assists",
    "deaths": "Aircraft losses",
}
REPORT_FILE = re.compile(r"[a-f0-9]{32}\.json")
REPORT_ID = re.compile(r"[a-f0-9]{32}")
STEAM_ID = re.compile(r"[0-9]{17}")


def read_report(path, live_instance=None):
    with path.open("rb") as stream:
        raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        return None
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get("schema") != 1:
        return None
    identifier = report.get("id")
    if not isinstance(identifier, str) or not REPORT_ID.fullmatch(identifier):
        return None
    if not isinstance(report.get("mission"), str):
        return None
    if live_instance is None:
        if identifier != path.stem or report.get("status") not in ("completed", "interrupted"):
            return None
        end_field = "ended_utc"
    else:
        if report.get("status") != "live" or report.get("game_instance") != live_instance:
            return None
        end_field = "updated_utc"
    started = datetime.fromisoformat(report["started_utc"])
    ended = datetime.fromisoformat(report[end_field])
    if started.tzinfo is None or ended.tzinfo is None or ended < started:
        return None
    if live_instance is not None and not 0 <= (datetime.now(timezone.utc) - ended).total_seconds() < 30:
        return None
    players = report.get("players")
    if not isinstance(players, list):
        return None
    if any(not isinstance(player, dict) or not isinstance(player.get("name"), str)
           or not isinstance(player.get("steam_id"), str) or not STEAM_ID.fullmatch(player["steam_id"])
           or any(type(player.get(key)) is not int or player[key] < 0 for key in COUNTERS)
           for player in players):
        return None
    report["duration"] = int((ended - started).total_seconds())
    report["ended_label"] = ended.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return report


def live_report(directory, instance):
    path = directory / "live.json"
    if not instance or directory.is_symlink() or directory.parent.is_symlink() or path.is_symlink():
        return None
    try:
        return read_report(path, live_instance=instance)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def collector_state(root, launch_path, instance, enabled):
    def marker(path):
        try:
            if path.is_file() and not path.is_symlink():
                with path.open(encoding="ascii") as stream:
                    return stream.read(96).strip()
        except (OSError, ValueError):
            pass
        return ""

    launch_instance, _, state = marker(launch_path).partition(" ")
    if instance and launch_instance == instance and state == "mod_set":
        return "mod_set"
    if not enabled:
        return "off"
    if not instance or launch_instance != instance:
        return "unconfirmed"
    if state == "off":
        return "restart"
    if state == "rebuild":
        return "rebuild"
    if state == "starting" and marker(root / "reports" / "collector-ready") == instance:
        return "ready"
    return "unconfirmed"


def is_finished(directory, identifier):
    if not identifier or not REPORT_ID.fullmatch(identifier) or directory.is_symlink() or directory.parent.is_symlink():
        return False
    path = directory / f"{identifier}.json"
    return path.is_file() and not path.is_symlink()


def recent_reports(directory):
    if directory.is_symlink() or directory.parent.is_symlink():
        return []
    try:
        candidates = [(path.stat().st_mtime_ns, path) for path in directory.iterdir()
                      if REPORT_FILE.fullmatch(path.name) and not path.is_symlink() and path.is_file()]
    except OSError:
        return []
    reports = []
    for _, path in sorted(candidates, reverse=True):
        try:
            report = read_report(path)
        except (OSError, ValueError, TypeError, KeyError):
            continue
        if report:
            reports.append(report)
        if len(reports) == 30:
            break
    return reports


def player_order(player, column):
    count = sum(player[key] for key in KILL_FIELDS) if column == "total" else player[column]
    return -count, player["name"].casefold()


def players_csv(report):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(("Steam ID", "Player", *(SORT_OPTIONS[key] for key in COUNTERS)))
    for player in sorted(report["players"], key=lambda row: player_order(row, "total")):
        name = player["name"]
        # Spreadsheet apps may interpret names starting with a formula marker.
        if name.lstrip().startswith(("=", "+", "-", "@")) or name.startswith(("\t", "\r", "\n")):
            name = "'" + name
        writer.writerow((player["steam_id"], name, *(player[key] for key in COUNTERS)))
    return io.BytesIO(output.getvalue().encode("utf-8-sig"))


def summarize(report):
    highlights = []
    for label, fields in (
        ("Aircraft shot down", ("player_aircraft", "ai_aircraft")),
        ("Surface targets destroyed", ("buildings", "vehicles", "ships")),
        ("Missiles intercepted", ("missiles",)),
        ("Assists", ("assists",)),
    ):
        counts = [(sum(player[key] for key in fields), player["name"]) for player in report["players"]]
        best = max((count for count, _ in counts), default=0)
        leaders = sorted((name for count, name in counts if best and count == best), key=str.casefold)
        highlights.append({"label": label, "total": sum(count for count, _ in counts),
                           "best": best, "leaders": leaders[:3], "more": max(0, len(leaders) - 3)})

    timeline = report.get("timeline")
    windows = []
    if isinstance(timeline, list):
        fields = ("minute", "aircraft", "surface", "missiles")
        for row in timeline[:289]:
            if (not isinstance(row, dict) or any(type(row.get(key)) is not int or row[key] < 0 for key in fields)
                    or row["minute"] > 1440 or row["minute"] % 5):
                continue
            total = row["aircraft"] + row["surface"] + row["missiles"]
            if total:
                windows.append({**row, "total": total})
    return highlights, sorted(windows, key=lambda row: row["minute"])
