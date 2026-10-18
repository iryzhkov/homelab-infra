#!/usr/bin/env python3
"""Reconcile only Ombi's anime Sonarr root and quality profile."""
import argparse
import copy
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any
import urllib.error
import urllib.request


class API:
    def __init__(self, settings_db):
        with sqlite3.connect(Path(settings_db).resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            rows = db.execute(
                "SELECT Content FROM GlobalSettings WHERE SettingsName = ?", ("OmbiSettings",)
            ).fetchall()
        if len(rows) != 1:
            raise RuntimeError("Expected exactly one OmbiSettings record")
        settings = json.loads(rows[0][0])
        key = settings.get("ApiKey")
        if not isinstance(key, str) or not key.strip():
            raise RuntimeError("Ombi API key is missing")
        self.key = key
        base_path = settings.get("BaseUrl") or ""
        if not isinstance(base_path, str) or (base_path and not base_path.startswith("/")):
            raise RuntimeError("Ombi BaseUrl must be an empty value or URL path")
        if any(c in base_path for c in ("?", "#", "\r", "\n")):
            raise RuntimeError("Ombi BaseUrl contains unsupported characters")
        self.base = "http://127.0.0.1:3579" + base_path.rstrip("/") + "/api/v1/"

    def call(self, path, method="GET", body=None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"ApiKey": self.key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise RuntimeError(method + " " + path + " returned HTTP " + str(error.code)) from None
        except urllib.error.URLError:
            raise RuntimeError("Ombi request failed: " + method + " " + path) from None


def desired_settings(current, profiles, roots, quality_profile_id, root_id):
    if not isinstance(current, dict) or current.get("enabled") is not True:
        raise RuntimeError("Ombi Sonarr integration must already be enabled")
    if not {"rootPathAnime", "qualityProfileAnime"}.issubset(current):
        raise RuntimeError("Ombi anime settings schema is incomplete")
    matches = [p for p in profiles if p.get("id") == quality_profile_id]
    if len(matches) != 1:
        raise RuntimeError("Requested Sonarr anime quality profile is missing or ambiguous")
    matching_roots = [r for r in roots if r.get("id") == root_id]
    if len(matching_roots) != 1 or matching_roots[0].get("path") != "/mnt/shared/media/anime":
        raise RuntimeError("Requested root is not the household anime library")
    result = copy.deepcopy(current)
    result["rootPathAnime"] = str(root_id)
    result["qualityProfileAnime"] = str(quality_profile_id)
    return result


def reconcile(api, quality_profile_id, root_id, backup_root, apply=False):
    current = api.call("Settings/sonarr")
    desired = desired_settings(
        current, api.call("Sonarr/Profiles"), api.call("Sonarr/RootFolders"),
        quality_profile_id, root_id,
    )
    fields = ("rootPathAnime", "qualityProfileAnime")
    changes = {key: {"before": current[key], "after": desired[key]}
               for key in fields if current[key] != desired[key]}
    result = {"changed": bool(changes), "mode": "apply" if apply else "check", "changes": changes}
    if not apply or not changes:
        return result

    # Prevent overwriting edits made since the plan, including unrelated settings.
    if api.call("Settings/sonarr") != current:
        raise RuntimeError("Ombi settings changed during reconciliation; rerun before applying")
    directory = Path(backup_root)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = directory / (timestamp + ".json")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(current, stream)
        stream.flush()
        os.fsync(stream.fileno())
    result["backup"] = str(backup)
    if api.call("Settings/sonarr", "POST", desired) is not True:
        raise RuntimeError("Ombi rejected the update; inspect the protected rollback snapshot")
    actual = api.call("Settings/sonarr")
    if actual != desired:
        raise RuntimeError("Ombi readback differs from the intended settings; inspect protected snapshot")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--quality-profile-id", type=int, default=6)
    parser.add_argument("--root-id", type=int, default=3)
    parser.add_argument("--settings-db", default="/srv/swarm/ombi/config/OmbiSettings.db")
    parser.add_argument("--backup-root", default="/var/backups/ombi-anime-settings")
    args = parser.parse_args()
    if args.apply and os.geteuid() != 0:
        raise RuntimeError("Apply must run as root to protect credential-bearing rollback snapshots")
    print(json.dumps(reconcile(API(args.settings_db), args.quality_profile_id,
                               args.root_id, args.backup_root, args.apply)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Never include response bodies or credential-bearing settings in errors.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(json.dumps({"error": message}), file=sys.stderr)
        sys.exit(1)
