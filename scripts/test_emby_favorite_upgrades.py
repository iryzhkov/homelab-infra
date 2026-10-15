#!/usr/bin/env python3
"""Offline behavioral checks; no services or credentials are accessed."""
import copy
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import urllib.parse

MODULE = runpy.run_path(str(Path(__file__).with_name("emby-favorite-upgrades.py")), run_name="test")
run = MODULE["run"]
PROFILE = MODULE["PROFILE"]


class FakeAPI:
    def __init__(self, app):
        self.app = app
        self.writes = []
        self.fail_favorites = False
        self.fail_dispatch = False
        self.status = "queued"
        self.favorites = [{"Id": str(i), "Type": "Movie", "ProviderIds": {"Tmdb": str(i)}}
                          for i in range(1, 5)]
        self.library = [{"id": i, "tmdbId": i, "qualityProfileId": 1, "monitored": False}
                        for i in range(1, 5)]

    def call(self, path, method="GET", body=None):
        if method != "GET":
            assert body is not None
            self.writes.append((path, method, copy.deepcopy(body)))
            if path == "command":
                if self.fail_dispatch:
                    raise RuntimeError("ambiguous timeout")
                return {"id": len(self.writes)}
            for item in self.library:
                if item["id"] in body.get("movieIds", body.get("seriesIds", [])):
                    item["qualityProfileId"] = body["qualityProfileId"]
            return None
        if path == "Users":
            return [{"Id": "user-a"}, {"Id": "user-b"}]
        if "/Items?" in path:
            if self.fail_favorites and "user-b" in path:
                raise RuntimeError("user favorites failure")
            query = urllib.parse.parse_qs(path.partition("?")[2])
            start = int(query["StartIndex"][0])
            return {"TotalRecordCount": len(self.favorites), "Items": copy.deepcopy(self.favorites[start:start+200])}
        if path.endswith("/Items/series-1"):
            return {"Id": "series-1", "Type": "Series", "ProviderIds": {"Tvdb": "10"}}
        if path in ("movie", "series"):
            return copy.deepcopy(self.library)
        if path == "qualityprofile":
            return [{"id": 7, "name": PROFILE}]
        if path.startswith("command/"):
            return {"status": self.status}
        raise AssertionError(path)


class FavoriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self.apis = {a: FakeAPI(a) for a in ("emby", "sonarr", "radarr")}
        self.apis["sonarr"].library = []

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_only_preview(self):
        result = run(self.apis, self.path, False, 2)
        self.assertEqual(result["pending"], 4)
        self.assertFalse(self.path.exists())
        self.assertFalse(any(a.writes for a in self.apis.values()))

    def test_fail_closed_for_any_user(self):
        self.apis["emby"].fail_favorites = True
        with self.assertRaises(RuntimeError):
            run(self.apis, self.path, True, 2)
        self.assertFalse(self.path.exists())
        self.assertFalse(any(a.writes for a in self.apis.values()))

    def test_budget_active_commands_and_idempotency(self):
        result = run(self.apis, self.path, True, 2)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        result = run(self.apis, self.path, True, 2)
        self.assertEqual(result["processed"], 0)
        self.apis["radarr"].status = "completed"
        self.assertEqual(run(self.apis, self.path, True, 2)["processed"], 2)
        self.assertEqual(run(self.apis, self.path, True, 2)["processed"], 0)
        self.assertTrue(all(not i["monitored"] for i in self.apis["radarr"].library))
        self.assertEqual(len([w for w in self.apis["radarr"].writes if w[0] == "command"]), 4)

    def test_ambiguous_dispatch_does_not_retry(self):
        self.apis["radarr"].fail_dispatch = True
        with self.assertRaises(RuntimeError):
            run(self.apis, self.path, True, 2)
        state = json.loads(self.path.read_text())
        self.assertEqual(state["titles"]["radarr:1"]["phase"], "dispatching")
        self.apis["radarr"].fail_dispatch = False
        run(self.apis, self.path, True, 2)
        attempts = [w for w in self.apis["radarr"].writes
                    if w[0] == "command" and w[2]["movieIds"] == [1]]
        self.assertEqual(len(attempts), 1)

    def test_unfavorite_does_not_downgrade_or_process_backlog(self):
        run(self.apis, self.path, True, 2)
        writes = len(self.apis["radarr"].writes)
        self.apis["emby"].favorites = []
        self.apis["radarr"].status = "completed"
        self.assertEqual(run(self.apis, self.path, True, 2)["processed"], 0)
        self.assertEqual(len(self.apis["radarr"].writes), writes)

    def test_episode_favorite_promotes_whole_series(self):
        self.apis["emby"].favorites = [{"Id": "episode-1", "Type": "Episode", "SeriesId": "series-1"}]
        self.apis["sonarr"].library = [{"id": 11, "tvdbId": 10, "qualityProfileId": 1}]
        result = run(self.apis, self.path, True, 2)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(self.apis["sonarr"].writes[-1][2], {"name": "SeriesSearch", "seriesId": 11})

    def test_new_favorites_take_priority_over_bootstrap(self):
        run(self.apis, self.path, True, 2)
        self.apis["radarr"].status = "completed"
        self.apis["emby"].favorites.append({"Id": "5", "Type": "Movie", "ProviderIds": {"Tmdb": "5"}})
        self.apis["radarr"].library.append({"id": 5, "tmdbId": 5, "qualityProfileId": 1})
        run(self.apis, self.path, True, 2)
        commands = [w[2]["movieIds"] for w in self.apis["radarr"].writes if w[0] == "command"]
        self.assertEqual(commands, [[1], [2], [5], [3]])

    def test_conflicting_provider_identifiers_skip(self):
        self.apis["emby"].favorites = [{"Id": "x", "Type": "Movie",
                                       "ProviderIds": {"Tmdb": "1", "Imdb": "tt2"}}]
        self.apis["radarr"].library[1]["imdbId"] = "tt2"
        result = run(self.apis, self.path, True, 2)
        self.assertEqual(result["ambiguous"], 1)
        self.assertEqual(result["processed"], 0)


if __name__ == "__main__":
    unittest.main()
