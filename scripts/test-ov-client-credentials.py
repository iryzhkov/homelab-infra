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
import types
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
OLD = b"old-synthetic-root-key\r\n"


class RecordingSecret:
    """Only in-memory Secret.Service/Item/Value API. Never touches D-Bus."""
    def __init__(self):
        self.data = OLD
        self.calls = []
        self.items = [self]
        self.locked = False
        self.fail_write = False
        self.item_path = "/synthetic/collection/item1"
        self.attributes = {"service": "openviking", "key": "api"}
        self.label = "retained synthetic label"
        self.api = types.SimpleNamespace(
            Service=types.SimpleNamespace(get_sync=self.get_sync),
            ServiceFlags=types.SimpleNamespace(OPEN_SESSION=1),
            SearchFlags=types.SimpleNamespace(ALL=2),
            Value=types.SimpleNamespace(new=self.new))

    def get_sync(self, flags, cancellable):
        self.calls.append(("open", flags, cancellable))
        return self

    def search_sync(self, schema, attributes, flags, cancellable):
        self.calls.append(("search", schema, attributes, flags, cancellable))
        return self.items

    def get_locked(self):
        return self.locked

    def load_secret_sync(self, cancellable):
        self.calls.append(("load", cancellable))
        return True

    def get_secret(self):
        return self

    def get_text(self):
        return self.data.decode()

    def get(self):
        return self.data

    def get_object_path(self):
        return self.item_path

    def get_attributes(self):
        return dict(self.attributes)

    def get_label(self):
        return self.label

    def new(self, text, length, content_type):
        self.calls.append(("new", length, content_type))
        assert length == len(text.encode()) and content_type == "text/plain"
        return text.encode()

    def set_secret_sync(self, value, cancellable):
        self.calls.append(("set", cancellable))
        if self.fail_write:
            raise RuntimeError(KEY.decode())  # Must never escape the boundary.
        self.data = value
        return True

    def factory(self):
        gi = types.SimpleNamespace(require_version=lambda *args: None)
        with patch.dict(sys.modules, gi=gi), patch.object(
                module.importlib, "import_module", return_value=self.api):
            return module.Keyring()


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
        self.config = self.folder / "config.toml"
        self.original_config = b'base_url = "https://example.invalid"\n# retained\n'
        self.config.write_bytes(self.original_config)
        self.config.chmod(0o644)
        self.target = self.folder / "key"
        self.target.write_bytes(OLD)
        self.target.chmod(0o600)
        self.backup = self.folder / "credential-rollovers" / "rollover-1.json"
        self.secret = RecordingSecret()

    def run_update(self, action="install", key=KEY, apply=True, **overrides):
        args = dict(home=self.home, host="selected-host", approved_hosts=["selected-host"],
                    generation="rollover-1", action=action, backend="keyfile",
                    key=key, apply=apply, keyring_factory=self.secret.factory)
        args.update(overrides)
        return module.operate(**args)

    def test_dry_run_has_no_writes(self):
        result = self.run_update(apply=False)
        self.assertTrue(result["changed"])
        self.assertFalse(result["mcp_restart_required"])
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.backup.parent.exists())

    def test_install_idempotence_restore_exact_bytes_and_config(self):
        self.assertTrue(self.run_update()["changed"])
        self.assertEqual(self.target.read_bytes(), KEY)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backup.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.backup.parent.stat().st_mode), 0o700)
        self.assertFalse(self.run_update()["changed"])
        self.assertFalse(self.run_update("restore")["mcp_restart_required"])
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.run_update("restore")["changed"])
        self.assertEqual(self.config.read_bytes(), self.original_config)

    def test_missing_keyfile_or_folder_refuses_without_creation(self):
        self.target.unlink()
        with self.assertRaises(module.Refusal):
            self.run_update()
        self.assertFalse(self.backup.parent.exists())
        self.config.unlink()
        self.folder.rmdir()
        with self.assertRaises((OSError, module.Refusal)):
            self.run_update()
        self.assertFalse(self.folder.exists())

    def test_missing_config_stays_absent(self):
        self.config.unlink()
        self.run_update()
        self.run_update("restore")
        self.assertFalse(self.config.exists())

    def test_overrides_refuse_install_and_restore(self):
        for config in [b'api_key = "synthetic-cached"\n', b'api_key = ""\n']:
            self.config.write_bytes(config)
            for action in ("install", "restore"):
                with self.assertRaisesRegex(module.Refusal, "cached TOML"):
                    self.run_update(action)
        self.config.write_bytes(self.original_config)
        for name in ("OV_API_KEY", "OV_CONFIG"):
            with patch.dict(os.environ, {name: "synthetic-override"}):
                with self.assertRaisesRegex(module.Refusal, "override"):
                    self.run_update()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.backup.parent.exists())

    def test_reused_generation_different_key_refused(self):
        self.run_update()
        with self.assertRaises(module.Refusal):
            self.run_update(key=OTHER)
        self.assertEqual(self.target.read_bytes(), KEY)

    def test_concurrent_client_edit_refuses_install_and_restore(self):
        self.run_update()
        self.target.write_bytes(OTHER)
        for action in ("install", "restore"):
            with self.assertRaises(module.Refusal):
                self.run_update(action)
        self.assertEqual(self.target.read_bytes(), OTHER)

    def test_last_read_change_refuses_commit(self):
        real_read = module.Keyfile.read
        reads = 0

        def racing_read(target):
            nonlocal reads
            reads += 1
            if reads == 3:
                self.target.write_bytes(OTHER)
            return real_read(target)

        with patch.object(module.Keyfile, "read", racing_read):
            with self.assertRaisesRegex(module.Refusal, "before commit"):
                self.run_update()
        self.assertEqual(self.target.read_bytes(), OTHER)

    def test_interruption_after_backup_can_resume_or_restore(self):
        with patch.object(module, "atomic_write", side_effect=OSError("synthetic fault")):
            with self.assertRaises(OSError):
                self.run_update()
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.run_update("restore")["changed"])
        self.assertTrue(self.run_update()["changed"])
        self.run_update("restore")
        self.assertEqual(self.target.read_bytes(), OLD)

    def test_symlink_hardlink_permissions_and_parent_refuse(self):
        for mode in (0o644, 0o666):
            self.target.chmod(mode)
            with self.assertRaises(module.Refusal):
                self.run_update()
        self.target.chmod(0o600)
        linked = self.home / "linked"
        os.link(self.target, linked)
        with self.assertRaises(module.Refusal):
            self.run_update()
        linked.unlink()
        self.target.unlink()
        linked.write_bytes(OTHER)
        self.target.symlink_to(linked)
        with self.assertRaises(OSError):
            self.run_update()
        self.assertEqual(linked.read_bytes(), OTHER)
        self.target.unlink()
        self.target.write_bytes(OLD)
        self.target.chmod(0o600)
        self.folder.chmod(0o777)
        with self.assertRaises(module.Refusal):
            self.run_update()
        self.folder.chmod(0o755)

    def test_scope_backend_and_generation_refused_before_writes(self):
        for overrides in [dict(host="other"), dict(approved_hosts=[]),
                          dict(approved_hosts=["selected-host", "selected-host"]),
                          dict(generation="../escape"), dict(host="all:*"), dict(backend="toml")]:
            with self.assertRaises(module.Refusal):
                self.run_update(**overrides)
        self.assertFalse(self.backup.parent.exists())

    def test_old_or_corrupt_backup_refuses_without_migration(self):
        self.run_update()
        for record in ({}, {"version": 1, "before": None}):
            self.backup.write_text(json.dumps(record))
            for action in ("install", "restore"):
                with self.assertRaisesRegex(module.Refusal, "unsupported backup schema"):
                    self.run_update(action)
        self.assertEqual(self.target.read_bytes(), KEY)

    def test_backup_identity_and_generation_refuse(self):
        self.run_update()
        original = json.loads(self.backup.read_text())
        for field, value in [("host", "different"), ("generation", "different"),
                             ("backend", "keyring"), ("identity", {})]:
            self.backup.write_text(json.dumps(dict(original, **{field: value})))
            with self.assertRaises(module.Refusal):
                self.run_update("restore")
        self.assertEqual(self.target.read_bytes(), KEY)

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
        (self.home / ".git").mkdir()
        with self.assertRaises(module.Refusal):
            module.source_key(source)
        with self.assertRaises(module.Refusal):
            self.run_update()

    def cli(self, data=KEY, **environment):
        result = subprocess.run(
            [sys.executable, "-B", str(SOURCE), "--stdin-key", "--backend", "keyfile",
             "--host", "selected-host", "--approved-host", "selected-host",
             "--generation", "rollover-1"],
            input=data, capture_output=True, env=dict(os.environ, **environment))
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertNotIn(OTHER, result.stdout + result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        return result

    def test_private_cli_errors_and_explicit_override_reason(self):
        self.assertEqual(self.cli(KEY + b"\n" + OTHER).returncode, 2)
        self.config.write_bytes(b'api_key = """synthetic-secret\nunterminated')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b"synthetic-secret", result.stdout + result.stderr)
        self.config.write_bytes(self.original_config)
        result = self.cli(OV_API_KEY=OTHER.decode())
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"OV_API_KEY override", result.stderr)
        with self.assertRaises(module.Refusal):
            self.run_update("restore")

    def test_keyring_dry_run_apply_idempotence_restore(self):
        self.target.unlink()  # Keyring backend never creates a fallback file.
        result = self.run_update(backend="keyring", apply=False)
        self.assertFalse(result["mcp_restart_required"])
        self.assertEqual(self.secret.data, OLD)
        self.assertFalse(self.backup.parent.exists())
        self.assertFalse(any(call[0] == "set" for call in self.secret.calls))
        self.run_update(backend="keyring")
        self.assertEqual(self.secret.data, KEY)
        self.assertFalse(self.run_update(backend="keyring")["changed"])
        record = json.loads(self.backup.read_text())
        self.assertEqual(module.unpack(record["before"]), OLD)
        self.run_update("restore", backend="keyring")
        self.assertEqual(self.secret.data, OLD)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.secret.attributes, {"service": "openviking", "key": "api"})
        self.assertEqual(self.secret.label, "retained synthetic label")
        self.assertTrue(all(call[3] == 2 for call in self.secret.calls if call[0] == "search"))

    def test_keyring_missing_locked_ambiguous_unavailable_refuses(self):
        for items, locked in [([], False), ([self.secret, self.secret], False),
                              ([self.secret], True)]:
            self.secret.items, self.secret.locked = items, locked
            with self.assertRaises(module.Refusal):
                self.run_update(backend="keyring")
        with patch.dict(sys.modules, gi=None):
            with self.assertRaisesRegex(module.Refusal, "unavailable"):
                module.Keyring()
        self.assertFalse(self.backup.parent.exists())
        self.assertEqual(self.target.read_bytes(), OLD)

    def test_keyring_failure_retains_backup_without_fallback(self):
        self.secret.fail_write = True
        with self.assertRaisesRegex(module.Refusal, "^existing keyring update failed; no fallback$"):
            self.run_update(backend="keyring")
        self.assertTrue(self.backup.exists())
        self.assertEqual(self.secret.data, OLD)
        self.assertEqual(self.target.read_bytes(), OLD)
        self.secret.fail_write = False
        self.run_update(backend="keyring")
        self.run_update("restore", backend="keyring")
        self.assertEqual(self.secret.data, OLD)

    def test_keyring_user_changes_and_item_replacement_refuse_restore(self):
        self.run_update(backend="keyring")
        self.secret.data = OTHER
        with self.assertRaises(module.Refusal):
            self.run_update("restore", backend="keyring")
        self.secret.data = KEY
        self.secret.item_path = "/synthetic/collection/replaced"
        with self.assertRaises(module.Refusal):
            self.run_update("restore", backend="keyring")
        self.assertEqual(self.secret.data, KEY)

    def test_same_running_synthetic_wrapper_reloads_keyfile(self):
        # Models the installed per-request keyfile fallback, with cached config empty.
        program = (
            "import sys; from pathlib import Path; "
            "key=Path.home()/'.config/ov-mcp/key'\n"
            "for request in sys.stdin:\n"
            " print(key.read_bytes().strip().decode(), flush=True)\n")
        with subprocess.Popen([sys.executable, "-B", "-c", program], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as wrapper:
            writer, reader = wrapper.stdin, wrapper.stdout
            assert writer is not None and reader is not None

            def request():
                writer.write("request\n")
                writer.flush()
                return reader.readline().strip()
            self.assertEqual(request(), OLD.strip().decode())
            self.run_update()
            self.assertEqual(request(), KEY.decode())
            self.run_update("restore")
            self.assertEqual(request(), OLD.strip().decode())
            writer.close()

    def ansible(self, check=False, **overrides):
        inventory = self.home / "inventory.ini"
        inventory.write_text("selected-host ansible_connection=local\n")
        source = self.home / "tenant-key"
        source.write_bytes(KEY)
        source.chmod(0o600)
        variables = dict(ov_client_enabled=True, ov_client_hosts=["selected-host"],
                         ov_client_generation="rollover-1", ov_client_backend="keyfile",
                         ov_client_key_source=str(source))
        variables.update(overrides)
        play = SOURCE.parents[1] / "playbooks" / "ov-client-credentials.yml"
        result = subprocess.run(["ansible-playbook", "-i", str(inventory), str(play),
                                 "-e", json.dumps(variables)] + (["--check"] if check else []),
                                capture_output=True)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertNotIn(OLD.strip(), result.stdout + result.stderr)
        return result

    def test_actual_playbook_preview_apply_idempotence_restore(self):
        for check in (False, True):
            result = self.ansible(check=check, ov_client_apply=check)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(self.target.read_bytes(), OLD)
            self.assertFalse(self.backup.parent.exists())
        result = self.ansible(ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), KEY)
        result = self.ansible(ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(b"changed=0", result.stdout)
        result = self.ansible(ov_client_action="restore", ov_client_apply=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.config.read_bytes(), self.original_config)

    def test_actual_playbook_scope_disabled_backend_and_unsafe_refuse(self):
        for overrides in [dict(ov_client_enabled=False),
                          dict(ov_client_hosts=["selected-host", "unapproved"]),
                          dict(ov_client_backend="toml"),
                          dict(ov_client_generation="../escape")]:
            result = self.ansible(ov_client_apply=True, **overrides)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.target.read_bytes(), OLD)
        self.target.chmod(0o644)
        self.assertNotEqual(self.ansible(ov_client_apply=True).returncode, 0)
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertFalse(self.backup.parent.exists())


if __name__ == "__main__":
    unittest.main()
