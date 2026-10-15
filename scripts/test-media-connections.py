#!/usr/bin/env python3
"""Offline connection preservation and failure-path tests."""
import copy
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("media_connections", Path(__file__).with_name("media-connections.py"))
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

CONFIG = {"arr_host": "arr.invalid", "prowlarr_host": "arr.invalid",
          "emby_host": "arr.invalid", "qbit_host": "qbit.invalid",
          "qbit_username": "private-user", "qbit_password": "private-password"}


def fields(values):
    return [{"name": key, "value": value} for key, value in values.items()]


class Runtime:
    def __init__(self):
        self.keys = {"sonarr": "sonarr-secret", "radarr": "radarr-secret", "emby": "emby-secret",
                     "prowlarr": "prowlarr-secret"}
        self.objects = {}
        self.collections = {}
        for app in ("sonarr", "radarr"):
            item = {"id": 1, "name": "qBittorrent", "implementation": "QBittorrent", "enable": True,
                    "tags": [7], "priority": 1,
                    "fields": fields({"host": "old.invalid", "port": 8080, "useSsl": False,
                         "urlBase": None, "username": "old-user", "password": "old-password",
                         "category": "preserve-" + app, "initialState": 0})}
            self.collections[app, "downloadclient"] = [item]
            self.objects[app, "downloadclient/1"] = item
        apps = []
        for app, number in (("radarr", 1), ("sonarr", 2)):
            item = {"id": number, "name": app.capitalize(), "implementation": app.capitalize(),
                    "syncLevel": "fullSync", "tags": [5],
                    "fields": fields({"prowlarrUrl": "http://old.invalid:9696",
                        "baseUrl": "http://old.invalid", "apiKey": "old-key",
                        "syncCategories": [2000, 5000], "animeSyncCategories": [5070],
                        "syncAnimeStandardFormatSearch": True})}
            apps.append(item)
            self.objects["prowlarr", "applications/" + str(number)] = item
        self.collections["prowlarr", "applications"] = apps
        self.objects["bazarr", "system/settings"] = {
            app: {"ip": "old.invalid", "port": 1, "ssl": False, "base_url": "",
                  "apikey": "old-key", "excluded_tags": [88], "only_monitored": False}
            for app in ("sonarr", "radarr")}
        self.objects["bazarr", "system/settings"]["general"] = {"wanted_search_frequency": 6, "providers": ["preserve"]}
        for app in ("sonarr", "radarr", "emby"):
            item = {"ip": "old.invalid", "port": 1, "ssl": False, "subDir": "",
                    "apiKey": "old-key", "enabled": True, "rootPathAnime": "3",
                    "qualityProfileAnime": "6", "addOnly": False, "id": 5}
            if app == "radarr":
                item = {"radarr": item, "radarr4K": {"enabled": False, "apiKey": "preserve"}}
            if app == "emby":
                item = {"enable": True, "servers": [item], "id": 1}
            self.objects["ombi", "Settings/" + app] = item
        self.preserved = {"sonarr": {"importlist": [{"enabled": True}], "indexer": [{"tags": [5]}]},
                          "radarr": {"qualityprofile": [{"cutoff": 19}]}}
        self.writes = []
        self.syncs = 0
        self.fail_write: int | None = None
        self.stale = False
        self.reads = 0

    def call(self, app, path):
        return copy.deepcopy(self.collections[app, path])

    def read(self, resource):
        self.reads += 1
        result = copy.deepcopy(self.objects[resource["app"], resource["path"]])
        if self.stale and self.reads > 8:
            result["concurrent"] = True
        return result

    def write(self, resource):
        if len(self.writes) == self.fail_write:
            raise RuntimeError("Injected API failure")
        self.writes.append(resource["name"])
        self.objects[resource["app"], resource["path"]] = copy.deepcopy(resource["desired"])

    def invariants(self):
        return copy.deepcopy(self.preserved)

    def force_sync(self):
        self.syncs += 1


