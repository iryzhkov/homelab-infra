#!/usr/bin/env python3
"""Prove WAL snapshot restore, atomic gzip publication, retention and missing NAS refusal."""
import gzip
import importlib.machinery
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / "stacks/jocasta/files/jocasta-backup"
loader = importlib.machinery.SourceFileLoader("jocasta_backup", str(PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
backup = importlib.util.module_from_spec(spec)
loader.exec_module(backup)

class BackupTest(unittest.TestCase):
    def test_live_wal_restore_and_retention(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            live = sqlite3.connect(root / "live.db")
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("CREATE TABLE revisions(id TEXT, body BLOB)")
            body = b"# Plan\r\nUnicode: \\xc3\\xa9\r\n"
            live.execute("INSERT INTO revisions VALUES(?,?)", ("stable-id@7", body))
            live.commit()
            backup.snapshot(root / "live.db", root / "snapshot.db")
            backup.publish(root / "snapshot.db", root / "nas", "2026-09-16.sqlite3.gz")
            with gzip.open(root / "nas/2026-09-16.sqlite3.gz", "rb") as compressed:
                (root / "restored.db").write_bytes(compressed.read())
            restored = sqlite3.connect(root / "restored.db")
            self.assertEqual(restored.execute("SELECT * FROM revisions").fetchone(), ("stable-id@7", body))
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual((root / "nas/2026-09-16.sqlite3.gz").stat().st_mode & 0o777, 0o600)
            for day in range(1, 16):
                (root / ("nas/2026-09-%02d.sqlite3.gz" % day)).touch()
            backup.prune(root / "nas", 3)
            self.assertEqual(len(list((root / "nas").glob("*.sqlite3.gz"))), 3)
            self.assertFalse(list((root / "nas").glob("*.partial")))
            live.close()
            restored.close()

    def test_missing_mount_does_not_create_destination(self):
        with tempfile.TemporaryDirectory() as scratch:
            destination = Path(scratch) / "absent/jocasta"
            with mock.patch.object(backup.subprocess, "run", return_value=mock.Mock(
                    stdout='{"filesystems":[{"fstype":"ext4"}]}')):
                with self.assertRaises(RuntimeError):
                    backup.require_nas(Path(scratch), destination)
            self.assertFalse(destination.exists())

    def test_stale_recovery_point(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            with self.assertRaises(RuntimeError):
                backup.check_freshness(root)
            (root / "hourly").mkdir()
            snapshot = root / "hourly/2026-09-16.sqlite3.gz"
            snapshot.touch()
            backup.check_freshness(root)
            with mock.patch.object(backup.time, "time", return_value=snapshot.stat().st_mtime + 7201):
                with self.assertRaises(RuntimeError):
                    backup.check_freshness(root)

    def test_outside_mount_refused(self):
        with self.assertRaises(RuntimeError):
            backup.require_nas("/mnt/backup", "/tmp/jocasta")

if __name__ == "__main__":
    unittest.main()
