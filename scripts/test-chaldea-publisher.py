#!/usr/bin/python3
"""Disposable publisher guard tests. No private keys, SSH or host services."""
import base64
from contextlib import closing
import importlib.machinery
import importlib.util
import itertools
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
        self.timesyncd_states = []
        self.fail_push = False
        original = self.publisher.run

        def run(args, data=None, env=None):
            if args[0] == "/usr/bin/systemctl":
                self.calls.append("timesyncd")
                return self.timesyncd_states.pop(0) if self.timesyncd_states else b"active\n"
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

    def run_main(self, *args):
        config = self.root / "config.json"
        config.write_text(json.dumps(self.publisher.config))
        with mock.patch.object(sys, "argv", ["chaldea-publisher", "--config", str(config), *args]), \
                mock.patch.object(p, "Publisher", return_value=self.publisher), \
                mock.patch.object(self.publisher, "bootstrap"), \
                mock.patch.object(sys, "stderr") as journal:
            result = p.main()
        logs = "".join(call.args[0] for call in journal.write.call_args_list)
        return result, logs

    def heartbeat_fixture(self):
        token_file = self.root / "heartbeat-token"
        token_file.write_text("a" * 64 + "\n")
        self.publisher.config.update(heartbeat_token_file=str(token_file),
            heartbeat_url="http://192.0.2.1:3002/api/v1/endpoints/chaldea_trust-publisher/external")
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        return opener

    def test_successful_publication_pushes_heartbeat_after_publication_without_token_in_argv_or_logs(self):
        opener = self.heartbeat_fixture()

        def opened(request, timeout):
            self.assertIn("push", self.calls)
            self.assertTrue((self.publisher.state / "published.jws").exists())
            self.assertTrue(request.full_url.endswith("/chaldea_trust-publisher/external?success=true"))
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + "a" * 64)
            self.assertEqual(timeout, 10)
            return mock.MagicMock(__enter__=lambda _: mock.Mock(status=200))

        opener.open.side_effect = opened
        with mock.patch.object(p.urllib.request, "build_opener", return_value=opener), \
                mock.patch.object(self.publisher, "run", wraps=self.publisher.run) as commands:
            result, logs = self.run_main()
        self.assertEqual(result, 0)
        opener.open.assert_called_once()
        self.assertIn("heartbeat=sent success=true", logs)
        self.assertNotIn("a" * 64, logs)
        self.assertNotIn("a" * 64, str(commands.call_args_list))

    def test_failed_publication_pushes_failure_heartbeat(self):
        opener = self.heartbeat_fixture()
        self.fail_push = True
        with mock.patch.object(p.urllib.request, "build_opener", return_value=opener):
            result, logs = self.run_main()
        self.assertEqual(result, 1)
        opener.open.assert_called_once()
        self.assertTrue(opener.open.call_args.args[0].full_url.endswith("?success=false"))
        self.assertFalse((self.publisher.state / "published.jws").exists())
        self.assertIn("heartbeat=sent success=false", logs)
        self.assertNotIn("a" * 64, logs)

    def test_heartbeat_error_never_changes_publication_result_or_logs_credentials(self):
        for failure in (False, True):
            with self.subTest(publication_fails=failure):
                opener = self.heartbeat_fixture()
                opener.open.side_effect = RuntimeError("Bearer " + "a" * 64)
                self.fail_push = failure
                with mock.patch.object(p.urllib.request, "build_opener", return_value=opener):
                    result, logs = self.run_main()
                self.assertEqual(result, int(failure))
                opener.open.assert_called_once()
                self.assertIn("heartbeat=error cause=RuntimeError", logs)
                self.assertNotIn("a" * 64, logs)
                if not failure:
                    self.assertTrue((self.publisher.state / "published.jws").exists())

    def test_missing_url_disables_heartbeat_without_guessing_an_address(self):
        self.heartbeat_fixture()
        del self.publisher.config["heartbeat_url"]
        with mock.patch.object(p.urllib.request, "build_opener") as opener:
            result, logs = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(logs.count("heartbeat=disabled"), 1)
        opener.assert_not_called()

    def test_missing_token_disables_heartbeat_once_per_publication_run(self):
        self.heartbeat_fixture()
        self.publisher.config["heartbeat_token_file"] = str(self.root / "absent-token")
        with mock.patch.object(p.urllib.request, "build_opener") as opener:
            result, logs = self.run_main()
        self.assertEqual(result, 0)
        self.assertEqual(logs.count("heartbeat=disabled"), 1)
        opener.assert_not_called()

    def test_age_and_alert_checks_never_push_even_on_failure(self):
        self.heartbeat_fixture()
        for mode in ("--check-age", "--test-alert"):
            for failure in (False, True):
                with self.subTest(mode=mode, failure=failure), \
                        mock.patch.object(self.publisher, "fetch", side_effect=RuntimeError("fetch failed")
                                          if failure else None, return_value=self.base), \
                        mock.patch.object(self.publisher, "notify"), \
                        mock.patch.object(p.urllib.request, "build_opener") as opener:
                    self.run_main(mode)
                    opener.assert_not_called()

    def test_heartbeat_disallows_redirects_and_environment_proxies(self):
        opener = self.heartbeat_fixture()
        with mock.patch.object(p.urllib.request, "build_opener", return_value=opener) as build:
            self.publisher.heartbeat(True)
        self.assertEqual(build.call_args.args[0].proxies, {})
        redirect = build.call_args.args[1]
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "http://elsewhere/"))

    def test_heartbeat_provisioning_uses_gatus_sops_and_suppresses_secret_output(self):
        play = yaml.safe_load((ROOT / "playbooks/chaldea-publisher-heartbeat.yml").read_text())[0]
        self.assertEqual(play["hosts"], "chaldea_publishers")
        self.assertIs(play["become"], True)
        load, guard, install = play["tasks"]
        self.assertTrue(load["community.sops.load_vars"]["file"].endswith("/secrets/gatus-alerting.sops.yaml"))
        self.assertEqual(load["delegate_to"], "localhost")
        for task in (load, guard, install):
            self.assertIs(task["no_log"], True)
        self.assertIs(install["diff"], False)
        copy = install["ansible.builtin.copy"]
        self.assertEqual(copy["dest"], "/etc/chaldea-publisher/heartbeat-token")
        self.assertEqual((copy["owner"], copy["group"], copy["mode"]),
                         ("root", "chaldea-publisher", "0640"))
        self.assertIs(copy["follow"], False)
        self.assertIs(copy["backup"], False)

    def test_gatus_template_renders_one_external_list_and_immediate_discord_alert(self):
        import jinja2
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["swarm_stacks"] = lambda hosts, groups: []
        env.filters["stack_hosts"] = lambda hosts, groups, name: []
        template = env.from_string((ROOT / "stacks/gatus/templates/config.yaml.j2").read_text())
        token_contexts = [{}, {"stack_secrets": {}}]
        token_contexts += [{"stack_secrets": {"CHALDEA_PUBLISHER_HEARTBEAT_TOKEN": token}}
                           for token in ("", None, "   ", "a" * 64)]
        for (host, smart_host), token_context in itertools.product(
                (("homelab", "normandy"), ("normandy", "normandy"),
                 ("oci", "normandy"), ("homelab", "homelab")), token_contexts):
            with self.subTest(host=host, smart_host=smart_host, token_context=token_context):
                rendered = template.render(**token_context,
                    inventory_hostname=host, smart_gatus_host=smart_host,
                    hostvars={"homelab": {"ansible_host": "192.0.2.1"},
                              "normandy": {"ansible_host": "192.0.2.2"},
                              "oci": {"mesh_host": "192.0.2.3"},
                              "pve": {"smart_drives": [{"model": "disk", "serial": "fixture"}]}},
                    groups={"docker_hosts": [], "edge_proxy": [], "hypervisors": ["pve"]},
                    traefik_static_routes=[], adguard_instances=[], mqtt_broker_ip="192.0.2.4",
                    edge_proxy_ip="192.0.2.5", smart_secrets={"GATUS_TOKEN": "fixture"},
                    smart_gatus_group="SMART", smart_heartbeat="45m")
                self.assertLessEqual(rendered.count("external-endpoints:"), 1)
                endpoints = yaml.safe_load(rendered).get("external-endpoints", [])
                publisher = [e for e in endpoints if e["name"] == "trust-publisher"]
                token = token_context.get("stack_secrets", {}).get("CHALDEA_PUBLISHER_HEARTBEAT_TOKEN")
                self.assertEqual(len(publisher), int(host == "homelab" and bool(token and token.strip())))
                self.assertEqual(len([e for e in endpoints if e["group"] == "SMART"]),
                                 int(host == smart_host))
                if publisher:
                    self.assertEqual(publisher[0]["heartbeat"]["interval"], "13h")
                    self.assertEqual(publisher[0]["group"], "chaldea")
                    self.assertEqual(publisher[0]["token"], "${CHALDEA_PUBLISHER_HEARTBEAT_TOKEN}")
                    self.assertEqual(publisher[0]["alerts"], [{"type": "discord",
                        "failure-threshold": 1, "success-threshold": 1, "send-on-resolved": True}])

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

    def test_alert_never_depends_on_retired_feed_or_its_issuer(self):
        calls = []
        def fail(args, *other):
            calls.append(args)
            raise subprocess.CalledProcessError(1, args)
        self.publisher.run = fail
        with mock.patch.object(p.shutil, "which", return_value="/usr/local/bin/feed"):
            with mock.patch("sys.stderr") as journal:
                self.publisher.notify("fixture failure")
        self.assertEqual(calls, [])
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
        with mock.patch.object(self.publisher, "pause") as sleep, \
                self.assertRaisesRegex(RuntimeError, "clock"):
            self.publisher.publish(self.base)
        self.assertEqual(saved.read_text(), "previous")
        self.assertNotIn("sign-bundle", self.calls)
        self.assertNotIn("push", self.calls)
        # The sample right before signing is single-shot; the wait happens earlier.
        self.assertEqual(self.calls, ["timesyncd", "clock-health"])
        sleep.assert_not_called()

    def fake_clock(self, check_seconds=0.0):
        # check_seconds models the time each failed timesyncd/runtime check takes.
        now = [0.0]
        def pause(seconds):
            now[0] += seconds
        def monotonic():
            value = now[0]
            now[0] += check_seconds
            return value
        return now, mock.patch.multiple(self.publisher, pause=mock.Mock(side_effect=pause),
                                        monotonic=mock.Mock(side_effect=monotonic))

    def test_clock_wait_is_bounded_and_outlasts_the_longest_poll(self):
        self.unhealthy = True
        now, clock = self.fake_clock()
        with clock, mock.patch.object(sys, "stderr"), self.assertRaisesRegex(RuntimeError, "clock"):
            self.publisher.await_clock()
        self.assertEqual(self.calls.count("clock-health"), 19)
        self.assertEqual(self.calls.count("timesyncd"), 19)
        self.assertEqual(now[0], p.CLOCK_WAIT_SECONDS)

    def test_slow_checks_still_sample_at_the_deadline(self):
        # A healthy poll at 2048 s must be seen even when each check takes time.
        now, clock = self.fake_clock(check_seconds=0.1)
        original = self.publisher.check_clock
        seen = []
        def check_clock(health):
            seen.append(now[0])
            self.unhealthy = now[0] < 2048
            return original(health)
        self.publisher.check_clock = check_clock
        with clock, mock.patch.object(sys, "stderr"):
            self.publisher.await_clock()
        self.assertGreaterEqual(seen[-1], 2048)
        self.assertLessEqual(seen[-1], p.CLOCK_WAIT_SECONDS + 1)

    def test_exhausted_wait_fails_once_before_fetching(self):
        opener = self.heartbeat_fixture()
        self.unhealthy = True
        fetch = self.publisher.fetch
        self.publisher.fetch = lambda: (self.calls.append("fetch"), fetch())[1]
        now, clock = self.fake_clock()
        with clock, mock.patch.object(p.urllib.request, "build_opener", return_value=opener):
            result, logs = self.run_main()
        self.assertEqual(result, 1)
        self.assertNotIn("fetch", self.calls)
        self.assertNotIn("sign-bundle", self.calls)
        opener.open.assert_called_once()
        self.assertTrue(opener.open.call_args.args[0].full_url.endswith("?success=false"))
        self.assertEqual(logs.count("phase=clock-wait cause=unhealthy clock"), 1)

    def test_age_check_never_waits_for_the_clock(self):
        self.unhealthy = True
        with mock.patch.object(self.publisher, "pause") as sleep:
            result, logs = self.run_main("--check-age")
        self.assertEqual(result, 0, logs)
        sleep.assert_not_called()
        self.assertNotIn("clock-health", self.calls)

    def test_spike_flagged_clock_waits_before_fetching_inputs(self):
        original = self.publisher.maintenance
        spikes = [True, True]
        def maintenance(command, *args):
            if command == "clock-health" and spikes:
                spikes.pop()
                self.calls.append(command)
                # The runtime writes the unhealthy result and exits 1.
                raise subprocess.CalledProcessError(1, [command], stderr=b"")
            return original(command, *args)
        self.publisher.maintenance = maintenance
        fetch, fetch_grants = self.publisher.fetch, self.publisher.fetch_grants
        self.publisher.fetch = lambda: (self.calls.append("fetch"), fetch())[1]
        self.publisher.fetch_grants = lambda: (self.calls.append("grants"), fetch_grants())[1]
        now, clock = self.fake_clock()
        with clock:
            result, logs = self.run_main()
        self.assertEqual(result, 0, logs)
        self.assertEqual(self.calls, ["timesyncd", "clock-health"] * 3 + [
            "fetch", "grants", "timesyncd", "clock-health", "sign-bundle", "install-bundle", "push"])
        self.assertEqual(now[0], 2 * p.CLOCK_RETRY_SECONDS)
        self.assertIn("clock-health unhealthy attempt=2 retry_in=120s", logs)
        self.assertNotIn("heartbeat=sent success=false", logs)

    def test_timesyncd_is_required_for_every_sample(self):
        self.timesyncd_states = [b"inactive\n"]
        now, clock = self.fake_clock()
        with clock, mock.patch.object(sys, "stderr"):
            self.publisher.await_clock()
        # The inactive check never ran the runtime, which could have used chrony.
        self.assertEqual(self.calls, ["timesyncd", "timesyncd", "clock-health"])
        self.timesyncd_states = [b"inactive\n"]
        self.calls.clear()
        with self.assertRaisesRegex(RuntimeError, "timesyncd"):
            self.publisher.publish(self.base)
        self.assertEqual(self.calls, ["timesyncd"])

    def test_other_clock_errors_fail_without_waiting(self):
        def maintenance(command, *args):
            raise subprocess.TimeoutExpired([command], 90)
        self.publisher.maintenance = maintenance
        with mock.patch.object(self.publisher, "pause") as sleep, self.assertRaises(subprocess.TimeoutExpired):
            self.publisher.await_clock()
        sleep.assert_not_called()

    def test_unit_and_resume_timeouts_cover_the_clock_wait(self):
        # The clock wait, a final sample at the deadline (systemctl plus runtime),
        # then about 20 bounded commands for fetch, grants, signing, guard and push.
        renewal = p.CLOCK_WAIT_SECONDS + 2 * 90 + 20 * 90
        templates = ROOT / "roles/chaldea_publisher/templates"
        def timeout(name):
            lines = [line for line in (templates / name).read_text().splitlines()
                     if line.startswith("TimeoutStartSec=")]
            self.assertEqual(len(lines), 1, name)
            return int(lines[0].removeprefix("TimeoutStartSec=").removesuffix("min")) * 60
        self.assertEqual(timeout("chaldea-publisher.service.j2"), 75 * 60)
        self.assertGreater(75 * 60, renewal)
        # The age check may queue behind a whole renewal, then fetches and checks age.
        self.assertGreater(timeout("chaldea-publisher-age.service.j2"), 75 * 60 + 6 * 90)
        self.assertIn("chaldea-publisher.service.j2",
                      (templates / "chaldea-publisher-emergency.service.j2").read_text())
        self.assertIn("RESUME_TIMEOUT_SECONDS = 76 * 60\n",
                      (ROOT / "scripts/chaldea-anchor-reissue").read_text())

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
                                "ansible_python_interpreter": sys.executable}}},
            "docker_hosts": {"hosts": {"homelab": {"ansible_host": "192.0.2.1"}}}}}}))
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

    def test_normandy_plays_keep_publisher_runtime_apart_from_shared_pin(self):
        pins = yaml.safe_load((ROOT / "vars/chaldea-runtime.yml").read_text())
        shared, publisher = pins["chaldea_host_version"], pins["chaldea_publisher_host_version"]
        self.assertEqual(shared, "host-v0.2.24")
        self.assertEqual(pins["chaldea_host_sha256"],
                         "8a94042352c31905694f25800dfb7240b80e914ba0e7653e74e60bf650766ca5")
        self.assertEqual(publisher, "host-v0.2.2")
        self.assertEqual(pins["chaldea_publisher_host_sha256"],
                         "ebaf896b2b4552002f00ad9b83855ff2364abe02f57ec667d7c9fcafb308c357")
        self.assertEqual(pins["chaldea_publisher_preserve_versions"], [shared])
        trust_include = yaml.safe_load(
            (ROOT / "roles/chaldea_access_trust/tasks/main.yml").read_text())[0]
        config = self.root / "config.json"

        def artifact(version, sha, preserved):
            return [{"ansible.builtin.assert": {"quiet": True, "that": [
                        "chaldea_host_version == '" + version + "'",
                        "chaldea_host_sha256 == '" + sha + "'",
                        "chaldea_host_artifact_preserve_versions == ['" + preserved + "']"]}},
                    {"ansible.builtin.set_fact": {"fixture_artifact_calls":
                        "{{ fixture_artifact_calls | default([]) + [chaldea_host_version] }}"}}]

        # The consumer play installs the shared release twice: its runtime role and
        # the trust role's public include, which provides the fetcher source.
        cases = {
            "chaldea-signer.yml": ("omarchy-normandy", [publisher], {
                "chaldea_node": [],
                "chaldea_host_artifact": artifact(publisher, pins["chaldea_publisher_host_sha256"], shared),
                "chaldea_publisher": [{"ansible.builtin.template": {
                    "src": str(ROOT / "roles/chaldea_publisher/templates/config.json.j2"),
                    "dest": str(config), "mode": "0600"}}]}),
            "akasha-normandy-consumer-trust.yml": ("normandy", [shared, shared], {
                "chaldea_node": [],
                "chaldea_host_artifact": artifact(shared, pins["chaldea_host_sha256"], publisher),
                "chaldea_access_trust": [trust_include]}),
        }
        for playbook, (host, calls, roles) in cases.items():
            work = self.root / playbook
            for role, tasks in roles.items():
                (work / "roles" / role / "tasks").mkdir(parents=True)
                if role == "chaldea_publisher":
                    shutil.copytree(ROOT / "roles/chaldea_publisher/defaults",
                                    work / "roles" / role / "defaults")
                (work / "roles" / role / "tasks/main.yml").write_text(
                    yaml.safe_dump(tasks or [{"ansible.builtin.meta": "noop"}]))
            play = yaml.safe_load((ROOT / "playbooks" / playbook).read_text())[0]
            # Keep the real roles list, role variables and vars files; drop host gates.
            play.update(hosts=host, become=False, pre_tasks=[], vars_files=[
                str((ROOT / "playbooks" / path).resolve()) for path in play["vars_files"]])
            play["post_tasks"] = [{"ansible.builtin.assert": {"quiet": True, "that": [
                "chaldea_host_version == '" + shared + "'",
                "fixture_artifact_calls == " + json.dumps(calls)]}}]
            (work / "play.yml").write_text(yaml.safe_dump([play]))
            (work / "inventory.yml").write_text(yaml.safe_dump({"all": {"hosts": {
                host: {"ansible_connection": "local", "ansible_python_interpreter": sys.executable},
                "homelab": {"ansible_host": "192.0.2.1"}}}}))
            proc = subprocess.run(
                ["ansible-playbook", "-i", str(work / "inventory.yml"), "--limit", host,
                 str(work / "play.yml")],
                capture_output=True, text=True, env={
                    **os.environ, "ANSIBLE_ROLES_PATH": str(work / "roles"),
                    "ANSIBLE_LOCAL_TEMP": str(work / "local"),
                    "ANSIBLE_REMOTE_TEMP": str(work / "remote")})
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("failed=0", proc.stdout)
        self.assertEqual(json.loads(config.read_text())["runtime"],
                         "/opt/chaldea-host/releases/" + publisher + "/host/access-maintenance.mjs")


if __name__ == "__main__":
    unittest.main()
