#!/usr/bin/env python3
"""Outbox privacy, hostile files, bounded draining and retry behavior."""
import contextlib
import importlib.machinery
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

HOME = Path(os.environ["HOME"])
assert os.environ["SHIZUNE_TEST_HOME"] == str(HOME)
assert Path(os.environ["TMPDIR"]).is_relative_to(HOME)
ROOT = Path(__file__).resolve().parents[1]
relay = importlib.machinery.SourceFileLoader(
    "relay", str(ROOT / "stacks/shizune/files/shizune-feed-relay")).load_module()
EVENT = {"v": 1, "event": "budget_delete", "count": 1, "at": "2026-11-22T01:02:03Z"}


class RelayTest(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(relay.title(json.dumps(EVENT)),
                         "Shizune deleted 1 stale pending budget transactions")
        self.assertEqual(relay.title(json.dumps(EVENT | {"event": "budget_categorize", "count": 500})),
                         "Shizune categorized 500 budget transactions")
        for change in ({"v": True}, {"count": True}, {"count": 0}, {"count": 501},
                       {"event": "private budget data"}, {"extra": "private"},
                       {"at": "2026-02-30T01:02:03Z"}, {"at": "2026-11-22T01:02:03+00:00"}):
            with self.assertRaises(ValueError):
                relay.title(json.dumps(EVENT | change))
        with self.assertRaises(ValueError):
            relay.title(json.dumps(EVENT)[:-1] + ', "count": 2}')

    def test_drain_retry_and_hostile_files(self):
        with tempfile.TemporaryDirectory(dir=HOME) as directory:
            root = Path(directory)
            def put(n, raw=None):
                p = root / (f"{n:032x}.json")
                p.write_text(json.dumps(EVENT) if raw is None else raw)
                p.chmod(0o600)
                return p
            valid = put(1)
            relay.relay(directory, lambda _: False)
            self.assertTrue(valid.exists())
            bad = put(2, '{"private": "do not log"}')
            big = put(3, "x" * 1025)
            link = root / ("4" * 32 + ".json")
            link.symlink_to(valid)
            hard = root / ("5" * 32 + ".json")
            os.link(big, hard)
            temporary = root / ".temp"
            temporary.write_text("untouched")
            output = io.StringIO()
            sent = []
            with contextlib.redirect_stdout(output):
                relay.relay(directory, lambda message: sent.append(message) or True)
            self.assertEqual(len(sent), 1)
            self.assertFalse(valid.exists())
            for p in (bad, big, link, hard):
                self.assertFalse(p.exists())
            self.assertTrue(temporary.exists())
            self.assertNotIn("private", output.getvalue())
            for n in range(100, 151):
                put(n)
            relay.relay(directory, lambda _: True)
            self.assertEqual(len(list(root.glob("*.json"))), 1)

if __name__ == "__main__":
    unittest.main()
