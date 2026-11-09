"""Read completed reports written atomically by the server collector."""

import json
import re
from datetime import datetime


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
STEAM_ID = re.compile(r"[0-9]{17}")


def read_report(path):
    with path.open("rb") as stream:
        raw = stream.read(256 * 1024 + 1)
    if len(raw) > 256 * 1024:
        return None
    report = json.loads(raw)
    if not isinstance(report, dict) or report.get("schema") != 1 or report.get("id") != path.stem:
        return None
    if not isinstance(report.get("mission"), str) or report.get("status") not in ("completed", "interrupted"):
        return None
    started = datetime.fromisoformat(report["started_utc"])
    ended = datetime.fromisoformat(report["ended_utc"])
    if started.tzinfo is None or ended.tzinfo is None or ended < started:
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
    report["ended_label"] = ended.strftime("%Y-%m-%d %H:%M UTC")
    return report


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
