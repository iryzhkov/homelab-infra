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
import shutil
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
        self.base = self.commit(None, {"README.md": b"data only\n", p.BUNDLE: envelope().encode()})
        self.publisher.run(["/usr/bin/git", "init", "--bare", str(self.publisher.grants_repo)])
        self.grants_base = self.commit(None, {p.GRANTS: b'[{"fixture":true}]'}, grants=True)
        self.publisher.git("update-ref", "refs/publisher/grants", self.grants_base, grants=True)
        self.publisher.git("update-ref", "refs/publisher/main", self.base)
        # Git fixtures are intentionally unsigned. Production authentication is
        # separately exercised with forged envelopes and the real Node verifier.
        self.publisher.authenticate_remote = lambda raw: p.payload(raw.decode())
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
            if "fetch" in args:
                assert env is not None
                if p.FLEET_REMOTE in args:
                    self.assertIn("--git-dir=" + str(self.publisher.grants_repo), args)
                    self.assertIn("keys/fleet-read-key", env["GIT_SSH_COMMAND"])
                    self.assertNotIn("trust-deploy-key", env["GIT_SSH_COMMAND"])
                else:
                    self.assertIn(p.REMOTE, args)
                    self.assertIn("--git-dir=" + str(self.publisher.repo), args)
                    self.assertIn("keys/trust-deploy-key", env["GIT_SSH_COMMAND"])
                    self.assertNotIn("fleet-read-key", env["GIT_SSH_COMMAND"])
                return b""
            if "push" in args:
                assert env is not None
                self.assertIn(p.REMOTE, args)
                self.assertNotIn(p.FLEET_REMOTE, args)
                self.assertIn("keys/trust-deploy-key", env["GIT_SSH_COMMAND"])
                self.assertNotIn("fleet-read-key", env["GIT_SSH_COMMAND"])
                self.calls.append("push")
                if self.fail_push:
                    raise subprocess.CalledProcessError(1, args)
                return b"ok"
            return original(args, data, env)
        self.publisher.run = run

    def commit(self, parent, paths, grants=False):
        def git(*args, **kwargs):
            return self.publisher.git(*args, grants=grants, **kwargs)
        git("read-tree", parent or "--empty", index=True)
        for path, data in paths.items():
            blob = git("hash-object", "-w", "--stdin", data=data).decode().strip()
            git("update-index", "--add", "--cacheinfo", "100644," + blob + "," + path, index=True)
        tree = git("write-tree", index=True).decode().strip()
        return git("commit-tree", tree, *(["-p", parent] if parent else []), data=b"fixture\n").decode().strip()

    def test_forged_remote_high_water_and_timestamp_cannot_advance_or_hide_age(self):
        node = shutil.which("node")
        self.assertIsNotNone(node)
        verifier = p.Publisher(self.publisher.config | {"node": node, "anchors": [{
            "kid": "deployment-2026-10b",
            "public_key": {"kty": "OKP", "crv": "Ed25519", "x": "A" * 43}}]})
        header = base64.urlsafe_b64encode(json.dumps({
            "alg": "EdDSA", "typ": "chaldea-trust+jws", "kid": "deployment-2026-10b"}).encode()).decode().rstrip("=")
        forged = header + "." + envelope(p.MAX_SEQUENCE, -86400).split(".")[1] + "." + "A" * 86
        bad = self.commit(self.base, {p.BUNDLE: forged.encode()})
        self.publisher.authenticate_remote = verifier.authenticate_remote
        self.assertIsNone(self.publisher.remote_bundle(bad))
        self.assertEqual(p.next_sequence(self.root / "state/publisher.sqlite", 0), 1)
        notices = []
        self.publisher.notify = lambda *args: notices.append(args)
        self.publisher.age_alert(bad)
        self.assertEqual(len(notices), 1)
        # Malformed main can be repaired from durable local high water.
        self.publisher.publish(bad)
        self.assertEqual(p.payload((self.root / "state/published.jws").read_text())["sequence"], 1)

    def test_malformed_remote_does_not_block_republication(self):
        self.publisher.authenticate_remote = lambda raw: p.payload(raw.decode())
        bad = self.commit(self.base, {p.BUNDLE: b"garbage"})
        self.publisher.publish(bad)
        self.assertIn("push", self.calls)

    def test_path_mode_and_symlink_are_refused_by_commit_guard(self):
        self.publisher.git("read-tree", self.base, index=True)
        blob = self.publisher.git("hash-object", "-w", "--stdin", data=b"fixture").decode().strip()
        for mode in ("100755", "120000"):
            self.publisher.git("update-index", "--cacheinfo", mode + "," + blob + "," + p.BUNDLE, index=True)
            tree = self.publisher.git("write-tree", index=True).decode().strip()
            commit = self.publisher.git("commit-tree", tree, "-p", self.base, data=b"mode fixture").decode().strip()
            with self.assertRaisesRegex(RuntimeError, "nonregular"):
                self.publisher.guard_commit(self.base, commit)

    def test_bootstrap_private_file_rejects_modes_symlinks_and_multiple_links(self):
        path = self.root / "fixture"
        path.write_text("metadata only")
        path.chmod(0o600)
        p.private_file(path)
        path.chmod(0o640)
        with self.assertRaises(RuntimeError):
            p.private_file(path)
        path.chmod(0o600)
        link = self.root / "link"
        link.symlink_to(path)
        with self.assertRaises(RuntimeError):
            p.private_file(link)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(RuntimeError):
            p.private_file(path)

    def test_root_provisioning_stays_outside_publisher_descendants(self):
        tasks = yaml.safe_load((ROOT / "roles/chaldea_publisher/tasks/main.yml").read_text())
        directories = next(t for t in tasks if t["name"] == "Create publisher subdirectories as the locked identity")
        self.assertEqual(directories["become_user"], "chaldea-publisher")
        self.assertFalse(directories["ansible.builtin.file"]["follow"])
        known = next(t for t in tasks if t["name"] == "Install independently pinned GitHub host key")
        self.assertEqual(known["ansible.builtin.copy"]["dest"], "{{ chaldea_publisher_config_root }}/known_hosts")

    def test_feed_failure_falls_back_without_retry(self):
        calls = []
        def fail(args, *other):
            calls.append(args)
            raise subprocess.CalledProcessError(1, args)
        self.publisher.run = fail
        with mock.patch.object(p.shutil, "which", return_value="/usr/local/bin/feed"):
            with mock.patch("sys.stderr") as journal:
                self.publisher.notify("fixture failure")
        self.assertEqual(len(calls), 1)
        self.assertIn("alert route=journal", "".join(str(c) for c in journal.write.call_args_list))

    def test_pin_checker_rejects_placeholders_mapping_drift_and_mismatched_keys(self):
        checker_loader = importlib.machinery.SourceFileLoader("pins", str(ROOT / "scripts/check-trust-pins.py"))
        checker_spec = importlib.util.spec_from_loader(checker_loader.name, checker_loader)
        assert checker_spec is not None
        checker = importlib.util.module_from_spec(checker_spec)
        checker_loader.exec_module(checker)
        infra, fleet = self.root / "infra", self.root / "fleet"
        (infra / "vars").mkdir(parents=True)
        (fleet / "inventory/group_vars").mkdir(parents=True)
        values = yaml.safe_load((ROOT / "vars/chaldea-runtime.yml").read_text())
        values["chaldea_access_trust_enabled"] = False
        # Start from placeholders whatever the repository currently pins, so the
        # refusal is tested independently of the live anchor values.
        values["chaldea_deployment_anchor_public_jwk"] = {
            "kty": "OKP", "crv": "Ed25519", "x": "REPLACE_WITH_NORMANDY_PUBLIC_JWK_X"}
        values["chaldea_deployment_anchor_spki_sha256"] = "REPLACE_WITH_NORMANDY_SPKI_SHA256"
        def save(other=None):
            (infra / "vars/chaldea-runtime.yml").write_text(yaml.safe_dump(values))
            (fleet / "inventory/group_vars/all.yml").write_text(yaml.safe_dump(other or values))
        save()
        with self.assertRaisesRegex(ValueError, "placeholder"):
            checker.check(fleet, infra)
        public = {"kty": "OKP", "crv": "Ed25519", "x": "A" * 43}
        values["chaldea_deployment_anchor_public_jwk"] = public
        import hashlib
        values["chaldea_deployment_anchor_spki_sha256"] = hashlib.sha256(
            bytes.fromhex("302a300506032b6570032100") + bytes(32)).hexdigest()
        save()
        checker.check(fleet, infra)
        save(values | {"chaldea_deployment_anchor_kid": "mismatch"})
        with self.assertRaisesRegex(ValueError, "mismatch"):
            checker.check(fleet, infra)
        save(values | {"chaldea_access_trust_repository": "ssh://git@github.com/iryzhkov/chaldea-fleet-config.git"})
        with self.assertRaisesRegex(ValueError, "mismatch"):
            checker.check(fleet, infra)
        saved_repository = values["chaldea_access_trust_repository"]
        values["chaldea_access_trust_repository"] = "ssh://git@github.com/iryzhkov/chaldea-fleet-config.git"
        save()
        with self.assertRaisesRegex(ValueError, "data-only"):
            checker.check(fleet, infra)
        values["chaldea_access_trust_repository"] = saved_repository
        values["chaldea_access_trust_anchors"] = [{"kid": "stale", "public_key": public}]
        save()
        with self.assertRaisesRegex(ValueError, "mapping"):
            checker.check(fleet, infra)

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

    def test_reissue_floor_preserves_global_high_water_and_emergency_empty_uses_guarded_push(self):
        path = self.root / "state/publisher.sqlite"
        self.assertEqual(p.next_sequence(path, 8, 92), 93)
        for floor in (-1, True, p.MAX_SEQUENCE):
            with self.assertRaises(RuntimeError):
                p.next_sequence(path, 8, floor)
        self.publisher.config["sequence_floor"] = 92
        self.publisher.publish(self.base, emergency_empty=True)
        self.assertEqual((self.root / "state/grants.json").read_text().strip(), "[]")
        self.assertEqual(p.payload((self.root / "state/published.jws").read_text())["sequence"], 93)
        self.assertEqual(self.calls, ["timesyncd", "clock-health", "sign-bundle", "install-bundle", "push"])

    def test_trust_fetch_uses_only_the_data_repository_credential(self):
        self.assertEqual(self.publisher.fetch(), self.base)

    def test_bootstraps_readme_only_trust_main_from_separate_read_only_grants(self):
        readme_only = self.commit(None, {"README.md": b"data only\n"})
        self.publisher.publish(readme_only)
        self.assertEqual((self.root / "state/grants.json").read_bytes(), b'[{"fixture":true}]')
        self.assertEqual(p.payload((self.root / "state/published.jws").read_text())["sequence"], 1)
        self.assertIn("push", self.calls)

    def test_fleet_fetch_failure_preserves_bundle_and_never_pushes(self):
        saved = self.root / "state/published.jws"
        saved.write_text("previous")
        def fail():
            raise RuntimeError("fleet unavailable")
        self.publisher.fetch_grants = fail
        with self.assertRaisesRegex(RuntimeError, "fleet unavailable"):
            self.publisher.publish(self.base)
        self.assertEqual(saved.read_text(), "previous")
        self.assertNotIn("sign-bundle", self.calls)
        self.assertNotIn("push", self.calls)
        # Emergency revocation does not depend on reachable/nonempty fleet grants.
        self.publisher.publish(self.base, emergency_empty=True)
        self.assertIn("push", self.calls)

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

    def test_enabled_bootstrap_check_retains_the_service_identity(self):
        import pwd
        try:
            test_user = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError:
            self.skipTest("UID NSS absent in job namespace; complete check runs in CI")
        tasks = yaml.safe_load((ROOT / "roles/chaldea_publisher/tasks/main.yml").read_text())
        stat = next(t for t in tasks if t["name"] == "Inspect publisher bootstrap files before activation")
        self.assertIs(stat["become"], True)
        self.assertEqual(stat["become_user"], "chaldea-publisher")
        guard = next(t for t in tasks if t["name"] == "Require private initialized bootstrap before timer activation")
        (self.root / "keys").mkdir(mode=0o700)
        for name in ("keys/deployment-2026-10b.pem", "keys/trust-deploy-key", "keys/fleet-read-key", "state/verify.sqlite"):
            fixture = self.root / name
            fixture.write_text("metadata fixture, never a private key")
            fixture.chmod(0o600)
        variables = {"chaldea_publisher_enabled": True, "chaldea_publisher_root": str(self.root),
                     "chaldea_deployment_anchor_kid": "deployment-2026-10b",
                     "ansible_become_user": test_user,
                     "ansible_facts": {"getent_passwd": {"chaldea-publisher": ["x", str(os.geteuid())]}}}
        play = self.root / "enabled-check.yml"
        play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                        "vars": variables, "tasks": [stat, guard]}]))
        result = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local",
                                 str(play), "--check", "--diff"], capture_output=True, text=True,
                                env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(self.root / "local"),
                                     "ANSIBLE_REMOTE_TEMP": str(self.root / "remote")})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