class ConnectionsTest(unittest.TestCase):
    def test_check_is_read_only_and_summary_contains_no_credentials(self):
        runtime = Runtime()
        result = policy.reconcile(runtime, CONFIG)
        self.assertEqual(result["managed_connections"], 8)
        self.assertTrue(result["changed"])
        self.assertEqual(runtime.writes, [])
        self.assertNotIn(CONFIG["qbit_password"], json.dumps(result))
        self.assertNotIn(runtime.keys["sonarr"], json.dumps(result))

    def test_apply_preserves_unowned_settings_and_second_apply_is_noop(self):
        runtime = Runtime()
        before = copy.deepcopy(runtime.objects)
        with tempfile.TemporaryDirectory() as directory:
            result = policy.reconcile(runtime, CONFIG, True, directory)
            self.assertEqual(stat.S_IMODE(Path(result["backup"]).stat().st_mode), 0o600)
            self.assertEqual(len(runtime.writes), 8)
            self.assertEqual(runtime.syncs, 1)
            self.assertFalse(policy.reconcile(runtime, CONFIG, True, directory)["changed"])
        for app in ("sonarr", "radarr"):
            actual = runtime.objects[app, "downloadclient/1"]
            self.assertEqual(actual["tags"], [7])
            self.assertIn({"name": "category", "value": "preserve-" + app}, actual["fields"])
        self.assertEqual(runtime.objects["ombi", "Settings/radarr"]["radarr4K"],
                         before["ombi", "Settings/radarr"]["radarr4K"])
        self.assertEqual(runtime.objects["bazarr", "system/settings"]["general"],
                         before["bazarr", "system/settings"]["general"])

    def test_missing_secret_and_ambiguous_target_refuse_without_writes(self):
        runtime = Runtime()
        with self.assertRaisesRegex(RuntimeError, "required"):
            policy.reconcile(runtime, {**CONFIG, "qbit_password": ""}, True)
        runtime.collections["sonarr", "downloadclient"] *= 2
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            policy.reconcile(runtime, CONFIG, True)
        self.assertEqual(runtime.writes, [])

    def test_schema_failure_preflights_every_service_before_writes(self):
        runtime = Runtime()
        del runtime.objects["ombi", "Settings/emby"]["servers"][0]["apiKey"]
        with self.assertRaisesRegex(RuntimeError, "schema"):
            policy.reconcile(runtime, CONFIG, True)
        self.assertEqual(runtime.writes, [])

    def test_stale_state_blocks_before_first_write(self):
        runtime = Runtime()
        runtime.stale = True
        with self.assertRaisesRegex(RuntimeError, "preflight"):
            policy.reconcile(runtime, CONFIG, True)
        self.assertEqual(runtime.writes, [])

    def test_empty_url_paths_are_equivalent_without_churn(self):
        current = {"subDir": None, "other": "keep"}
        self.assertEqual(policy.nested_patch(current, [], {"subDir": ""}), current)

    def test_fullsync_permits_only_managed_remote_url_and_key(self):
        runtime = Runtime()
        data = policy.plan(runtime, CONFIG)
        original = {"id": 42, "tags": [5], "fields": fields({
            "baseUrl": "http://old.invalid:9696/42/",
            "apiKey": "old-secret", "categories": [5070]})}
        data["invariants"]["sonarr"]["indexer"] = [original]
        expected = policy.expected_after_sync(data, runtime.keys)
        item = expected["sonarr"]["indexer"][0]
        self.assertEqual(item["tags"], original["tags"])
        values = {f["name"]: f["value"] for f in item["fields"]}
        self.assertEqual(values, {"baseUrl": "http://arr.invalid:9696/42/",
                                 "apiKey": "prowlarr-secret", "categories": [5070]})
        self.assertEqual(data["invariants"]["sonarr"]["indexer"][0], original)

    def test_partial_api_failure_stops_and_preserves_private_snapshot(self):
        runtime = Runtime()
        runtime.fail_write = 1
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "Injected API failure"):
                policy.reconcile(runtime, CONFIG, True, directory)
            self.assertEqual(len(runtime.writes), 1)
            snapshots = list(Path(directory).glob("*.json"))
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(stat.S_IMODE(snapshots[0].stat().st_mode), 0o600)
        self.assertEqual(runtime.syncs, 0)


if __name__ == "__main__":
    unittest.main()
