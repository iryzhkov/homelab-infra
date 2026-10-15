#!/usr/bin/env python3
"""Offline preservation, concurrency and idempotency checks."""
import copy
import importlib.util
from pathlib import Path
import stat
import tempfile
import unittest
from typing import Any

spec = importlib.util.spec_from_file_location(
    "ombi_anime_settings", Path(__file__).with_name("ombi-anime-settings.py"))
assert spec is not None and spec.loader is not None
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class FakeAPI:
    def __init__(self):
        self.settings: dict[str, Any] = {"enabled": True, "rootPathAnime": "1", "qualityProfileAnime": "4",
                         "qualityProfile": "4", "rootPath": "1", "apiKey": "private",
                         "futureField": {"nested": [1, 2]}, "id": 5}
        self.writes = []
        self.reads = 0
        self.concurrent = False

    def call(self, path, method="GET", body: Any = None) -> Any:
        if path == "Sonarr/Profiles":
            return [{"id": 6, "name": "Approved anime profile"}]
        if path == "Sonarr/RootFolders":
            return [{"id": 3, "path": "/mnt/shared/media/anime"}]
        if method == "POST":
            self.writes.append(copy.deepcopy(body))
            self.settings = copy.deepcopy(body)
            return True
        self.reads += 1
        if self.concurrent and self.reads == 2:
            self.settings["rootPath"] = "concurrent-change"
        return copy.deepcopy(self.settings)


class OmbiAnimeSettingsTest(unittest.TestCase):
    def test_check_preserves_everything_and_never_writes(self):
        api = FakeAPI()
        before = copy.deepcopy(api.settings)
        result = policy.reconcile(api, 6, 3, "/unused")
        self.assertEqual(set(result["changes"]), {"rootPathAnime", "qualityProfileAnime"})
        self.assertEqual(api.settings, before)
        self.assertEqual(api.writes, [])

    def test_apply_preserves_other_fields_and_is_idempotent(self):
        api = FakeAPI()
        before = copy.deepcopy(api.settings)
        with tempfile.TemporaryDirectory() as directory:
            result = policy.reconcile(api, 6, 3, directory, apply=True)
            self.assertEqual(len(api.writes), 1)
            expected = {**before, "rootPathAnime": "3", "qualityProfileAnime": "6"}
            self.assertEqual(api.settings, expected)
            self.assertEqual(stat.S_IMODE(Path(result["backup"]).stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(Path(directory).stat().st_mode), 0o700)
            second = policy.reconcile(api, 6, 3, directory, apply=True)
            self.assertFalse(second["changed"])
            self.assertEqual(len(api.writes), 1)

    def test_concurrent_change_refuses_before_write(self):
        api = FakeAPI()
        api.concurrent = True
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "changed during reconciliation"):
                policy.reconcile(api, 6, 3, directory, apply=True)
        self.assertEqual(api.writes, [])

    def test_invalid_target_refuses_before_write(self):
        api = FakeAPI()
        with self.assertRaisesRegex(RuntimeError, "quality profile"):
            policy.reconcile(api, 99, 3, "/unused", apply=True)
        with self.assertRaisesRegex(RuntimeError, "anime library"):
            policy.reconcile(api, 6, 1, "/unused", apply=True)
        self.assertEqual(api.writes, [])


if __name__ == "__main__":
    unittest.main()
