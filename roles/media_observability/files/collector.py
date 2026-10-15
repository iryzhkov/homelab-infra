#!/usr/bin/env python3
"""Read-only media operations metrics for the existing Alloy textfile collector."""
import argparse
import collections
import concurrent.futures
import datetime
import http.cookiejar
import importlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

FLOOR_BYTES = 21990232555520
MINIMUM_SEED_SECONDS = 864000
PHASES = ("pending", "profile-updated", "dispatching", "queued", "complete", "failed")


def timestamp(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.timestamp()
    except (ValueError, TypeError):
        return None


def emit(lines, name, value, **labels):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Non-numeric metric")
    if labels:
        label = "{" + ",".join(k + "=" + json.dumps(str(v)) for k, v in sorted(labels.items())) + "}"
    else:
        label = ""
    lines.append("media_" + name + label + " " + str(value))


def atomic_write(path, content, mode):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class API:
    def __init__(self, base, headers=None):
        self.base = base.rstrip("/") + "/"
        self.headers = headers or {}

    def get(self, endpoint):
        req = urllib.request.Request(self.base + endpoint, headers=self.headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)


def arr_api(root, directory, port, version):
    config = ET.parse(Path(root) / directory / "config/config.xml").getroot()
    key = config.findtext("ApiKey")
    if not key:
        raise ValueError("Missing API credential")
    return API("http://127.0.0.1:" + str(port) + (config.findtext("UrlBase") or "")
               + "/api/" + version, {"X-Api-Key": key})


def queue_metrics(lines, service, page, now):
    records = page["records"]
    if not isinstance(records, list):
        raise ValueError("Invalid queue response")
    total = page["totalRecords"]
    if total != len(records):
        raise ValueError("Queue response is incomplete")
    ages = [max(0, now - t) for item in records
            if (t := timestamp(item.get("added"))) is not None]
    emit(lines, "import_queue_items", len(records), service=service)
    emit(lines, "import_queue_warning_items",
         sum(bool(r.get("statusMessages")) or r.get("trackedDownloadStatus") in ("warning", "error")
             for r in records), service=service)
    emit(lines, "import_queue_oldest_seconds", max(ages, default=0), service=service)
    emit(lines, "import_queue_age_unknown_items", len(records) - len(ages), service=service)


def collect_arr(lines, service, root, now):
    directory, port, version = {"sonarr": ("sonarr-tv", 8989, "v3"),
                               "radarr": ("radarr-movie", 7878, "v3"),
                               "prowlarr": ("prowlarr", 9696, "v1"),
                               "lidarr": ("lidarr-music", 8686, "v1")}[service]
    api = arr_api(root, directory, port, version)
    health = api.get("health")
    if not isinstance(health, list):
        raise ValueError("Invalid health response")
    emit(lines, "health_issues", len(health), service=service)
    for level in ("warning", "error"):
        emit(lines, "health_issues_by_level", sum(str(h.get("type", "")).lower() == level for h in health),
             service=service, level=level)
    statuses = api.get("indexerstatus")
    emit(lines, "indexer_backoffs",
         sum((timestamp(s.get("disabledTill")) or 0) > now for s in statuses), service=service)
    if service in ("sonarr", "radarr", "lidarr"):
        queue_metrics(lines, service, api.get("queue?page=1&pageSize=10000&includeUnknownSeriesItems=true"
                                            "&includeUnknownMovieItems=true&includeUnknownArtistItems=true"), now)


def collect_bazarr(lines, root):
    text = (Path(root) / "bazarr/config/config/config.yaml").read_text()
    section = re.search(r"(?m)^auth:\n((?:[ \t].*\n)*)", text)
    key = re.search(r"(?m)^\s+apikey:\s*['\"]?([a-fA-F0-9]{32})['\"]?\s*$",
                    section.group(1) if section else "")
    if not key:
        raise ValueError("Missing API credential")
    api = API("http://127.0.0.1:6767/api", {"X-API-KEY": key.group(1)})
    emit(lines, "health_issues", len(api.get("system/health")["data"]), service="bazarr")
    for kind in ("movies", "episodes"):
        data = api.get(kind + "/wanted?start=0&length=1")
        emit(lines, "subtitles_missing_items", data["total"], kind=kind)
    providers = api.get("providers")["data"]
    emit(lines, "subtitle_provider_failures", sum(p.get("status") != "Good" for p in providers))


def jellystat_metrics(lines, settings, logs):
    if not isinstance(settings, dict) or not isinstance(logs, list):
        raise ValueError("Invalid Jellystat scheduler response")
    for task in ("PartialJellyfinSync", "JellyfinSync"):
        interval = settings.get(task, {}).get("Interval")
        emit(lines, "jellystat_scheduler_configured", int(isinstance(interval, (int, float)) and interval > 0),
             task=task)
    # The logging API returns only the last 50 runs: absence is unknown, never a healthy zero.
    successful = [timestamp(row.get("TimeRun")) for row in logs
                  if str(row.get("Result", "")).lower() in ("success", "successful", "completed", "2")
                  and ("sync" in str(row.get("Name", "")).lower())]
    successful = [t for t in successful if t is not None]
    emit(lines, "jellystat_sync_history_available", int(bool(successful)))
    if successful:
        emit(lines, "jellystat_sync_last_success_timestamp_seconds", max(successful))
    # Upstream exposes activity events through JWT Socket.IO, not an authenticated REST heartbeat.
    emit(lines, "jellystat_activity_monitor_freshness_known", 0)


def collect_jellystat(lines, root):
    key = (Path(root) / "jellystat/janitorr-api-key").read_text().strip()
    if not key:
        raise ValueError("Missing Jellystat credential")
    api = API("http://127.0.0.1:3012", {"x-api-token": key})
    jellystat_metrics(lines, api.get("api/getTaskSettings"), api.get("logs/getLogs"))


def favorite_metrics(lines, state, heartbeat):
    titles = state.get("titles")
    if state.get("version") != 1 or not isinstance(titles, dict):
        raise ValueError("Invalid favorite state")
    counts = collections.Counter(item.get("phase") for item in titles.values())
    if any(k not in PHASES for k in counts):
        raise ValueError("Unknown favorite phase")
    for phase in PHASES:
        emit(lines, "favorite_items", counts[phase], phase=phase)
    emit(lines, "favorite_heartbeat_known", int(bool(heartbeat)))
    for key in ("last_attempt_timestamp_seconds", "last_success_timestamp_seconds",
                "last_progress_timestamp_seconds", "running", "last_result_success"):
        if key in heartbeat:
            emit(lines, "favorite_" + key, heartbeat[key])


def collect_favorites(lines, state_dir):
    directory = Path(state_dir)
    heartbeat = directory / "heartbeat.json"
    favorite_metrics(lines, json.loads((directory / "state.json").read_text()),
                     json.loads(heartbeat.read_text()) if heartbeat.exists() else {})


def collect_seedpool(lines, config):
    snapshot = importlib.import_module("seedpool_policy").snapshot
    credentials = json.loads(Path(config["qbit_credentials_file"]).read_text())
    base = credentials["url"].rstrip("/")
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    form = urllib.parse.urlencode({"username": credentials["username"],
                                   "password": credentials["password"]}).encode()
    req = urllib.request.Request(base + "/api/v2/auth/login", data=form,
                                 headers={"Referer": base})
    with opener.open(req, timeout=10) as response:
        if response.read().strip() != b"Ok.":
            raise ValueError("qBittorrent authentication failed")
    with opener.open(base + "/api/v2/torrents/info", timeout=15) as response:
        torrents = json.load(response)
    data = snapshot(torrents, config["floor_bytes"], config["minimum_seed_seconds"])
    for key in ("current_active_bytes", "headroom_bytes", "below_minimum_count",
                "below_minimum_bytes", "classified_count", "active_count", "unknown_count"):
        emit(lines, "seedpool_" + key, data[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="/etc/media-observability/config.json")
    parser.add_argument("--stdout", action="store_true",
                        help="Print metrics without writing collector files (read-only validation)")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    now = time.time()
    state_file = Path(config["state_file"])
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    lines, status = [], {}
    sources = {
        "sonarr": lambda out: collect_arr(out, "sonarr", config["state_root"], now),
        "radarr": lambda out: collect_arr(out, "radarr", config["state_root"], now),
        "prowlarr": lambda out: collect_arr(out, "prowlarr", config["state_root"], now),
        "lidarr": lambda out: collect_arr(out, "lidarr", config["state_root"], now),
        "bazarr": lambda out: collect_bazarr(out, config["state_root"]),
        "jellystat": lambda out: collect_jellystat(out, config["state_root"]),
        "favorites": lambda out: collect_favorites(out, config["favorite_state_dir"]),
        "ombi": lambda out: API("http://127.0.0.1:3579/api/v1").get("Status"),
        "emby": lambda out: API("http://127.0.0.1:8096/emby/System/Info").get("Public"),
        "seedpool": lambda out: collect_seedpool(out, config),
    }
    def read_source(collect):
        partial = []
        collect(partial)
        return partial

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        pending = {name: executor.submit(read_source, collect) for name, collect in sources.items()}
        for name, future in pending.items():
            try:
                lines.extend(future.result())
                state[name] = now
                status[name] = 1
            except Exception:
                # Exceptions can contain URLs, tokens, usernames, paths or response bodies.
                status[name] = 0
            emit(lines, "source_up", status[name], service=name)
            emit(lines, "source_last_success_timestamp_seconds", state.get(name, 0), service=name)
    emit(lines, "seedpool_floor_bytes", config["floor_bytes"])
    emit(lines, "seedpool_minimum_seed_seconds", config["minimum_seed_seconds"])
    emit(lines, "collector_timestamp_seconds", now)
    content = "\n".join(lines) + "\n"
    if args.stdout:
        print(content, end="")
    else:
        atomic_write(state_file, json.dumps(state), 0o600)
        atomic_write(config["output_file"], content, 0o644)
        print(json.dumps({"event": "media_collection", "sources": status,
                          "success": all(status.values()), "timestamp": now}, sort_keys=True))


if __name__ == "__main__":
    main()
