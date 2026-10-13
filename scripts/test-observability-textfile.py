#!/usr/bin/env python3
"""Root writers must never follow predictable temporary-file symlinks."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


gpu = load("gpu", "roles/base/files/homelab-gpu-report")
backup = load("backup", "roles/observability_backup/files/observability-vm-backup")


class TextfileTest(unittest.TestCase):
    def test_gpu_ignores_predictable_temp_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim"
            victim.write_text("preserve")
            target = root / "igpu.prom"
            (root / "igpu.prom.tmp").symlink_to(victim)
            gpu.write_textfile(str(target), "homelab", "i915", {"rcs0_busy_pct": 12})
            self.assertEqual(victim.read_text(), "preserve")
            self.assertIn("igpu_rcs0_busy_pct", target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)

    def test_failed_gpu_replace_preserves_previous_sample_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "igpu.prom"
            target.write_text("previous")
            with patch.object(gpu.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    gpu.write_textfile(str(target), "homelab", "i915", {"rcs0_busy_pct": 12})
            self.assertEqual(target.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_backup_success_replaces_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim"
            victim.write_text("preserve")
            target = root / "backup.prom"
            target.symlink_to(victim)
            backup.record_backup_success({"metrics_file": str(target)})
            self.assertEqual(victim.read_text(), "preserve")
            self.assertFalse(target.is_symlink())
            self.assertIn("observability_backup_last_success_timestamp_seconds", target.read_text())

    def test_failed_backup_replace_preserves_previous_success(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "backup.prom"
            target.write_text("previous")
            with patch.object(backup.os, "replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    backup.record_backup_success({"metrics_file": str(target)})
            self.assertEqual(target.read_text(), "previous")
            self.assertEqual(list(Path(directory).iterdir()), [target])


if __name__ == "__main__":
    unittest.main()
