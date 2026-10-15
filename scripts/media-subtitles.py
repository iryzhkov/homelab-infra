#!/usr/bin/env python3
"""Reconcile English subtitles without downloading media or subtitles."""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROFILE_NAME = "English full (original and dual audio)"
MANUAL_HOURS = 876000


class API:
    def __init__(self, state_root):
        config = Path(state_root) / "bazarr/config/config/config.yaml"
        self.config = config
        text = config.read_text()
        auth = re.search(r"(?m)^auth:\n((?:[ \t].*\n)*)", text)
        key = re.search(r"(?m)^\s+apikey:\s*['\"]?([a-fA-F0-9]{32})['\"]?\s*$",
                        auth.group(1) if auth else "")
        if not key:
            raise RuntimeError("Expected Bazarr API key in its protected auth section")
        base = re.search(r"(?m)^  base_url:\s*(.*)$", text)
        base_url = (base.group(1).strip().strip("'\"") if base else "").rstrip("/")
        self.base = "http://127.0.0.1:6767" + base_url + "/api/"
        self.key = key.group(1)

    def call(self, path, form=None) -> Any:
        body = None if form is None else urllib.parse.urlencode(form, doseq=True).encode()
        req = urllib.request.Request(self.base + path, data=body,
                headers={"X-API-KEY": self.key,
                         "Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=900) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raise RuntimeError(path.split("?")[0] + " returned HTTP " + str(error.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("Bazarr request failed: " + path.split("?")[0]) from None


def desired_profile(profile_id):
    # Excluding matching English audio would also exclude Japanese+English files.
    return {"profileId": profile_id, "name": PROFILE_NAME, "cutoff": 1,
            "items": [{"id": 1, "language": "en", "forced": "False", "hi": "False",
                       "audio_exclude": "False", "audio_only_include": "False"}],
            "mustContain": [], "mustNotContain": [], "originalFormat": False, "tag": None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--state-root", default="/srv/swarm")
    parser.add_argument("--backup-root", default="/var/backups/media-subtitles")
    parser.add_argument("--providers", nargs="*", choices=["yifysubtitles", "gestdown"], default=[])
    parser.add_argument("--automatic-search", action="store_true",
                        help="Explicitly enable six-hour wanted searches after reviewing candidates")
    parser.add_argument("--movie-candidate", type=int)
    parser.add_argument("--episode-candidate", type=int)
    args = parser.parse_args()
    api = API(args.state_root)
    settings = api.call("system/settings")
    profiles = api.call("system/languages/profiles")
    movies = api.call("movies")["data"]
    shows = api.call("series")["data"]
    matches = [p for p in profiles if p["name"] == PROFILE_NAME]
    if len(matches) > 1:
        raise RuntimeError("Duplicate managed subtitle profile")
    profile_id = matches[0]["profileId"] if matches else max([p["profileId"] for p in profiles] + [0]) + 1
    profile = desired_profile(profile_id)
    new_profiles = [profile if p["profileId"] == profile_id else p for p in profiles]
    if not matches:
        new_profiles.append(profile)
    form = {}
    if profiles != new_profiles:
        form["languages-profiles"] = json.dumps(new_profiles)
    general = settings["general"]
    wanted = {"use_embedded_subs": True, "movie_default_enabled": True,
              "movie_default_profile": profile_id, "serie_default_enabled": True,
              "serie_default_profile": profile_id,
              "wanted_search_frequency": 6 if args.automatic_search else MANUAL_HOURS,
              "wanted_search_frequency_movie": 6 if args.automatic_search else MANUAL_HOURS,
              "upgrade_subs": False}
    if args.providers:
        wanted["enabled_providers"] = sorted(set(general["enabled_providers"] + args.providers))
    for key, value in wanted.items():
        current = general.get(key)
        same = current == value or (key.endswith("_profile") and str(current) == str(value))
        if not same:
            form["settings-general-" + key] = str(value).lower() if isinstance(value, bool) else value
    # An enabled language allows both embedded and external English tracks to count.
    languages = api.call("system/languages")
    enabled = [l["code2"] for l in languages if l.get("enabled")]
    if "en" not in enabled:
        form["languages-enabled"] = sorted(set(enabled + ["en"]))
    changed_movies = [m["radarrId"] for m in movies if m["profileId"] != profile_id]
    changed_shows = [s["sonarrSeriesId"] for s in shows if s["profileId"] != profile_id]
    result = {"changed": bool(form or changed_movies or changed_shows),
              "profileId": profile_id, "movie_assignments": len(changed_movies),
              "series_assignments": len(changed_shows), "settings": sorted(form),
              "automatic_search": args.automatic_search,
              "providers": wanted.get("enabled_providers", general["enabled_providers"])}
    if args.apply and result["changed"]:
        if os.geteuid() != 0:
            raise RuntimeError("Apply requires root for protected rollback snapshots")
        backup = Path(args.backup_root) / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        backup.mkdir(mode=0o700, parents=True)
        os.chmod(backup, 0o700)
        for name, data in [("config.yaml", api.config.read_text()),
                           ("state.json", json.dumps({"profiles": profiles, "general": general,
                            "movies": movies, "series": shows}))]:
            fd = os.open(backup / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as stream:
                stream.write(data)
        source = Path(args.state_root) / "bazarr/config/db/bazarr.db"
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(backup / "bazarr.db") as dst:
                src.backup(dst)
        os.chmod(backup / "bazarr.db", 0o600)
        result["backup"] = str(backup)
        if form:
            api.call("system/settings", form)
        for endpoint, id_key, ids in [("movies", "radarrid", changed_movies),
                                      ("series", "seriesid", changed_shows)]:
            for offset in range(0, len(ids), 20):
                batch = ids[offset:offset + 20]
                api.call(endpoint, {id_key: batch, "profileid": [profile_id] * len(batch)})
        actual = api.call("system/languages/profiles")
        if not any(p["profileId"] == profile_id and p["items"] == profile["items"] for p in actual):
            raise RuntimeError("Subtitle profile verification failed")
    # These GET endpoints list candidates; downloads require separate POST requests.
    if args.movie_candidate or args.episode_candidate:
        result["candidates"] = []
        for kind, item_id in [("movies", args.movie_candidate), ("episodes", args.episode_candidate)]:
            if item_id is None:
                continue
            param = "radarrid" if kind == "movies" else "episodeid"
            rows = api.call("providers/" + kind + "?" + param + "=" + str(item_id))["data"]
            result["candidates"].append({"kind": kind, "id": item_id, "count": len(rows),
                "providers": sorted(set(r["provider"] for r in rows)),
                "top": [{"provider": r["provider"], "score": r["score"],
                         "language": r["language"], "forced": r["forced"]} for r in rows[:3]]})
        result["provider_status"] = api.call("providers")["data"]
    print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as error:
        print("subtitle reconciliation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
