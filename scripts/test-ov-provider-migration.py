#!/usr/bin/env python3
"""Failure-boundary fixtures; no Docker, cloud calls or production access."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration", ROOT / "scripts/ov-provider-migration.py")
assert spec is not None and spec.loader is not None
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        scratch_home = Path(os.environ["HOME"])
        assert scratch_home.name == "home" and scratch_home.parent.name == "ov-selfhost-20261024"
        assert Path(os.environ["TMPDIR"]).is_relative_to(scratch_home)
        self.temp = tempfile.TemporaryDirectory(dir=os.environ["TMPDIR"])
        self.addCleanup(self.temp.cleanup)
        self.stage = Path(self.temp.name) / ".cache/ov-selfhost-20261024/upgrade-stage"
        self.stage.mkdir(parents=True)
        self.backup = self.stage / "backup-0.2.6"
        (self.backup / "data/viking/default/resources/note").mkdir(parents=True)
        (self.backup / "config").mkdir()
        (self.backup / "config/ov.conf").write_text(json.dumps({
            "server": {"root_api_key": "synthetic-only"},
            "storage": {"workspace": "/app/data"}}))
        for name in ["raw.md", ".abstract.md", ".overview.md"]:
            (self.backup / "data/viking/default/resources/note" / name).write_text(name + "\n")
        (self.backup / "complete").write_text("quiescent-copy\n")

    def test_prepare_repeated_and_mutation_detection(self):
        before = migration.manifest(self.backup)
        self.assertTrue(migration.prepare(self.stage)["changed"])
        target = self.stage / migration.CANDIDATE
        self.assertFalse(migration.prepare(self.stage)["changed"])
        self.assertEqual(migration.manifest(self.backup), before)
        self.assertEqual(migration.verify(self.stage)["original_files"], 3)
        self.assertEqual((target / "config/ov.conf").stat().st_mode & 0o777, 0o600)
        compose = json.loads((target / "compose.json").read_text())["services"]["candidate"]
        self.assertEqual(compose["ports"], ["127.0.0.1:19335:1933"])
        self.assertEqual(compose["image"], migration.IMAGE)
        self.assertTrue(all(str(self.backup) not in str(v) for v in compose["volumes"]))
        (target / "data/viking/default/resources/note/.overview.md").write_text("changed")
        with self.assertRaises(ValueError):
            migration.verify(self.stage)
        with self.assertRaises(ValueError):
            migration.prepare(self.stage)
        self.assertEqual(migration.manifest(self.backup), before)

    def test_incomplete_backup_and_existing_target_are_never_overwritten(self):
        (self.backup / "complete").unlink()
        with self.assertRaises(FileNotFoundError):
            migration.prepare(self.stage)
        (self.backup / "complete").write_text("quiescent-copy")
        target = self.stage / migration.CANDIDATE
        target.mkdir()
        (target / "keep").write_text("sentinel")
        with self.assertRaises(FileNotFoundError):
            migration.prepare(self.stage)
        self.assertEqual((target / "keep").read_text(), "sentinel")

    def test_failed_copy_has_no_published_candidate_and_retry_succeeds(self):
        original = migration.manifest(self.backup)
        with mock.patch.object(migration.shutil, "copytree", side_effect=OSError("synthetic disk failure")):
            with self.assertRaises(OSError):
                migration.prepare(self.stage)
        self.assertFalse((self.stage / migration.CANDIDATE).exists())
        self.assertFalse(list(self.stage.glob(".candidate-*")))
        self.assertEqual(migration.manifest(self.backup), original)
        self.assertTrue(migration.prepare(self.stage)["changed"])

    def test_paths_and_symlinks_fail_closed(self):
        self.assertEqual(migration.stage_path(str(self.stage)), self.stage)
        for value in ["/srv/swarm/openviking", "relative/.cache/ov-selfhost-20261024/upgrade-stage"]:
            with self.assertRaises(ValueError):
                migration.stage_path(value)
        link = self.backup / "config/escape"
        link.symlink_to(self.stage / "outside")
        with self.assertRaises(ValueError):
            migration.prepare(self.stage)
        link.unlink()
        migration.prepare(self.stage)
        target = self.stage / migration.CANDIDATE
        (target / "data/viking/escape").symlink_to(self.backup / "data/viking/default")
        with self.assertRaises(ValueError):
            migration.verify(self.stage)

    def test_reindex_is_a_plan_and_forces_all_resource_vectors(self):
        plan = migration.reindex_plan()
        self.assertEqual(plan["path"], "/api/v1/content/reindex")
        self.assertEqual(plan["body"], {"uri": "viking://resources/", "mode": "vectors_only",
                                      "recursive": True, "force": True, "wait": False})


if __name__ == "__main__":
    unittest.main()
