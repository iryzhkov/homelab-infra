#!/usr/bin/env python3
"""Reconcile the household search scope. No media or download mutations."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

LABEL = "media-search-animation"
SERVICES = {"prowlarr": ("prowlarr", 9696, "v1"),
            "sonarr": ("sonarr-tv", 8989, "v3"),
            "radarr": ("radarr-movie", 7878, "v3")}


class API:
    def __init__(self, name, state_root):
        directory, port, version = SERVICES[name]
        config = ET.parse(Path(state_root) / directory / "config/config.xml").getroot()
        self.key = config.findtext("ApiKey") or ""
        if not self.key:
            raise RuntimeError("Missing API key for " + name)
        self.base = ("http://127.0.0.1:" + str(port)
                     + (config.findtext("UrlBase") or "").rstrip("/") + "/api/" + version)

    def call(self, path, method="GET", body=None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base + "/" + path, data=data,
                    method=method, headers={"X-Api-Key": self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            # Error bodies and URLs may contain credentials; do not echo them.
            raise RuntimeError(method + " " + path.split("?")[0]
                               + " returned HTTP " + str(error.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("Service request failed: " + method + " " + path.split("?")[0]) from None


def is_animation(item):
    return any(str(g).lower() == "animation" for g in item.get("genres", []))


def is_anime_series(item):
    return (is_animation(item) or item.get("seriesType") == "anime"
            or "anime" in str(item.get("rootFolderPath", "")).lower()
            or any("anime" in p.lower() for p in Path(item.get("path", "")).parts[:-1]))


def category_changes(app):
    desired = copy.deepcopy(app)
    changed = False
    for field in desired.get("fields", []):
        if field["name"] in ("syncCategories", "animeSyncCategories"):
            old = field.get("value") or []
            new = [v for v in old if not 6000 <= int(v) < 7000]
            if new != old:
                field["value"] = new
                changed = True
    return desired if changed else None


def specification(schema, implementation, value):
    result = copy.deepcopy(next(s for s in schema if s["implementation"] == implementation))
    result["name"] = implementation
    result["negate"] = False
    result["required"] = False
    for field in result["fields"]:
        if field["name"] == "value":
            field["value"] = value
    return result


def rule(name, tag, spec):
    return {"name": name, "removeTagsAutomatically": False, "tags": [tag],
            "specifications": [spec]}


def rule_equal(current, desired):
    def normalized(r):
        return {"name": r["name"], "tags": sorted(r["tags"]),
                "removeTagsAutomatically": r["removeTagsAutomatically"],
                "specifications": [{"implementation": s["implementation"],
                    "negate": s.get("negate", False), "required": s.get("required", False),
                    "fields": [(f["name"], f.get("value")) for f in s["fields"]]}
                    for s in r["specifications"]]}
    return normalized(current) == normalized(desired)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", default="/srv/swarm")
    parser.add_argument("--backup-root", default="/var/backups/media-search-scope")
    args = parser.parse_args()
    if args.apply and args.check:
        parser.error("--check and --apply are mutually exclusive")
    apis = {name: API(name, args.state_root) for name in SERVICES}
    p = apis["prowlarr"]
    snapshot = {"applications": p.call("applications"),
                "prowlarrIndexers": p.call("indexer"), "apps": {}}
    actions = []
    # Refuse ambiguous future application additions rather than changing another target.
    for implementation in ("Sonarr", "Radarr"):
        if sum(a.get("implementation") == implementation for a in snapshot["applications"]) != 1:
            raise RuntimeError("Expected exactly one household " + implementation + " application")
    for app in snapshot["applications"]:
        if app.get("implementation") in ("Sonarr", "Radarr"):
            changed = category_changes(app)
            if changed:
                actions.append(("prowlarr", "applications/" + str(app["id"]), "PUT", changed))
    # Read all inputs and validate schemas before any writes.
    for name in ("sonarr", "radarr"):
        api = apis[name]
        resource = "series" if name == "sonarr" else "movie"
        data = {path: api.call(path) for path in
                ("tag", "autotagging", "autotagging/schema", "indexer", resource, "rootfolder")}
        snapshot["apps"][name] = data
        tags = [t for t in data["tag"] if t["label"] == LABEL]
        if len(tags) > 1:
            raise RuntimeError("Duplicate managed tag")
        tag = tags[0]["id"] if tags else -1
        if not tags:
            actions.append((name, "tag", "POST", {"label": LABEL}))
        schemas = data["autotagging/schema"]
        specs = [("animation", "GenreSpecification", ["Animation"])]
        if name == "sonarr":
            type_schema = next(s for s in schemas if s["implementation"] == "SeriesTypeSpecification")
            value_field = next(f for f in type_schema["fields"] if f["name"] == "value")
            anime = next(o["value"] for o in value_field["selectOptions"]
                         if str(o.get("name", "")).lower() == "anime")
            specs.append(("anime-type", "SeriesTypeSpecification", anime))
            specs.extend(("anime-root-" + str(root["id"]), "RootFolderSpecification", root["path"])
                         for root in data["rootfolder"] if "anime" in root["path"].lower())
        for suffix, implementation, value in specs:
            desired = rule(LABEL + "-" + suffix, tag, specification(schemas, implementation, value))
            existing = [r for r in data["autotagging"] if r["name"] == desired["name"]]
            if len(existing) > 1:
                raise RuntimeError("Duplicate managed auto-tagging rule")
            if not existing:
                actions.append((name, "autotagging", "POST", desired))
            elif not rule_equal(existing[0], desired):
                desired["id"] = existing[0]["id"]
                actions.append((name, "autotagging/" + str(desired["id"]), "PUT", desired))
        eligible = is_anime_series if name == "sonarr" else is_animation
        ids = [i["id"] for i in data[resource] if eligible(i) and tag not in i.get("tags", [])]
        if ids:
            actions.append((name, resource + "/editor", "PUT",
                            {resource + "Ids": ids, "tags": [tag], "applyTags": "add"}))
        nyaa = [i for i in data["indexer"] if "nyaa" in i["name"].lower()]
        if len(nyaa) != 1:
            raise RuntimeError("Expected exactly one Nyaa indexer in " + name)
        indexer = copy.deepcopy(nyaa[0])
        if tag not in indexer.get("tags", []):
            indexer["tags"] = sorted(set(indexer.get("tags", []) + [tag]))
            actions.append((name, "indexer/" + str(indexer["id"]), "PUT", indexer))
    result = {"changed": bool(actions), "mode": "apply" if args.apply else "check",
              "actions": [{"service": n, "endpoint": path, "method": method}
                          for n, path, method, body in actions]}
    if not args.apply or not actions:
        print(json.dumps(result))
        return
    if os.geteuid() != 0:
        raise RuntimeError("Apply must run as root to protect rollback snapshots")
    directory = Path(args.backup_root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    backup = directory / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + ".json")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(snapshot, stream)
        stream.flush()
        os.fsync(stream.fileno())
    result["backup"] = str(backup)
    tag_ids = {}
    for name, path, method, body in actions:
        body = copy.deepcopy(body)
        if name in tag_ids and "tags" in body:
            body["tags"] = [tag_ids[name] if t == -1 else t for t in body["tags"]]
        response = apis[name].call(path, method, body)
        if path == "tag":
            tag_ids[name] = response["id"]
    command = p.call("command", "POST", {"name": "ApplicationIndexerSync", "forceSync": True})
    deadline = time.monotonic() + 90
    while True:
        status = p.call("command/" + str(command["id"]))
        if status["status"] == "failed":
            raise RuntimeError("Prowlarr indexer sync failed; rollback snapshot " + str(backup))
        if status["status"] == "completed":
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Prowlarr sync timed out; rollback snapshot " + str(backup))
        time.sleep(2)
    # A forced full sync must preserve user tags on the target indexers.
    for name in ("sonarr", "radarr"):
        data = snapshot["apps"][name]
        tag = tag_ids.get(name) or next(t["id"] for t in data["tag"] if t["label"] == LABEL)
        indexers = apis[name].call("indexer")
        nyaa = [i for i in indexers if "nyaa" in i["name"].lower()]
        if len(nyaa) != 1 or tag not in nyaa[0].get("tags", []):
            raise RuntimeError("Nyaa scope was not preserved by full sync in " + name)
        adult_names = {"hf", "hf (prowlarr)", "happyfappy", "happyfappy (prowlarr)"}
        if any(i["name"].strip().lower() in adult_names for i in indexers):
            raise RuntimeError("Adult tracker remains after full sync in " + name)
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
