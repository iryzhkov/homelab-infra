#!/usr/bin/env python3
"""Exercise owner guards, retention, cleanup, private API and isolated recovery."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("backup", str(ROOT / "roles/observability_backup/files/observability-vm-backup"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
backup = importlib.util.module_from_spec(spec)
loader.exec_module(backup)
ROW = {"metric": {"__name__": "up", "job": "test"}, "value": [100, "1"]}
PAYLOAD = {"status": "success", "data": {"resultType": "vector", "result": [ROW]}}
IMAGE = "victoriametrics/victoria-metrics@sha256:" + "a" * 64


class NativeBackupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        state = self.root / "state"
        (state / "victoriametrics").mkdir(parents=True)
        (state / ".swarm-owner").write_text("homelab\n")
        self.config = {
            "host": "homelab", "state_root": str(state),
            "backup_root": str(self.root / "backups"), "restore_root": str(self.root / "restore"),
            "keep": 2, "proof_query": "up", "peer_copy": True,
            "max_backup_bytes": 10000, "minimum_free_bytes": 10,
            "restore_max_age_seconds": 7200,
            "vmbackup_image": IMAGE, "vmrestore_image": IMAGE,
        }

    def cmd(self, argv, **kwargs):
        if argv[:3] == ["docker", "ps", "-q"]:
            return "abc123"
        if argv[:3] == ["docker", "inspect", "--format"]:
            return IMAGE
        return ""

    def api(self, container, path, *args, **kwargs):
        if path == "/snapshot/create":
            return {"status": "ok", "snapshot": "20261013-test"}
        if path == "/snapshot/delete":
            return {"status": "ok"}
        return PAYLOAD

    def test_backup_retains_generations_cleans_snapshot_and_mirrors(self):
        root = Path(self.config["backup_root"])
        for name in ["20260101T000000Z-old", "20260102T000000Z-old"]:
            (root / name).mkdir(parents=True)
            (root / name / "proof.json").write_text("{}")
        with patch.object(backup, "command", side_effect=self.cmd), \
             patch.object(backup, "api", side_effect=self.api) as api, \
             patch.object(backup, "size", return_value=1), \
             patch.object(backup, "native_helper") as helper, \
             patch.object(backup, "peer_copy") as peer:
            backup.backup(self.config)
        self.assertEqual(len(list(root.iterdir())), 2)
        self.assertTrue(any(c.args[1] == "/snapshot/delete" for c in api.call_args_list))
        self.assertIn("-snapshotName=20261013-test", helper.call_args.args[0])
        self.assertIn("--network", helper.call_args.args[0])
        peer.assert_called_once()

    def test_native_failure_still_deletes_snapshot_and_partial(self):
        with patch.object(backup, "command", side_effect=self.cmd), \
             patch.object(backup, "api", side_effect=self.api) as api, \
             patch.object(backup, "size", return_value=1), \
             patch.object(backup, "native_helper", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                backup.backup(self.config)
        self.assertTrue(any(c.args[1] == "/snapshot/delete" for c in api.call_args_list))
        self.assertEqual(list(Path(self.config["backup_root"]).iterdir()), [])

    def test_running_task_without_owner_is_refused(self):
        (Path(self.config["state_root"]) / ".swarm-owner").unlink()
        with patch.object(backup, "command", side_effect=self.cmd), patch.object(backup, "api") as api:
            with self.assertRaises(RuntimeError):
                backup.backup(self.config)
            api.assert_not_called()

    def test_restore_only_mounts_scratch_and_backup_without_ports(self):
        source = Path(self.config["backup_root"]) / "generation"
        (source / "data").mkdir(parents=True)
        (source / "proof.json").write_text(json.dumps({
            "created": int(backup.time.time()), "query": "up", "time": 100,
            "expected": [ROW], "vm_image": IMAGE,
        }))
        with patch.object(backup, "command", side_effect=self.cmd) as commands, \
             patch.object(backup, "native_helper") as helper, \
             patch.object(backup, "size", return_value=1), \
             patch.object(backup, "api", return_value=PAYLOAD):
            backup.restore_check(self.config)
        args = helper.call_args.args[0]
        self.assertIn("-src=fs:///backup", args)
        run = next(c.args[0] for c in commands.call_args_list if c.args[0][:3] == ["docker", "run", "-d"])
        self.assertIn("none", run)
        self.assertNotIn("-p", run)
        self.assertFalse(any(str(Path(self.config["state_root"])) in a for a in run))
        self.assertEqual(list(Path(self.config["restore_root"]).iterdir()), [])

    def test_authentication_key_is_not_in_host_arguments(self):
        with patch.object(backup, "command", return_value='{"status":"ok"}') as cmd:
            backup.api("container", "/snapshot/create", authenticated=True)
        self.assertNotIn("authKey", " ".join(cmd.call_args.args[0]))
        self.assertIn("/run/secrets/vm_snapshot_key", cmd.call_args.kwargs["stdin"])

    def test_nonowner_without_local_task_skips_api(self):
        (Path(self.config["state_root"]) / ".swarm-owner").write_text("normandy\n")
        with patch.object(backup, "command", return_value=""), patch.object(backup, "api") as api:
            backup.backup(self.config)
            api.assert_not_called()

    def test_peer_copy_respects_reserved_forced_rrsync_layout(self):
        sync = self.root / "sync.json"
        sync.write_text(json.dumps({
            "peer": {"user": "swarmsync", "address": "192.0.2.10"},
            "key": "/etc/swarm-sync/key", "known_hosts": "/etc/swarm-sync/known_hosts",
        }))
        self.config["swarm_sync_config"] = str(sync)
        with patch.object(backup, "command") as cmd:
            backup.peer_copy(self.config, Path(self.config["backup_root"]))
        args = cmd.call_args.args[0]
        self.assertEqual(args[-1], "swarmsync@192.0.2.10:observability/snapshots/victoriametrics/")
        self.assertIn("--delete", args)
        self.assertNotIn("/srv/swarm/observability/victoriametrics", args)

    def test_disk_ceiling_refuses_before_api(self):
        with patch.object(backup, "command", side_effect=self.cmd), \
             patch.object(backup, "size", return_value=10001), patch.object(backup, "api") as api:
            with self.assertRaises(RuntimeError):
                backup.backup(self.config)
            api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
