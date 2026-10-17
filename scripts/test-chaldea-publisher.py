#!/usr/bin/python3
"""Disposable publisher guard tests. No private keys, SSH or host services."""
import base64
from contextlib import closing
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import sys
import time
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("publisher", str(ROOT / "roles/chaldea_publisher/files/chaldea-publisher"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
p = importlib.util.module_from_spec(spec)
loader.exec_module(p)


def envelope(number=1, age=0):
    body = {"sequence": number, "published_at": int(time.time()) - age,
            "expires_at": int(time.time()) - age + 86400, "keys": []}
    return "synthetic." + base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=") + ".fixture"


def state_file(path, number=None):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE access_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO access_meta VALUES ('format', 'chaldea-access-state-v1')")
        if number:
            db.execute("INSERT INTO access_meta VALUES ('trust', ?)", (json.dumps({"sequence": number}),))
        db.commit()
    path.chmod(0o600)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(os.environ["HOME"]).resolve()
        self.assertEqual(self.home, Path(os.environ["ROLLOUT_TEST_HOME"]).resolve())
        self.assertIn(".scratch", str(self.home))
        self.temp = tempfile.TemporaryDirectory(dir=self.home)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "state").mkdir(mode=0o700)
        self.publisher = p.Publisher({"root": str(self.root), "node": "/synthetic/node",
                                     "runtime": "/synthetic/runtime", "kid": "deployment-2026-10b"})
        self.publisher.run(["/usr/bin/git", "init", "--bare", str(self.publisher.repo)])
        state_file(self.root / "state/publisher.sqlite")
        self.base = self.commit(None, {"access-trust/issuer-grants.json": b'[{"fixture":true}]',
                                       p.BUNDLE: envelope().encode()})
        self.publisher.git("update-ref", "refs/publisher/main", self.base)
        self.calls = []
        self.unhealthy = False
        self.fail_push = False
        original = self.publisher.run

        def run(args, data=None, env=None):
            if args[0] == "/usr/bin/systemctl":
                self.calls.append("timesyncd")
                return b"active\n"
            if args[0] == "/synthetic/node":
                command = args[2]
                self.calls.append(command)
                opts = dict(zip(args[3::2], args[4::2]))
                output = Path(opts["--output"])
                if command == "clock-health":
                    output.write_text(json.dumps({"healthy": not self.unhealthy, "offsetSeconds": 0,
                                                  "checked_at": int(time.time())}))
                elif command == "sign-bundle":
                    output.write_text(envelope(int(opts["--sequence"])))
                elif command == "install-bundle":
                    self.assertEqual(opts["--anchor-file"], str(self.root / "state/anchors.json"))
                    output.write_bytes(Path(opts["--bundle-file"]).read_bytes())
                return b""
            if "push" in args:
                self.calls.append("push")
                if self.fail_push:
                    raise subprocess.CalledProcessError(1, args)
                return b"ok"
            return original(args, data, env)
        self.publisher.run = run

    def commit(self, parent, paths):
        git = self.publisher.git
        git("read-tree", parent or "--empty", index=True)
        for path, data in paths.items():
            blob = git("hash-object", "-w", "--stdin", data=data).decode().strip()
            git("update-index", "--add", "--cacheinfo", "100644," + blob + "," + path, index=True)
        tree = git("write-tree", index=True).decode().strip()
        return git("commit-tree", tree, *(["-p", parent] if parent else []), data=b"fixture\n").decode().strip()

    def test_push_guard_refuses_other_paths_and_merge_parent(self):
        good = self.commit(self.base, {p.BUNDLE: envelope(2).encode()})
        self.publisher.guard_commit(self.base, good)
        bad = self.commit(self.base, {p.BUNDLE: envelope(2).encode(), "site.yml": b"unrelated\n"})
        with self.assertRaisesRegex(RuntimeError, "another path"):
            self.publisher.guard_commit(self.base, bad)
        with self.assertRaisesRegex(RuntimeError, "parent"):
            self.publisher.guard_commit(good, bad)

    def test_sequence_uses_durable_state_and_remote_high_water(self):
        path = self.root / "state/publisher.sqlite"
        self.assertEqual(p.next_sequence(path, 8), 9)
        with closing(sqlite3.connect(path)) as db:
            db.execute("INSERT INTO access_meta VALUES ('trust', ?)", (json.dumps({"sequence": 12}),))
            db.commit()
        self.assertEqual(p.next_sequence(path, 8), 13)
        with self.assertRaises(RuntimeError):
            p.next_sequence(path, p.MAX_SEQUENCE)
        path.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            p.next_sequence(path, 1)
        self.assertFalse(path.exists())

    def test_verified_envelope_precedes_guarded_push(self):
        self.publisher.publish(self.base)
        self.assertEqual(self.calls, ["timesyncd", "clock-health", "sign-bundle", "install-bundle", "push"])
        self.assertEqual(p.payload((self.root / "state/published.jws").read_text())["sequence"], 2)

    def test_clock_failure_preserves_previous_bundle_and_never_signs_or_pushes(self):
        saved = self.root / "state/published.jws"
        saved.write_text("previous")
        self.unhealthy = True
        with self.assertRaisesRegex(RuntimeError, "clock"):
            self.publisher.publish(self.base)
        self.assertEqual(saved.read_text(), "previous")
        self.assertNotIn("sign-bundle", self.calls)
        self.assertNotIn("push", self.calls)

    def test_failed_push_keeps_previous_bundle(self):
        saved = self.root / "state/published.jws"
        saved.write_text("previous")
        self.fail_push = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.publisher.publish(self.base)
        self.assertEqual(saved.read_text(), "previous")

    def test_verification_failure_never_pushes(self):
        saved = self.root / "state/published.jws"
        saved.write_text("previous")
        original = self.publisher.maintenance
        def maintenance(command, *args):
            if command == "install-bundle":
                raise RuntimeError("untrusted envelope")
            return original(command, *args)
        self.publisher.maintenance = maintenance
        with self.assertRaisesRegex(RuntimeError, "untrusted"):
            self.publisher.publish(self.base)
        self.assertEqual(saved.read_text(), "previous")
        self.assertNotIn("push", self.calls)

    def test_same_sequence_candidate_is_refused(self):
        original = self.publisher.maintenance
        def maintenance(command, *args):
            result = original(command, *args)
            if command == "install-bundle":
                (self.root / "state/verified.jws").write_text(envelope(1))
            return result
        self.publisher.maintenance = maintenance
        with self.assertRaisesRegex(RuntimeError, "sequence"):
            self.publisher.publish(self.base)
        self.assertNotIn("push", self.calls)

    def test_stale_alert_once_until_recovery(self):
        notices = []
        self.publisher.notify = lambda *args: notices.append(args)
        stale = self.commit(self.base, {p.BUNDLE: envelope(2, 43201).encode()})
        self.publisher.age_alert(stale)
        self.publisher.age_alert(stale)
        self.assertEqual(len(notices), 1)
        self.publisher.age_alert(self.base)
        self.publisher.age_alert(stale)
        self.assertEqual(len(notices), 2)

    def test_unhealthy_stale_future_and_large_offset_clock(self):
        path = self.root / "health"
        for changes in ({"healthy": False}, {"checked_at": 60}, {"checked_at": 101},
                        {"offsetSeconds": 31}, {"offsetSeconds": float("nan")}):
            path.write_text(json.dumps({"healthy": True, "checked_at": 100, "offsetSeconds": 0} | changes))
            with self.assertRaises(RuntimeError):
                p.clock_ok(path, 100)

    def test_target_is_separate_and_check_mode_is_local(self):
        inventory = yaml.safe_load((ROOT / "inventory/hosts.yml").read_text())
        groups = inventory["all"]["children"]
        self.assertEqual(list(groups["chaldea_publishers"]["hosts"]), ["omarchy-normandy"])
        self.assertEqual(groups["docker_hosts"]["hosts"]["normandy"]["ansible_host"], "192.168.70.234")
        # Full playbook, local synthetic inventory. No key creation, sudo, network or systemctl.
        # Root NSS can be intentionally absent in Chaldea's namespace; CI exercises this path.
        import pwd
        try:
            pwd.getpwnam("root")
        except KeyError:
            self.skipTest("root NSS absent in job namespace; complete check runs in CI")
        inv = self.root / "inventory.yml"
        inv.write_text(yaml.safe_dump({"all": {"children": {"chaldea_publishers": {"hosts": {
            "omarchy-normandy": {"ansible_connection": "local", "ansible_become": False,
                                "ansible_python_interpreter": sys.executable}}}}}}))
        variables = {
            "chaldea_publisher_root": str(self.root / "publisher"),
            "chaldea_publisher_config_root": str(self.root / "config"),
            "chaldea_publisher_program": str(self.root / "bin/publisher"),
            "chaldea_publisher_unit_dir": str(self.root / "units"),
        }
        proc = subprocess.run(["ansible-playbook", "-i", str(inv), "playbooks/chaldea-signer.yml",
                               "--check", "--diff", "-e", json.dumps(variables)],
                              cwd=ROOT, capture_output=True, text=True, env={
                                  **os.environ, "ANSIBLE_LOCAL_TEMP": str(self.root / "local"),
                                  "ANSIBLE_REMOTE_TEMP": str(self.root / "remote")})
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertFalse((self.root / "publisher").exists())


if __name__ == "__main__":
    unittest.main()
