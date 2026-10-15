#!/usr/bin/env python3
"""Promote Emby favorites to named *arr profiles, with a durable bounded search queue."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

PROFILE = "Favorites - UHD + Original"


class API:
    def __init__(self, base, key, header="X-Api-Key"):
        self.base, self.key, self.header = base.rstrip("/"), key, header

    def call(self, path, method="GET", body=None) -> Any:
        request = urllib.request.Request(self.base + "/" + path,
            data=None if body is None else json.dumps(body).encode(), method=method,
            headers={self.header: self.key, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise RuntimeError("API request failed: HTTP " + str(error.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("API request failed: connection error") from None


def clients(root):
    result = {}
    for name, stack, port in (("sonarr", "sonarr-tv", 8989), ("radarr", "radarr-movie", 7878)):
        config = ET.parse(root / stack / "config/config.xml").getroot()
        key = config.findtext("ApiKey")
        if not key:
            raise RuntimeError("Missing " + name + " API key")
        result[name] = API("http://127.0.0.1:" + str(port)
            + (config.findtext("UrlBase") or "").rstrip("/") + "/api/v3", key)
    with sqlite3.connect("file:" + str(root / "ombi/config/OmbiSettings.db") + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("SELECT Content FROM GlobalSettings WHERE SettingsName = ?", ("EmbySettings",)).fetchall()
    if len(rows) != 1:
        raise RuntimeError("Expected exactly one Ombi EmbySettings record")
    settings = json.loads(rows[0][0])
    servers = settings.get("Servers", [])
    if settings.get("Enable") is False or len(servers) != 1:
        raise RuntimeError("Expected exactly one enabled Ombi Emby server")
    server = servers[0]
    if server.get("Enable") is False or not server.get("ApiKey"):
        raise RuntimeError("Missing enabled Ombi Emby server API key")
    host = str(server["Ip"]).rstrip("/")
    if "://" not in host:
        host = ("https://" if server.get("Ssl") else "http://") + host
    parsed = urllib.parse.urlsplit(host)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError("Unsupported Ombi Emby address")
    if not parsed.port and server.get("Port"):
        host += ":" + str(server["Port"])
    subdir = str(server.get("SubDir") or "").strip("/")
    result["emby"] = API(host + ("/" + subdir if subdir else ""), server["ApiKey"], "X-Emby-Token")
    return result


def favorite_titles(api):
    users = api.call("Users")
    if not isinstance(users, list) or not users:
        raise RuntimeError("Emby returned no users")
    titles = {}
    for user in users:
        user_id = urllib.parse.quote(str(user["Id"]), safe="")
        start, expected, seen = 0, None, set()
        while True:
            query = urllib.parse.urlencode({"Recursive": "true", "IsFavorite": "true",
                "IncludeItemTypes": "Movie,Series,Season,Episode", "Fields": "ProviderIds",
                "StartIndex": start, "Limit": 200})
            page = api.call("Users/" + user_id + "/Items?" + query)
            total = page.get("TotalRecordCount")
            if not isinstance(total, int) or total < 0 or (expected is not None and total != expected):
                raise RuntimeError("Incomplete or changing Emby favorite listing")
            expected = total
            items = page.get("Items")
            if not isinstance(items, list):
                raise RuntimeError("Malformed Emby favorite listing")
            for item in items:
                item_id = str(item["Id"])
                if item_id in seen:
                    raise RuntimeError("Duplicate Emby favorite pagination item")
                seen.add(item_id)
                kind = item.get("Type")
                if kind in ("Season", "Episode"):
                    parent = item.get("SeriesId")
                    if not parent:
                        raise RuntimeError("Favorite child has no series identifier")
                    key = "Series:" + str(parent)
                    if key not in titles:
                        series = api.call("Users/" + user_id + "/Items/" + urllib.parse.quote(str(parent), safe=""))
                        if series.get("Type") != "Series":
                            raise RuntimeError("Favorite child resolved to non-series")
                        titles[key] = series
                elif kind in ("Movie", "Series"):
                    titles[kind + ":" + item_id] = item
                else:
                    raise RuntimeError("Unexpected favorite media type")
            start += len(items)
            if start == expected:
                break
            if not items or start > expected:
                raise RuntimeError("Truncated Emby favorite listing")
    return list(titles.values())


def provider_pairs(item):
    providers = {str(k).lower(): str(v).lower() for k, v in item.get("ProviderIds", {}).items() if v}
    permitted = ("tmdb", "imdb") if item["Type"] == "Movie" else ("tvdb", "imdb")
    return {(name, providers[name]) for name in permitted if name in providers}


def matches(titles, library):
    result, unmatched, ambiguous = {}, 0, 0
    for item in titles:
        app = "radarr" if item["Type"] == "Movie" else "sonarr"
        pairs = provider_pairs(item)
        candidates = []
        for record in library[app]:
            known = {(provider, str(record.get(field, "")).lower())
                     for provider, field in (("tmdb", "tmdbId"), ("tvdb", "tvdbId"), ("imdb", "imdbId"))
                     if record.get(field)}
            if pairs & known:
                candidates.append(record)
        if len(candidates) == 1:
            record = candidates[0]
            result[app + ":" + str(record["id"])] = (app, record)
        elif candidates:
            ambiguous += 1
        else:
            unmatched += 1
    return result, unmatched, ambiguous


def save_state(path, state):
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(apis, state_path, apply, limit):
    # Complete every external read before any profile or search mutation.
    titles = favorite_titles(apis["emby"])
    library, targets = {}, {}
    for name in ("sonarr", "radarr"):
        resource = "series" if name == "sonarr" else "movie"
        library[name] = apis[name].call(resource)
        profiles = [p for p in apis[name].call("qualityprofile") if p["name"] == PROFILE]
        if len(profiles) != 1:
            raise RuntimeError("Expected exactly one " + name + " profile named " + PROFILE)
        targets[name] = profiles[0]["id"]
    selected, unmatched, ambiguous = matches(titles, library)
    bootstrap = not state_path.exists()
    state = json.loads(state_path.read_text()) if not bootstrap else {"version": 1, "titles": {}}
    if state.get("version") != 1 or not isinstance(state.get("titles"), dict):
        raise RuntimeError("Invalid durable state; refusing to rebuild the queue")
    records = state["titles"]
    for key, (app, record) in selected.items():
        if key not in records:
            records[key] = {"app": app, "id": record["id"], "phase": "pending",
                            "originalProfileId": record["qualityProfileId"],
                            "priority": 0 if bootstrap else 1}
    active = 0
    for entry in records.values():
        if entry["phase"] == "dispatching":
            # An uncertain acceptance may still be running; reserve its slot.
            active += 1
        if entry["phase"] == "queued":
            status = apis[entry["app"]].call("command/" + str(entry["commandId"]))["status"]
            if status in ("queued", "started"):
                active += 1
            elif status == "completed":
                entry["phase"] = "complete"
            elif status in ("failed", "aborted", "cancelled"):
                entry["phase"] = "failed"
            else:
                raise RuntimeError("Unknown *arr search command state")
    pending = sorted((key for key in selected if records[key]["phase"] in ("pending", "profile-updated")),
                     key=lambda key: records[key].get("priority", 0), reverse=True)
    summary = {"favorites": len(titles), "matched": len(selected), "unmatched": unmatched,
        "ambiguous": ambiguous, "pending": len(pending), "activeSearches": active,
        "dispatching": sum(e["phase"] == "dispatching" for e in records.values()),
        "failed": sum(e["phase"] == "failed" for e in records.values()),
        "processed": 0, "mode": "apply" if apply else "check"}
    if not apply:
        return summary
    save_state(state_path, state)
    for key in pending[:max(0, limit - active)]:
        entry = records[key]
        app, record = selected[key]
        resource = "series" if app == "sonarr" else "movie"
        # The editor changes only the named property; paths and monitoring stay intact.
        if record["qualityProfileId"] != targets[app]:
            apis[app].call(resource + "/editor", "PUT",
                {resource + "Ids": [record["id"]], "qualityProfileId": targets[app]})
        entry["phase"] = "profile-updated"
        save_state(state_path, state)
        # Persist intent BEFORE dispatch: timeout/crash cannot repeat a whole-series search.
        # An ambiguous dispatch requires operator reconciliation, never an automatic retry.
        entry["phase"] = "dispatching"
        save_state(state_path, state)
        body = ({"name": "SeriesSearch", "seriesId": record["id"]} if app == "sonarr"
                else {"name": "MoviesSearch", "movieIds": [record["id"]]})
        response = apis[app].call("command", "POST", body)
        entry.update(phase="queued", commandId=response["id"])
        save_state(state_path, state)
        summary["processed"] += 1
    summary["pending"] = sum(key in selected and e["phase"] in ("pending", "profile-updated")
                             for key, e in records.items())
    return summary


def run_with_heartbeat(api_factory, state_path, limit):
    """Record attempts and progress without changing the durable queue schema."""
    heartbeat_path = state_path.parent / "heartbeat.json"
    heartbeat = json.loads(heartbeat_path.read_text()) if heartbeat_path.exists() else {}
    previous = json.loads(state_path.read_text()) if state_path.exists() else {"titles": {}}
    phases_before = {key: value.get("phase") for key, value in previous["titles"].items()}
    heartbeat.update(last_attempt_timestamp_seconds=time.time(), running=1)
    save_state(heartbeat_path, heartbeat)
    try:
        result = run(api_factory(), state_path, True, limit)
        current = json.loads(state_path.read_text())
        phases_after = {key: value.get("phase") for key, value in current["titles"].items()}
        heartbeat.update(last_success_timestamp_seconds=time.time(), last_result_success=1)
        if phases_after != phases_before or result["processed"]:
            heartbeat["last_progress_timestamp_seconds"] = time.time()
        return result
    except Exception:
        heartbeat["last_result_success"] = 0
        raise
    finally:
        heartbeat["running"] = 0
        save_state(heartbeat_path, heartbeat)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--state-root", default="/srv/swarm")
    parser.add_argument("--state-dir", default="/var/lib/emby-favorite-upgrades")
    parser.add_argument("--max-searches", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.max_searches <= 10:
        parser.error("--max-searches must be between 1 and 10")
    directory = Path(args.state_dir)
    if args.apply:
        if os.geteuid() != 0:
            raise RuntimeError("Apply must run as root")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        with open(directory / "lock", "a") as lock:
            os.chmod(directory / "lock", 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = run_with_heartbeat(lambda: clients(Path(args.state_root)), directory / "state.json", args.max_searches)
    else:
        result = run(clients(Path(args.state_root)), directory / "state.json", False, args.max_searches)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(json.dumps({"error": message}), file=sys.stderr)
        sys.exit(1)
