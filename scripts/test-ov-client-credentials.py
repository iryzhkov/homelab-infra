#!/usr/bin/env python3
"""Run only in the asserted disposable Chaldea HOME."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

TEST_HOME = Path(os.environ["OV_CLIENT_TEST_HOME"])
assert TEST_HOME.is_absolute() and Path.home() == TEST_HOME
assert ".cache/ov-selfhost-20261024" in str(TEST_HOME)
assert Path(os.environ["TMPDIR"]).is_relative_to(TEST_HOME)
SOURCE = Path(__file__).with_name("ov-client-credentials.py")
spec = importlib.util.spec_from_file_location("credentials", SOURCE)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
KEY = b"synthetic-tenant-key-for-unit-tests"
OTHER = b"another-synthetic-test-key"


class Credentials(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=TEST_HOME)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir(mode=0o700)
        self.env = patch.dict(os.environ, HOME=str(self.home))
        self.env.start()
        self.addCleanup(self.env.stop)
        self.folder = self.home / ".config" / "ov-mcp"
        self.folder.mkdir(parents=True)
        self.target = self.folder / "config.toml"
        self.original = b'base_url = "https://example.invalid"\n# retained\n[extra]\nvalue = 3\n'
        self.target.write_bytes(self.original)
        self.target.chmod(0o644)
        self.backup = self.folder / "credential-rollovers" / "rollover-1.json"

    def run_update(self, action="install", key=KEY, apply=True, **overrides):
        args = dict(home=self.home, host="selected-host", approved_hosts=["selected-host"],
                    generation="rollover-1", action=action, key=key, apply=apply)
        args.update(overrides)
        return module.operate(**args)

    def test_dry_run_has_no_writes(self):
        result = self.run_update(apply=False)
        self.assertTrue(result["changed"])
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.backup.parent.exists())

    def test_install_idempotence_restore_original_mode_and_bytes(self):
        self.assertTrue(self.run_update()["changed"])
        self.assertEqual(tomllib.loads(self.target.read_text())["api_key"], KEY.decode())
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backup.parent.stat().st_mode), 0o700)
        self.assertFalse(self.run_update()["changed"])
        self.assertTrue(self.run_update("restore")["mcp_restart_required"])
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o644)
        self.assertFalse(self.run_update("restore")["changed"])

    def test_missing_config_restores_absence(self):
        self.target.unlink()
        self.run_update()
        self.run_update("restore")
        self.assertFalse(self.target.exists())

    def test_missing_config_directory_dry_run(self):
        self.target.unlink()
        self.folder.rmdir()
        self.assertTrue(self.run_update(apply=False)["changed"])
        self.assertFalse(self.folder.exists())

    def test_existing_inline_key_preserves_other_values(self):
        self.target.write_bytes(b'api_key = "old-synthetic" # old comment\n' + self.original)
        self.target.chmod(0o600)
        self.run_update()
        data = tomllib.loads(self.target.read_text())
        self.assertEqual(data["api_key"], KEY.decode())
        self.assertEqual(data["extra"], {"value": 3})

    def test_reused_generation_different_key_refused(self):
        self.run_update()
        before = self.target.read_bytes()
        with self.assertRaises(module.Refusal):
            self.run_update(key=OTHER)
        self.assertEqual(self.target.read_bytes(), before)

    def test_concurrent_client_edit_refuses_install_and_restore(self):
        self.run_update()
        self.target.write_bytes(b'base_url = "https://changed.invalid"\n')
        for action in ("install", "restore"):
            with self.assertRaises(module.Refusal):
                self.run_update(action)
        self.assertIn(b"changed.invalid", self.target.read_bytes())

    def test_interruption_after_backup_can_resume_or_restore(self):
        with patch.object(module, "atomic_write", side_effect=OSError("synthetic fault")):
            with self.assertRaises(OSError):
                self.run_update()
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.run_update("restore")["changed"])
        self.assertTrue(self.run_update()["changed"])
        self.run_update("restore")
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_unsafe_target_and_parent_refused(self):
        self.target.unlink()
        outside = self.home / "unrelated"
        outside.write_text("unrelated")
        self.target.symlink_to(outside)
        with self.assertRaises(OSError):
            self.run_update()
        self.assertEqual(outside.read_text(), "unrelated")
        self.target.unlink()
        self.target.write_bytes(self.original)
        self.target.chmod(0o600)
        self.folder.chmod(0o777)
        with self.assertRaises(module.Refusal):
            self.run_update()
        self.folder.chmod(0o755)

    def test_hardlink_and_public_inline_key_refused(self):
        os.link(self.target, self.home / "linked")
        with self.assertRaises(module.Refusal):
            self.run_update()
        (self.home / "linked").unlink()
        self.target.write_bytes(b'api_key = "public-synthetic"\n')
        with self.assertRaises(module.Refusal):
            self.run_update()

    def test_scope_and_generation_refused_before_writes(self):
        for overrides in [dict(host="other"), dict(approved_hosts=[]),
                          dict(approved_hosts=["selected-host", "selected-host"]),
                          dict(generation="../escape"), dict(host="all:*")]:
            with self.assertRaises(module.Refusal):
                self.run_update(**overrides)
        self.assertFalse(self.backup.parent.exists())

    def test_corrupt_backup_refused(self):
        self.run_update()
        self.backup.write_text("{}")
        with self.assertRaises(module.Refusal):
            self.run_update()
        with self.assertRaises(module.Refusal):
            self.run_update("restore")

    def test_backup_links_and_permissions_refused(self):
        self.run_update()
        self.backup.chmod(0o644)
        with self.assertRaises(module.Refusal):
            self.run_update("restore")
        self.backup.chmod(0o600)
        self.backup.parent.chmod(0o755)
        with self.assertRaises(module.Refusal):
            self.run_update("restore")

    def test_protected_source_guards(self):
        source = self.home / "protected-key"
        source.write_bytes(KEY + b"\n")
        source.chmod(0o600)
        self.assertEqual(module.source_key(source), KEY)
        source.chmod(0o644)
        with self.assertRaises(module.Refusal):
            module.source_key(source)
        source.chmod(0o600)
        alias = self.home / "source-link"
        alias.symlink_to(source)
        with self.assertRaises(OSError):
            module.source_key(alias)
        (self.home / ".git").mkdir()
        with self.assertRaises(module.Refusal):
            module.source_key(source)

    def test_cli_error_cannot_disclose_input(self):
        result = subprocess.run([sys.executable, "-B", str(SOURCE), "--stdin-key",
                                 "--host", "selected-host", "--approved-host", "selected-host",
                                 "--generation", "rollover-1"], input=KEY + b"\n" + OTHER,
                                capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertNotIn(OTHER, result.stdout + result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def test_toml_failure_is_private_and_rollback_requires_backup(self):
        self.target.write_bytes(b'api_key = """synthetic-secret\nunterminated')
        result = subprocess.run([sys.executable, "-B", str(SOURCE), "--stdin-key",
                                 "--host", "selected-host", "--approved-host", "selected-host",
                                 "--generation", "rollover-1"], input=KEY, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"synthetic-secret", result.stdout + result.stderr)
        self.target.write_bytes(self.original)
        with self.assertRaises(module.Refusal):
            self.run_update("restore")


    def ansible(self, **overrides):
        inventory = self.home / "inventory.ini"
        inventory.write_text("selected-host ansible_connection=local\n")
        source = self.home / "tenant-key"
        source.write_bytes(KEY)
        source.chmod(0o600)
        variables = dict(ov_client_enabled=True, ov_client_hosts=["selected-host"],
                         ov_client_generation="rollover-1", ov_client_key_source=str(source))
        variables.update(overrides)
        play = SOURCE.parents[1] / "playbooks" / "ov-client-credentials.yml"
        result = subprocess.run(["ansible-playbook", "-i", str(inventory), str(play),
                                 "-e", json.dumps(variables)], capture_output=True)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        return result

    def test_actual_playbook_preview_apply_restore(self):
        result = self.ansible()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.backup.parent.exists())
        result = self.ansible(ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tomllib.loads(self.target.read_text())["api_key"], KEY.decode())
        result = self.ansible(ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"changed=0", result.stdout)
        result = self.ansible(ov_client_action="restore", ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), self.original)

    def test_actual_playbook_scope_and_disabled_refuse(self):
        for overrides in [dict(ov_client_enabled=False),
                          dict(ov_client_hosts=["selected-host", "unapproved"]),
                          dict(ov_client_generation="../escape")]:
            result = self.ansible(ov_client_apply=True, **overrides)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.target.read_bytes(), self.original)
        self.assertFalse(self.backup.parent.exists())


if __name__ == "__main__":
    unittest.main()
