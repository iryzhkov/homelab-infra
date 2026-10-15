#!/usr/bin/env python3
"""Failure-oriented unit tests; no network or live state changes."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collector = load("media_collector", "roles/media_observability/files/collector.py")
worker = load("media_worker", "scripts/emby-favorite-upgrades.py")


class CollectorTests(unittest.TestCase):
    def test_missing_queue_age_and_partial_pages_are_not_healthy_zero(self):
        lines = []
        collector.queue_metrics(lines, "sonarr",
            {"records": [{"added": None, "statusMessages": ["private title"]}], "totalRecords": 1}, 100)
        self.assertTrue(any("import_queue_age_unknown_items" in l and l.endswith(" 1") for l in lines))
        self.assertNotIn("private title", "\n".join(lines))
        with self.assertRaises(ValueError):
            collector.queue_metrics([], "sonarr", {"records": [], "totalRecords": 5}, 100)

    def test_arr_does_not_call_unsupported_indexerstatus(self):
        class ArrAPI:
            def get(self, endpoint):
                if endpoint == "health":
                    return []
                if endpoint.startswith("queue?"):
                    return {"records": [], "totalRecords": 0}
                raise AssertionError("Unsupported endpoint called: " + endpoint)
        with mock.patch.object(collector, "arr_api", return_value=ArrAPI()):
            lines = []
            collector.collect_arr(lines, "sonarr", "/unused", 100)
        self.assertTrue(any("import_queue_items" in line for line in lines))
        self.assertFalse(any("indexer_backoffs" in line for line in lines))

    def test_jellystat_history_and_activity_liveness_are_distinct(self):
        lines = []
        collector.jellystat_metrics(lines, {"PartialJellyfinSync": {"Interval": 60}},
            [{"Name": "Recently Added Sync", "Result": "Success", "TimeRun": "2026-10-15T01:00:00Z"},
             {"Name": "Full Jellyfin Sync", "Result": "Failed", "TimeRun": "2026-10-15T02:00:00Z"}])
        self.assertTrue(any("activity_monitor_freshness_known 0" in l for l in lines))
        good = [l for l in lines if "sync_last_success" in l]
        self.assertEqual(float(good[0].split()[-1]), collector.timestamp("2026-10-15T01:00:00Z"))
        missing = []
        collector.jellystat_metrics(missing, {}, [])
        self.assertFalse(any("sync_last_success" in l for l in missing))

    def test_missing_heartbeat_is_explicit_unknown(self):
        lines = []
        collector.favorite_metrics(lines, {"version": 1, "titles": {"private title": {"phase": "queued"}}}, {})
        self.assertTrue(any("favorite_heartbeat_known 0" in l for l in lines))
        self.assertNotIn("private title", "\n".join(lines))
        with self.assertRaises(ValueError):
            collector.favorite_metrics([], {"version": 1, "titles": {"x": {"phase": "new-unknown"}}}, {})

    def test_atomic_output_is_readable_to_alloy_and_private_state_stays_private(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "media.prom"
            collector.atomic_write(output, "media_source_up 1\n", 0o644)
            self.assertEqual(output.stat().st_mode & 0o777, 0o644)
            collector.atomic_write(output, "media_source_up 0\n", 0o644)
            self.assertEqual(output.read_text(), "media_source_up 0\n")
            self.assertEqual([p.name for p in Path(d).iterdir()], ["media.prom"])

    def test_partial_failure_emits_down_and_keeps_previous_success_without_secrets(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"state_file": d + "/state.json", "state_root": d,
                      "favorite_state_dir": d, "floor_bytes": 21990232555520,
                      "minimum_seed_seconds": 864000, "output_file": d + "/media.prom"}
            Path(config["state_file"]).write_text(json.dumps({"sonarr": 42}))
            cfg = Path(d) / "config.json"
            cfg.write_text(json.dumps(config))
            output = io.StringIO()
            def fail(*args):
                raise ValueError("https://secret-user:secret-password@private-tracker/?apikey=secret")
            with mock.patch.object(collector, "collect_arr", side_effect=fail), \
                 mock.patch.object(collector, "collect_bazarr", side_effect=lambda out, root: collector.emit(out, "health_issues", 0, service="bazarr")), \
                 mock.patch.object(collector, "collect_jellystat", side_effect=fail), \
                 mock.patch.object(collector, "collect_favorites", side_effect=fail), \
                 mock.patch.object(collector, "collect_seedpool", side_effect=fail), \
                 mock.patch.object(collector.API, "get", side_effect=fail), \
                 mock.patch("sys.argv", ["collector", "--config", str(cfg), "--stdout"]), \
                 contextlib.redirect_stdout(output):
                collector.main()
            text = output.getvalue()
            self.assertIn('media_source_up{service="sonarr"} 0', text)
            self.assertIn('media_source_last_success_timestamp_seconds{service="sonarr"} 42', text)
            self.assertIn('media_source_up{service="bazarr"} 1', text)
            self.assertNotIn("secret", text)
            self.assertFalse(Path(config["output_file"]).exists())
            self.assertEqual(json.loads(Path(config["state_file"]).read_text()), {"sonarr": 42})


class WorkerHeartbeatTests(unittest.TestCase):
    def test_failure_preserves_success_and_durable_queue(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            state.write_text('{"version":1,"titles":{}}')
            heartbeat = Path(d) / "heartbeat.json"
            heartbeat.write_text('{"last_success_timestamp_seconds":42}')
            with mock.patch.object(worker, "run", side_effect=RuntimeError("API failed")):
                with self.assertRaises(RuntimeError):
                    worker.run_with_heartbeat(lambda: {}, state, 2)
            data = json.loads(heartbeat.read_text())
            self.assertEqual(data["last_success_timestamp_seconds"], 42)
            self.assertEqual(data["running"], 0)
            self.assertEqual(data["last_result_success"], 0)
            self.assertEqual(json.loads(state.read_text()), {"version": 1, "titles": {}})
            self.assertEqual(heartbeat.stat().st_mode & 0o777, 0o600)

    def test_phase_transition_records_progress_without_schema_change(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / "state.json"
            state.write_text('{"version":1,"titles":{"x":{"phase":"queued"}}}')
            def complete(*args):
                state.write_text('{"version":1,"titles":{"x":{"phase":"complete"}}}')
                return {"processed": 0}
            with mock.patch.object(worker, "run", side_effect=complete):
                result = worker.run_with_heartbeat(lambda: {}, state, 2)
            heartbeat = json.loads((Path(d) / "heartbeat.json").read_text())
            self.assertEqual(result, {"processed": 0})
            self.assertEqual(heartbeat["running"], 0)
            self.assertEqual(heartbeat["last_result_success"], 1)
            self.assertGreater(heartbeat["last_progress_timestamp_seconds"], 0)
            self.assertEqual(json.loads(state.read_text())["version"], 1)


if __name__ == "__main__":
    unittest.main()
