"""Index declared mission requirements and the catalog links between packages."""

from time import monotonic, time

import server_mods
import workshop


LOADER_ID = server_mods.LOADER_ID


def catalog(refresh=False):
    try:
        if refresh:
            workshop.mod_catalog.cache_clear()
        return workshop.mod_catalog(int(monotonic() // 600)), True
    except (OSError, ValueError, RecursionError):
        return [], False


def package_index(entries):
    return {entry["id"].casefold(): entry for entry in entries
            if isinstance(entry.get("id"), str) and server_mods.PACKAGE_ID.fullmatch(entry["id"].casefold())}


def artifact_for(package, version):
    artifacts = package.get("artifacts")
    if not isinstance(artifacts, list):
        return {}
    releases = [item for item in artifacts if isinstance(item, dict) and item.get("category") == "release"]
    if version:
        return next((item for item in releases if item.get("version") == version), {})
    return max(releases, key=lambda item: server_mods.version_key(item.get("version")), default={})


def build(items, workshop_ids, local, local_rotation, declarations, selected, entries, catalog_available):
    packages = package_index(entries)
    releases = server_mods.server_releases(entries)
    selected_assets = {asset.get("id", LOADER_ID): asset for asset in (selected or {}).get("assets", [])}
    selected_item = (selected or {}).get("item")
    mods = {}
    coverage = []
    visited = set()

    def mod(key, label=None):
        package = packages.get(key, {})
        installed = selected_assets.get(key, {})
        name = installed.get("name") or package.get("displayName") or label or key
        latest = releases.get(key, [])
        version = latest[0][0]["version"] if latest else None
        current = installed.get("version")
        return mods.setdefault(key, {"id": key, "name": name, "selected_version": installed.get("version"),
                                     "latest_version": version,
                                     "update_available": bool(current and version and server_mods.version_key(current)
                                                              and server_mods.version_key(version) > server_mods.version_key(current)),
                                     "used_by": {}, "supports": set()})

    def link(key, mission, kind, requested_version=None):
        uses = mods[key]["used_by"]
        label = (mission["source"], mission["key"])
        if label not in uses or kind == "Direct":
            uses[label] = {"mission": mission["title"], "source": mission["source"],
                           "rotation": mission["rotation"], "kind": kind, "requested_version": requested_version}

    def visit(key, mission, versions, version=None, trail=()):
        if key in trail or len(trail) >= server_mods.MOD_LIMIT:
            return
        mod(key)
        link(key, mission, "Dependency" if trail else "Direct", None if trail else version)
        if not trail:
            mod(LOADER_ID, server_mods.BEPINEX["name"])["supports"].add(key)
            link(LOADER_ID, mission, "Dependency")
        chosen_version = versions.get(key) or version
        visit_key = (mission["source"], mission["key"], key, str(chosen_version))
        if visit_key in visited:
            return
        visited.add(visit_key)
        artifact = artifact_for(packages.get(key, {}), chosen_version)
        if not artifact and not trail and version:
            mission["partial"] = True
        parent = artifact.get("extends")
        dependencies = artifact.get("dependencies")
        dependencies = list(dependencies) if isinstance(dependencies, list) else []
        if parent:
            dependencies.append(parent)
        children = set()
        for dependency in dependencies:
            if not isinstance(dependency, dict) or not isinstance(dependency.get("id"), str):
                continue
            target = dependency["id"].casefold()
            if target in (*trail, key) or target in children:
                continue
            children.add(target)
            mod(target)["supports"].add(key)
            visit(target, mission, versions, dependency.get("version"), (*trail, key))

    for identifier in dict.fromkeys([*workshop_ids, *([selected_item] if selected_item else [])]):
        item = items.get(identifier)
        details = workshop.describe_item(item, entries) if item else {}
        title = details.get("title") or ((selected or {}).get("title") if selected_item == identifier else None)
        mission = {"key": identifier, "title": title or f"Workshop item {identifier}", "source": "Workshop",
                   "rotation": identifier in workshop_ids, "partial": False}
        requirements = details.get("mods", {}).get("requirements", [])
        versions = {key: asset["version"] for key, asset in selected_assets.items()} if identifier == selected_item else {}
        for requirement in requirements:
            key = requirement.get("package_id")
            if key:
                visit(key, mission, versions, requirement.get("matched_version") or requirement.get("requested_version"))
            else:
                key = "unmatched:" + workshop.normalized_name(requirement["name"])
                mod(key, requirement["name"])
                link(key, mission, "Direct")
                mission["partial"] = True
        if identifier == selected_item:
            for key, asset in selected_assets.items():
                mod(key)
                link(key, mission, "Selected set")
                if asset.get("parent") in selected_assets:
                    mod(asset["parent"])["supports"].add(key)
        if not item or not title:
            mission["notes"] = "Steam details unavailable"
        elif not catalog_available or time() - item.get("checked", 0) >= workshop.CACHE_TTL or mission["partial"]:
            mission["notes"] = "Some dependency details are unavailable or cached"
        elif requirements:
            mission["notes"] = "Requirements listed by the author"
        elif details.get("mods", {}).get("required"):
            mission["notes"] = "Mod required · no package names listed"
        else:
            mission["notes"] = "No requirements listed on Steam"
        coverage.append(mission)

    for name in local:
        keys = declarations.get(name, [])
        keys = [key for key in keys if isinstance(key, str) and server_mods.PACKAGE_ID.fullmatch(key)] if isinstance(keys, list) else []
        mission = {"key": name, "title": name, "source": "Local", "rotation": name in local_rotation,
                   "notes": "Requirements recorded by an admin" if keys else "No requirements recorded"}
        for key in keys:
            visit(key, mission, {})
        coverage.append(mission)

    for entry in mods.values():
        uses = list(entry["used_by"].values())
        entry["used_by"] = sorted(uses, key=lambda use: (use["mission"].casefold(), use["source"]))
        entry["supports"] = sorted((mods[key]["name"] for key in entry["supports"]), key=str.casefold)
        entry["role"] = "Foundation" if entry["supports"] else "Shared" if len(uses) > 1 else "Mission specific"
    sorted_mods = sorted(mods.values(), key=lambda entry: (not entry["supports"], -len(entry["used_by"]), entry["name"].casefold()))
    return {"mods": sorted_mods, "missions": coverage, "local": local, "declarations": declarations,
            "selected_title": (selected or {}).get("title"),
            "packages": sorted(({"id": key, "name": entry.get("displayName") or key} for key, entry in packages.items()),
                               key=lambda entry: entry["name"].casefold()), "catalog_available": catalog_available}
