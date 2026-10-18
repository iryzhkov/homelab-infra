#!/usr/bin/env python3
"""Offline classification and credential-exclusion checks for manual pack discovery."""
import copy
import json
from pathlib import Path
import runpy
import unittest
import urllib.parse

M = runpy.run_path(str(Path(__file__).with_name("media-anime-packs.py")), run_name="test")
classify = M["classify"]
discover = M["discover"]
SERIES = {"id": 90, "title": "Chainsaw Man", "year": 2022, "seriesType": "anime",
          "alternateTitles": [{"title": "チェンソーマン"}], "seasons": [{"seasonNumber": 1}]}


class FakeAPI:
    key = "PRIVATE-API-KEY"

    def __init__(self, kind, fail=False):
        self.kind, self.fail, self.calls = kind, fail, []

    def get(self, endpoint):
        self.calls.append(endpoint)
        if self.kind == "sonarr":
            return copy.deepcopy(SERIES)
        if endpoint == "indexer":
            return [{"id": 13, "name": "SeedPool", "enable": True},
                    {"id": 6, "name": "Nyaa.si", "enable": True}]
        query = urllib.parse.parse_qs(endpoint.partition("?")[2])
        if self.fail and query["indexerIds"] == ["6"]:
            raise RuntimeError("API request returned HTTP 503")
        return [
            {"title": "Chainsaw Man (2022) S01 1080p WEB", "size": 100, "seeders": 5,
             "downloadUrl": "https://private/download?apikey=PRIVATE-API-KEY", "infoHash": "SECRET-HASH"},
            {"title": "Chainsaw Man S01E01", "size": 10, "seeders": 2},
            {"title": "Chainsaw Man Movie (2025)", "size": 200, "seeders": 5},
            {"title": "Chainsaw Man [01-12] [1080p]", "size": 150, "seeders": 3},
            {"title": "Chainsaw Man S01 PRIVATE-API-KEY abcdef0123456789abcdef0123456789abcdef0123 https://private/secret?apikey=x",
             "size": 99, "seeders": 1}
        ]


class PackTests(unittest.TestCase):
    def test_explicit_season_is_hint_not_automatic_acceptance(self):
        c = classify("Chainsaw Man (2022) S01 1080p", SERIES, 1)
        self.assertEqual(c["release_kind"], "season_pack")
        self.assertEqual(c["season_status"], "explicit_requested_season")
        self.assertIn("audio_quality_size_and_identity_require_manual_review", c["identity_review_flags"])

    def test_episode_and_movie_are_not_season_packs(self):
        self.assertEqual(classify("Chainsaw Man S01E01", SERIES, 1)["release_kind"], "episode")
        self.assertEqual(classify("Chainsaw Man Movie (2025)", SERIES, 1)["release_kind"], "movie_or_special")

    def test_absolute_batch_does_not_invent_season(self):
        c = classify("チェンソーマン [01-12] Complete", SERIES, 1)
        self.assertEqual(c["release_kind"], "absolute_or_complete_batch")
        self.assertEqual(c["season_status"], "season_unspecified")
        self.assertNotIn("title_identity_unconfirmed", c["identity_review_flags"])

    def test_wrong_year_title_or_season_stays_flagged(self):
        c = classify("Different Show (2014) S02", SERIES, 1)
        self.assertIn("title_identity_unconfirmed", c["identity_review_flags"])
        self.assertIn("release_year_differs_from_series_premiere", c["identity_review_flags"])
        self.assertEqual(c["season_status"], "other_or_multiple_seasons")

    def test_two_canonical_get_queries_and_whitelisted_output(self):
        sonarr, prowlarr = FakeAPI("sonarr"), FakeAPI("prowlarr")
        result = discover(sonarr, prowlarr, 90, 1)
        calls = [p for p in prowlarr.calls if p.startswith("search?")]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(urllib.parse.parse_qs(p.partition("?")[2])["query"] == ["Chainsaw Man"] for p in calls))
        self.assertEqual(len(result["packs"]), 6)
        self.assertTrue(result["manual_review_only"])
        serialized = json.dumps(result)
        for secret in ["PRIVATE-API-KEY", "https://", "SECRET-HASH", "downloadUrl", "infoHash", "apikey=x", "abcdef0123456789abcdef0123456789abcdef0123"]:
            self.assertNotIn(secret, serialized)
        self.assertTrue(all(p["release_kind"] != "episode" for p in result["packs"]))

    def test_partial_failure_is_explicit(self):
        result = discover(FakeAPI("sonarr"), FakeAPI("prowlarr", fail=True), 90, 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0]["indexer"], "Nyaa.si")
        self.assertEqual(len(result["tracker_results"]), 1)
        self.assertTrue(result["packs"])


if __name__ == "__main__":
    unittest.main()
