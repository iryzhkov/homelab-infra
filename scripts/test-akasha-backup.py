#!/usr/bin/env python3
"""Disposable Akasha packaging/restore, locking, reporting and template checks."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
assert os.environ.get("AKASHA_TEST_HOME") == os.environ.get("HOME")
assert Path(os.environ["HOME"]).is_relative_to(Path(os.environ["TMPDIR"]))


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


offsite = load("offsite", "roles/backup/files/homelab-akasha-offsite")
maintenance = load("maintenance", "roles/akasha/files/akasha-maintenance")
heartbeat = load("heartbeat", "roles/akasha/files/akasha-heartbeat")


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "akasha"
        self.state.mkdir(mode=0o700)
        with sqlite3.connect(self.state / "events.db") as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE receipts (id TEXT PRIMARY KEY, state TEXT)")
            db.execute("CREATE TABLE cursors (sequence INTEGER)")
            db.execute("INSERT INTO receipts VALUES ('stable-id', 'visible')")
            db.execute("INSERT INTO cursors VALUES (42)")
            db.commit()
            maintenance.snapshot(self.state)
        self.remote = self.root / "fake-b2"
        self.remote.mkdir(mode=0o700)
        self.work = self.root / "work"
        self.work.mkdir(mode=0o700)
        self.calls = []
        self.patchers = [
            mock.patch.object(offsite, "SOURCE", self.state / "backup/events.db"),
            mock.patch.object(offsite, "LOCK", self.state / "backup.lock"),
            mock.patch.object(offsite, "rclone", side_effect=self.transfer),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def transfer(self, verb, source, destination):
        self.calls.append((verb, str(source), str(destination)))
        self.assertEqual(verb, "copyto")
        remote_prefix = offsite.REMOTE + "/"
        if str(source).startswith(remote_prefix):
            shutil.copyfile(self.remote / str(source)[len(remote_prefix):], destination)
        else:
            shutil.copyfile(source, self.remote / str(destination)[len(remote_prefix):])

    def test_package_fetches_and_restores_receipts_cursors_under_shared_lock(self):
        original_copy = offsite.shutil.copyfileobj
        original_verify = offsite.verify_database
        def checked_copy(source, destination):
            with (self.state / "backup.lock").open("rb+") as lock:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            original_copy(source, destination)
        def checked_verify(path):
            # The verified private copy no longer holds up hourly replacement.
            with (self.state / "backup.lock").open("rb+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            original_verify(path)
        # These must never enter the remote, even when present.
        for name in ("events.db-wal", "events.db-shm", "events.db.partial"):
            (self.state / "backup" / name).write_text("do not package")
        with mock.patch.object(offsite.shutil, "copyfileobj", side_effect=checked_copy), \
                mock.patch.object(offsite, "verify_database", side_effect=checked_verify):
            manifest, age = offsite.package(self.work)
        self.assertLess(age, 3600)
        self.assertEqual(set(p.name for p in self.remote.iterdir()), {"events.db", "manifest.json"})
        self.assertEqual(len(self.calls), 4)
        with sqlite3.connect(self.work / "restored.db") as db:
            self.assertEqual(db.execute("SELECT * FROM receipts").fetchall(), [("stable-id", "visible")])
            self.assertEqual(db.execute("SELECT * FROM cursors").fetchall(), [(42,)])
        for path in self.work.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(manifest["snapshot_time"], offsite.SOURCE.stat().st_mtime)

    def test_snapshot_cannot_replace_generation_while_packager_holds_lock(self):
        with (self.state / "backup.lock").open("rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            code = ("import runpy; from pathlib import Path; "
                    "runpy.run_path(" + repr(str(ROOT / "roles/akasha/files/akasha-maintenance")) +
                    ")['snapshot'](Path(" + repr(str(self.state)) + "))")
            process = subprocess.Popen([sys.executable, "-c", code])
            try:
                time.sleep(0.2)
                self.assertIsNone(process.poll())
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                process.wait(timeout=10)
        self.assertEqual(process.returncode, 0)

    def test_stale_future_missing_unsafe_or_corrupt_source_never_uploads(self):
        for mutation in ("stale", "future", "mode", "symlink", "corrupt", "missing"):
            with self.subTest(mutation=mutation):
                maintenance.snapshot(self.state)
                source = offsite.SOURCE
                if mutation in ("stale", "future"):
                    timestamp = time.time() + (60 if mutation == "future" else -7201)
                    os.utime(source, (timestamp, timestamp))
                elif mutation == "mode":
                    source.chmod(0o644)
                elif mutation == "symlink":
                    source.unlink()
                    source.symlink_to(self.state / "events.db")
                elif mutation == "corrupt":
                    source.write_bytes(b"not sqlite")
                else:
                    source.unlink()
                self.calls.clear()
                with self.assertRaises((ValueError, OSError, sqlite3.Error)):
                    offsite.package(self.work)
                self.assertEqual(self.calls, [])

    def test_health_during_real_snapshot_rotation_and_failed_rotation(self):
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        opener.open.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
        original_connect = maintenance.sqlite3.connect
        observed = []
        def connect_during_gap(*args, **kwargs):
            if not offsite.SOURCE.exists():
                maintenance.health(self.state, "http://127.0.0.1/healthz")
                observed.append(True)
            return original_connect(*args, **kwargs)
        with mock.patch.object(maintenance.urllib.request, "build_opener", return_value=opener):
            with mock.patch.object(maintenance.sqlite3, "connect", side_effect=connect_during_gap):
                maintenance.snapshot(self.state)
            self.assertTrue(observed)
            # A failed rotation must fail health once its lock is released.
            with mock.patch.object(maintenance.sqlite3, "connect", side_effect=RuntimeError("fixture")):
                with self.assertRaises(RuntimeError):
                    maintenance.snapshot(self.state)
            with self.assertRaises((RuntimeError, OSError)):
                maintenance.health(self.state, "http://127.0.0.1/healthz")

    def test_health_reconciles_rotation_finishing_after_missing_snapshot_stat(self):
        source = offsite.SOURCE
        snapshot_bytes = source.read_bytes()
        source.unlink()
        real_stat = Path.stat
        observed = []
        with (self.state / "backup.lock").open("rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            lock.write(json.dumps({"started": time.time(), "snapshot_time": time.time()}).encode())
            lock.flush()
            def stat_finishing_rotation(path, *args, **kwargs):
                if path == source and not observed:
                    observed.append(True)
                    source.write_bytes(snapshot_bytes)
                    lock.seek(0)
                    lock.truncate()
                    lock.flush()
                    fcntl.flock(lock, fcntl.LOCK_UN)
                    raise FileNotFoundError("snapshot was missing before publication")
                return real_stat(path, *args, **kwargs)
            with mock.patch.object(Path, "stat", stat_finishing_rotation):
                maintenance.check_snapshot(self.state)
            self.assertTrue(observed)

    def test_invalid_or_empty_rotation_evidence_fails_closed(self):
        offsite.SOURCE.unlink()
        with (self.state / "backup.lock").open("rb+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for evidence in (b"", b"{", b"[]", b'{"started":true,"snapshot_time":null}'):
                with self.subTest(evidence=evidence):
                    lock.seek(0)
                    lock.truncate()
                    lock.write(evidence)
                    lock.flush()
                    with self.assertRaises((RuntimeError, ValueError, OSError)):
                        maintenance.check_snapshot(self.state)

    def test_rotation_grace_requires_live_lock_fresh_snapshot_and_bounded_duration(self):
        source = offsite.SOURCE
        source.unlink()
        now = time.time()
        with (self.state / "backup.lock").open("rb+") as lock:
            for held, started, snapshot_time, valid in (
                    (True, now, now - 3600, True),
                    (False, now, now - 3600, False),
                    (True, now - 301, now - 3600, False),
                    (True, now, now - 86401, False),
                    (True, now, now + 60, False),
                    (True, now + 60, now - 3600, False)):
                with self.subTest(held=held, started=started, snapshot_time=snapshot_time):
                    lock.seek(0)
                    lock.truncate()
                    lock.write(json.dumps({"started": started, "snapshot_time": snapshot_time}).encode())
                    lock.flush()
                    fcntl.flock(lock, fcntl.LOCK_EX if held else fcntl.LOCK_UN)
                    opener = mock.MagicMock()
                    opener.open.return_value.__enter__.return_value.status = 200
                    opener.open.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
                    with mock.patch.object(maintenance.urllib.request, "build_opener", return_value=opener):
                        if valid:
                            maintenance.health(self.state, "http://127.0.0.1/healthz")
                            opener.open.side_effect = OSError("not ready")
                            with self.assertRaises(OSError):
                                maintenance.health(self.state, "http://127.0.0.1/healthz")
                        else:
                            with self.assertRaises((RuntimeError, OSError)):
                                maintenance.health(self.state, "http://127.0.0.1/healthz")
            fcntl.flock(lock, fcntl.LOCK_UN)

    def test_fifo_source_fails_without_waiting_for_timeout(self):
        offsite.SOURCE.unlink()
        os.mkfifo(offsite.SOURCE, 0o600)
        started = time.monotonic()
        with self.assertRaises(ValueError):
            offsite.package(self.work)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.calls, [])

    def test_remote_failure_corruption_and_stale_manifest_are_rejected(self):
        offsite.package(self.work)
        manifest_path = self.remote / "manifest.json"
        original = manifest_path.read_text()
        for age in (-1, 86401):
            manifest = json.loads(original)
            manifest["snapshot_time"] = time.time() - age
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                offsite.verify_remote(self.work)
        manifest_path.write_text(original)
        (self.remote / "events.db").write_bytes(b"bad")
        with self.assertRaises(ValueError):
            offsite.verify_remote(self.work)
        with mock.patch.object(offsite, "rclone", side_effect=RuntimeError("upload failed")):
            with self.assertRaises(RuntimeError):
                offsite.package(self.work)

    def test_main_failure_cleans_staging_and_never_prints_transport_secrets(self):
        secret = "credential-must-not-be-logged"
        with mock.patch.object(offsite, "STATE", self.root / "stage"), \
                mock.patch.object(offsite, "require_encryption", side_effect=RuntimeError(secret)), \
                mock.patch.object(sys, "argv", ["offsite"]), \
                contextlib.redirect_stderr(io.StringIO()) as logs:
            self.assertEqual(offsite.main(), 1)
        self.assertNotIn(secret, logs.getvalue())
        self.assertFalse((self.root / "stage/work").exists())
        self.assertEqual((self.root / "stage").stat().st_mode & 0o777, 0o700)

    def test_encryption_guard_accepts_only_private_sse_b2_and_uses_no_argv(self):
        config = self.root / "rclone.conf"
        config.write_text("[b2]\naccount=fixture\nkey=secret\n")
        for private, encryption, valid in [
                ("allPrivate", {"mode": "SSE-B2", "algorithm": "AES256"}, True),
                ("allPublic", {"mode": "SSE-B2", "algorithm": "AES256"}, False),
                ("allPrivate", {"mode": "none"}, False)]:
            replies = [
                {"accountId": "fixture", "authorizationToken": "secret",
                 "apiInfo": {"storageApi": {"apiUrl": "https://api001.backblazeb2.com"}}},
                {"buckets": [{"bucketName": "igor-offsite-backup", "bucketType": private,
                              "defaultServerSideEncryption": {"value": encryption}}]}]
            opener = mock.MagicMock()
            opener.open.side_effect = [
                contextlib.nullcontext(io.StringIO(json.dumps(reply))) for reply in replies]
            with mock.patch.object(offsite, "RCLONE_CONFIG", str(config)), \
                    mock.patch.object(offsite.urllib.request, "build_opener", return_value=opener):
                if valid:
                    offsite.require_encryption()
                else:
                    with self.assertRaises(ValueError):
                        offsite.require_encryption()


class HeartbeatTest(unittest.TestCase):
    def test_results_and_transport_errors_never_leak_token_or_change_result(self):
        with tempfile.TemporaryDirectory() as directory:
            token = Path(directory) / "token"
            token.write_text("a" * 64)
            for result in ("success", "exit-code", "timeout", "signal"):
                opener = mock.MagicMock()
                opener.open.return_value.__enter__.return_value.status = 200
                with mock.patch.dict(os.environ, SERVICE_RESULT=result), \
                        mock.patch.object(sys, "argv", ["heartbeat", "--url", "http://192.0.2.1/external",
                                                       "--token-file", str(token)]), \
                        mock.patch.object(heartbeat.urllib.request, "build_opener", return_value=opener) as build:
                    self.assertEqual(heartbeat.main(), 0)
                    request = opener.open.call_args.args[0]
                    self.assertTrue(request.full_url.endswith("?success=" + str(result == "success").lower()))
                    self.assertEqual(request.headers["Authorization"], "Bearer " + "a" * 64)
                    self.assertEqual(build.call_args.args[0].proxies, {})
                    self.assertIsNone(build.call_args.args[1].redirect_request(None, None, 302, "", {}, "http://other/"))
                    opener.open.side_effect = RuntimeError("a" * 64)
                    with contextlib.redirect_stdout(io.StringIO()) as logs:
                        self.assertEqual(heartbeat.main(), 0)
                    self.assertNotIn("a" * 64, logs.getvalue())

    def test_units_report_from_stop_post_with_ignored_failure(self):
        for action, role in (("backup", "akasha"), ("health", "akasha"), ("offsite", "backup")):
            unit = (ROOT / f"roles/{role}/templates/akasha-{action}.service.j2").read_text()
            self.assertIn("ExecStopPost=-/usr/local/libexec/akasha-heartbeat", unit)
            self.assertIn(f"/etc/akasha/heartbeat-{action}", unit)

    def test_gatus_provisioning_and_single_external_list(self):
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["swarm_stacks"] = lambda hosts, groups: []
        env.filters["stack_hosts"] = lambda hosts, groups, name: []
        template = env.from_string((ROOT / "stacks/gatus/templates/config.yaml.j2").read_text())
        for host in ("homelab", "normandy"):
            for tokens in ({}, {"AKASHA_BACKUP_HEARTBEAT_TOKEN": "a" * 64},
                           {f"AKASHA_{name}_HEARTBEAT_TOKEN": "a" * 64 for name in ("BACKUP", "OFFSITE", "HEALTH")}):
                rendered = template.render(inventory_hostname=host, smart_gatus_host="normandy",
                    stack_secrets=tokens, hostvars={"homelab": {"ansible_host": "192.0.2.1"},
                    "normandy": {"ansible_host": "192.0.2.2"}, "oci": {"mesh_host": "192.0.2.3"}},
                    groups={"docker_hosts": [], "edge_proxy": [], "hypervisors": []},
                    traefik_static_routes=[], adguard_instances=[], mqtt_broker_ip="192.0.2.4",
                    edge_proxy_ip="192.0.2.5", smart_secrets={"GATUS_TOKEN": "fixture"},
                    smart_gatus_group="SMART", smart_heartbeat="45m")
                self.assertLessEqual(rendered.count("external-endpoints:"), 1)
                endpoints = yaml.safe_load(rendered).get("external-endpoints") or []
                endpoints = [e for e in endpoints if e["name"].startswith("akasha-")]
                self.assertEqual(len(endpoints), len(tokens) if host == "homelab" else 0)
                for endpoint in endpoints:
                    name = endpoint["name"].removeprefix("akasha-")
                    self.assertEqual(endpoint["heartbeat"]["interval"],
                                     {"backup": "2h", "offsite": "13h", "health": "3m"}[name])
                    self.assertEqual(endpoint["alerts"], [{"type": "discord", "failure-threshold": 1,
                        "success-threshold": 1, "send-on-resolved": True}])
        play = yaml.safe_load((ROOT / "playbooks/akasha-heartbeat.yml").read_text())[0]
        self.assertEqual(play["hosts"], "homelab")
        for task in play["tasks"]:
            self.assertIs(task["no_log"], True)
        self.assertIs(play["tasks"][-1]["diff"], False)
        copy = play["tasks"][-1]["ansible.builtin.copy"]
        for item, expected_group, expected_mode in (
                ("offsite", "root", "0600"), ("backup", "akasha", "0640"), ("health", "akasha", "0640")):
            self.assertEqual(copy["owner"], "root")
            self.assertEqual(jinja2.Template(copy["group"]).render(item=item), expected_group)
            self.assertEqual(jinja2.Template(copy["mode"]).render(item=item), expected_mode)
        narrow = yaml.safe_load((ROOT / "playbooks/akasha-backup.yml").read_text())[0]
        self.assertEqual(narrow["tasks"][0]["ansible.builtin.include_role"]["tasks_from"], "akasha")


if __name__ == "__main__":
    unittest.main()
