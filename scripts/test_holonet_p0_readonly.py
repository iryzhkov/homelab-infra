#!/usr/bin/env python3
"""Synthetic-only tests for fixed GET dispatch, bounds and output privacy."""
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("probe", Path(__file__).with_name("holonet-p0-readonly.py"))
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
UID = "b" * 32
OTHER = "c" * 32
FUTURE = "2099-01-01T00:00:00+00:00"


class Response(io.BytesIO):
    status = 200


class Opener:
    def __init__(self, payload=None, fail=False):
        self.payload = payload or {}
        self.calls = []
        self.fail = fail

    def open(self, request, timeout):
        self.calls.append(request)
        if self.fail:
            raise urllib.error.URLError("private-credential-value")
        return Response(json.dumps(self.payload).encode())


class ProbeTests(unittest.TestCase):
    def test_fixed_get_and_limits(self):
        opener = Opener()
        reader = probe.Reader("SYNTHETIC_SECRET", opener, FUTURE)
        reader.get("Users/" + UID + "/Items", "sample", {"Limit": 25, "StartIndex": 25})
        self.assertEqual(opener.calls[0].get_method(), "GET")
        self.assertTrue(opener.calls[0].full_url.startswith(probe.BASE))
        for path, query in [("Items/Delete", {}), ("Users/" + UID + "/Policy", {}),
                            ("http://evil.test", {}), ("Plugins", {"api_key": "x"}),
                            ("Users/" + UID + "/Items", {"Limit": 26}),
                            ("Users/" + UID + "/Items", {"StartIndex": 50})]:
            with self.subTest(path=path, query=query), self.assertRaises(probe.Refused):
                reader.get(path, "forbidden", query)
        self.assertEqual(len(opener.calls), 1)
        self.assertNotIn("SYNTHETIC_SECRET", json.dumps(reader.ledger))

    def test_budget_and_deadline_before_dispatch(self):
        opener = Opener()
        reader = probe.Reader("SYNTHETIC_SECRET", opener, FUTURE)
        reader.requests = 200
        with self.assertRaises(probe.Refused):
            reader.get("Plugins", "budget")
        reader = probe.Reader("SYNTHETIC_SECRET", opener, "2000-01-01T00:00:00+00:00")
        with self.assertRaises(probe.Refused):
            reader.get("Plugins", "deadline")
        self.assertEqual(opener.calls, [])
        reader = probe.Reader("SYNTHETIC_SECRET", opener, FUTURE, prior_requests=199)
        reader.get("Plugins", "last-request")
        with self.assertRaises(probe.Refused):
            reader.get("Plugins", "over-budget")
        self.assertEqual(len(opener.calls), 1)

    def test_no_redirect_or_exception_details(self):
        with self.assertRaisesRegex(probe.Refused, "redirect_refused"):
            probe.NoRedirect().redirect_request(None, None, 302, "secret", {}, "http://evil.test")
        reader = probe.Reader("SYNTHETIC_SECRET", Opener(fail=True), FUTURE)
        with self.assertRaises(probe.Refused) as error:
            reader.get("Plugins", "error")
        self.assertEqual(str(error.exception), "backend_read_failed")
        self.assertNotIn("private-credential-value", json.dumps(reader.ledger))

    def test_response_size_bound(self):
        reader = probe.Reader("SYNTHETIC_SECRET", Opener({"blob": "x" * 100}), FUTURE)
        with patch.object(probe, "MAX_RESPONSE", 16), self.assertRaisesRegex(probe.Refused, "response_budget"):
            reader.get("Plugins", "size")

    def test_summary_excludes_titles_paths_configuration_and_history_values(self):
        marker = "PRIVATE_MARKER"
        payload = {"TotalRecordCount": 1, "Items": [{"Id": "123", "Name": marker, "Path": marker,
                   "Configuration": {"ProfilePin": marker}, "ProviderIds": {"tvdb": marker},
                   "UserData": {"Played": True, "PlayCount": 7, "LastPlayedDate": marker,
                                "PlaybackPositionTicks": 30, "IsFavorite": True}}]}
        result = probe.summarize(payload)
        self.assertNotIn(marker, json.dumps(result))
        self.assertEqual(result["played_true"], 1)
        self.assertEqual(result["id_shapes"], ["decimal-string"])

    def test_complete_probe_uses_authorized_user_context_and_sanitizes(self):
        marker = "PRIVATE_MARKER"
        class Fake:
            def __init__(self):
                self.calls = []
            def get(self, path, label, query=None, authenticated=True):
                self.calls.append((path, query))
                if path == "System/Info/Public":
                    return {"Id": probe.SERVER, "Version": "4.10.1.0", "Private": marker}
                if path == "System/Info":
                    return {"Version": "4.10.1.0", "Paths": marker}
                if path == "Plugins":
                    return [{"Name": "Playback Reporting", "Version": "1", "Configuration": marker}]
                if path == "Users/Public":
                    return [{"Id": UID, "Name": "Igor"}, {"Id": "d"*32, "Name": marker}]
                if path == "Users":
                    return [{"Id": OTHER, "Name": "TV", "Configuration": {"ProfilePin": marker}}]
                if path in ["Users/" + UID, "Users/" + OTHER]:
                    return {"Name": "Igor" if path.endswith(UID) else "TV", "Policy": {"IsAdministrator": False}, "Configuration": {"ProfilePin": marker}}
                if path.endswith("/Views"):
                    return {"Items": [{"Id": "42", "Name": "Movies", "Type": "CollectionFolder"}]}
                if path.endswith("/HomeSections"):
                    return [{"Id": "row1", "SectionType": "items", "CustomName": marker, "Query": {"private": marker}}]
                return {"Items": [{"Id": "1", "Type": "Movie", "Name": marker, "Path": marker,
                                  "UserData": {"LastPlayedDate": marker}}], "TotalRecordCount": 1}
        fake = Fake()
        self_outer = self
        class Bridge:
            def open(self, request, timeout):
                if request.get_method() != "GET":
                    raise AssertionError("mutating request")
                parsed = urllib.parse.urlsplit(request.full_url)
                path = parsed.path.removeprefix("/emby/")
                query = urllib.parse.parse_qs(parsed.query)
                if "ParentId" in query:
                    self_outer.assertIn("UserDataPlayCount", query["Fields"][0])
                    self_outer.assertIn("UserDataLastPlayedDate", query["Fields"][0])
                return Response(json.dumps(fake.get(path, "bridge")).encode())
        reader = probe.Reader("SYNTHETIC_SECRET", Bridge(), FUTURE)
        result = probe.probe(reader)
        self.assertNotIn(marker, json.dumps(result))
        for path, query in fake.calls:
            if path.startswith("Users/") and path != "Users/Public":
                self.assertIn(path.split("/")[1], [UID, OTHER])
        self.assertEqual(result["profiles"]["Igor"]["home_rows"][0]["name"], "other")
        self.assertLess(len(fake.calls), 40)

    def test_history_aggregate_is_scoped_and_mutating_gets_refuse(self):
        opener = Opener({"0-00": 1})
        reader = probe.Reader("SYNTHETIC_SECRET", opener, FUTURE)
        result = probe.history_report(reader, UID, "Igor")
        self.assertEqual(result["positive_buckets"], 1)
        self.assertNotIn(UID, json.dumps(result))
        for path in ("user_usage_stats/load_backup", "user_usage_stats/save_backup", "user_usage_stats/user_manage/remove_unknown/x"):
            with self.assertRaises(probe.Refused):
                reader.get(path, "forbidden")
        with self.assertRaises(probe.Refused):
            reader.get("user_usage_stats/HourlyReport", "unscoped", {"days": 7})
        with self.assertRaises(probe.Refused):
            probe.history_report(reader, UID, "unauthorized")
        with self.assertRaises(probe.Refused):
            reader.get("user_usage_stats/HourlyReport", "arbitrary-filter", {"user_id": UID, "days": 7, "end_date": "2026-10-20", "filter": "Movie\\\' OR 1=1"})
        self.assertEqual(len(opener.calls), 1)

    def test_invalid_identifier_refuses(self):
        for value in [True, "../x", "a?api_key=x", ""]:
            with self.assertRaises(probe.Refused):
                probe.opaque(value)


if __name__ == "__main__":
    unittest.main()
