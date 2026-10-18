#!/usr/bin/env python3
"""Rollout contracts and a fresh synthetic --check; no host connections or keys."""
import copy
import http.server
import importlib.util
import json
import threading
from unittest import mock
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]

class RolloutTest(unittest.TestCase):
    def setUp(self):
        home = Path(os.environ["HOME"]).resolve()
        self.assertIn(".scratch", str(home))
        self.assertEqual(home, Path(os.environ["ROLLOUT_TEST_HOME"]).resolve())

    def test_registry_and_config_expose_only_the_first_sources(self):
        v = yaml.safe_load((ROOT / "vars/chaldea-runtime.yml").read_text())
        v.update(yaml.safe_load((ROOT / "vars/akasha-registry.yml").read_text()))
        v.update(akasha_state_root="/var/lib/akasha", akasha_nats_seed_file="/etc/akasha/nats.seed")
        # Model Ansible's recursive variable resolution for this plain Jinja renderer.
        v["chaldea_access_trust_anchors"] = [{
            "kid": v["chaldea_deployment_anchor_kid"],
            "public_key": v["chaldea_deployment_anchor_public_jwk"]}]
        env = Environment(undefined=StrictUndefined)
        env.filters["to_nice_json"] = json.dumps
        c = json.loads(env.from_string((ROOT / "roles/akasha/templates/config.json.j2").read_text()).render(v))
        self.assertEqual(c["access_state"], "/var/lib/akasha/access-state.db")
        self.assertEqual(c["bundle_file"], "/var/lib/chaldea-access-trust-public/bundle.jws")
        self.assertEqual(c["clock_health_file"], "/run/chaldea-access-trust/clock-health.json")
        self.assertEqual(v["chaldea_deployment_anchor_kid"], "deployment-2026-10b")
        self.assertEqual(c["anchors"], [{
            "kid": "deployment-2026-10b",
            "public_key": v["chaldea_deployment_anchor_public_jwk"]}])
        self.assertEqual(v["chaldea_deployment_anchor_public_jwk"]["crv"], "Ed25519")
        self.assertEqual([(r["source"],r["profile"],r["project"],r["kinds"]) for r in c["registry"]], [
            ("chaldea.daily-health.agent-a","daily-health","homelab",["report"]),
            ("agent-a.fleet","fleet-timers","chaldea",["job_finished"]),
            ("agent-a.toolfeedback","toolfeedback","chaldea",["tool_call"])])
        self.assertTrue(all(r["host"] == "agent-a" and not r.get("global_read") and not r.get("roots")
                            for r in c["registry"]))
        self.assertEqual(c["bind"], "127.0.0.1")
        self.assertEqual(c["port"], 1935)

    def test_enrollment_is_opt_in_and_preserves_initial_registry(self):
        base = yaml.safe_load((ROOT / "vars/akasha-registry.yml").read_text())["akasha_registry"]
        extra = yaml.safe_load((ROOT / "vars/akasha-enrollment-registry.yml").read_text())
        play = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]
        task = play["pre_tasks"][0]
        self.assertIs(extra["akasha_enrollment_registry_enabled"], False)
        self.assertIs(extra["akasha_pc_global_reader_enabled"], False)
        self.assertIs(extra["akasha_pc_quota_events_enabled"], False)
        self.assertEqual(task["when"], "akasha_enrollment_registry_enabled | bool")
        for reader in (False, True):
            for quota in (False, True):
                # Jinja native rendering executes the actual Ansible composition expression.
                from jinja2.nativetypes import NativeEnvironment
                native = NativeEnvironment(undefined=StrictUndefined)
                from ansible.plugins.filter.core import to_bool
                native.filters["bool"] = to_bool
                rendered = native.from_string(task["ansible.builtin.set_fact"]["akasha_registry"]).render(
                    extra | dict(akasha_registry=base, akasha_pc_global_reader_enabled=reader,
                                 akasha_pc_quota_events_enabled=quota))
                self.assertIsInstance(rendered, list)
                assert isinstance(rendered, list)
                self.assertEqual(rendered[:len(base)], base)
                self.assertEqual(len({r["source_id"] for r in rendered}), len(rendered))
                optional = [r for r in rendered if r.get("global_read") or r["source"] == "chaldea-quota"]
                self.assertEqual(len(optional), int(reader) + int(quota))
                self.assertTrue(all(r["host"] == "omarchy-pc" for r in optional))
        self.assertNotIn("public_key", json.dumps(extra))


    def test_quota_sharing_is_separate_service_scope(self):
        from itertools import product
        from jinja2.nativetypes import NativeEnvironment
        from ansible.plugins.filter.core import to_bool
        base = yaml.safe_load((ROOT / "vars/akasha-registry.yml").read_text())["akasha_registry"]
        extra = yaml.safe_load((ROOT / "vars/akasha-enrollment-registry.yml").read_text())
        self.assertIs(extra["akasha_quota_laptop_publisher_enabled"], False)
        self.assertIs(extra["akasha_quota_pc_reader_enabled"], False)
        expression = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]["pre_tasks"][0]["ansible.builtin.set_fact"]["akasha_registry"]
        native = NativeEnvironment(undefined=StrictUndefined)
        native.filters["bool"] = to_bool
        for laptop, reader, operator, seven in product((False, True), repeat=4):
            rendered = native.from_string(expression).render(extra | dict(
                akasha_registry=base, akasha_quota_laptop_publisher_enabled=laptop,
                akasha_quota_pc_reader_enabled=reader, akasha_pc_global_reader_enabled=operator,
                akasha_pc_quota_events_enabled=seven))
            assert isinstance(rendered, list)
            sharing = [r for r in rendered if r["source"] == "quota"]
            self.assertEqual({r["host"] for r in sharing},
                             ({"omarchy-macbook-air-igor"} if laptop else set()) |
                             ({"omarchy-pc"} if reader else set()))
            self.assertEqual(len({r["source_id"] for r in rendered}), len(rendered))
            self.assertEqual(rendered[:len(base)], base)
            for row in sharing:
                self.assertEqual((row["profile"], row["project"], row["mode"], row["kinds"]),
                                 ("quota-service", "chaldea", "service", ["quota_headroom_snapshot"]))
                self.assertNotIn("global_read", row)
                self.assertEqual(row.get("quota_fleet_read", False), row["host"] == "omarchy-pc")
            self.assertEqual(len([r for r in rendered if r.get("global_read")]), int(operator))
            self.assertEqual(len([r for r in rendered if r["source"] == "chaldea-quota"]), int(seven))

    def test_normandy_consumer_path_is_disabled_and_requires_exact_host(self):
        play = yaml.safe_load((ROOT / "playbooks/akasha-normandy-consumer-trust.yml").read_text())[0]
        self.assertEqual(play["hosts"], "normandy")
        self.assertIs(play["vars"]["akasha_normandy_consumer_trust_enabled"], False)
        self.assertEqual([r["role"] for r in play["roles"]],
                         ["chaldea_node", "chaldea_host_artifact", "chaldea_access_trust"])
        pre = play["pre_tasks"]
        self.assertEqual(pre[0]["ansible.builtin.meta"], "end_play")
        self.assertEqual(pre[0]["when"], "not akasha_normandy_consumer_trust_enabled | bool")
        self.assertIn("ansible_limit == 'normandy'", pre[1]["ansible.builtin.assert"]["that"])
        self.assertNotIn("initialize-state", json.dumps(play))
        self.assertNotIn("sign-bundle", json.dumps(play))

    def test_infra_remains_the_only_server_feed_config_owner(self):
        tasks = yaml.safe_load((ROOT / "roles/drift_check/tasks/main.yml").read_text())
        config = next(t["ansible.builtin.copy"] for t in tasks if t["name"] == "Point the feed client at Akasha through the scoped helper")
        self.assertEqual(config["mode"], "0600")
        for host in ("homelab", "normandy"):
            text = Environment(undefined=StrictUndefined).from_string(config["content"]).render(
                feed_url="https://akasha.ryzhkov.dev", inventory_hostname=host)
            self.assertNotIn("fleet-reader", text)
            self.assertIn("FEED_TOKEN_HOST=" + ("omarchy-normandy" if host == "normandy" else host), text)
            self.assertIn("FEED_TOKEN=''", text)
            self.assertIn("FEED_KEY_FILE=''", text)

    def test_ported_units_and_bootstrap_never_create_credentials_or_state(self):
        role = ROOT / "roles/chaldea_access_trust"
        tasks = (role / "tasks/main.yml").read_text()
        self.assertNotIn("initialize-state", tasks)
        self.assertNotIn("genpkey", tasks)
        self.assertNotIn("ssh-keygen", tasks)
        self.assertIn('mode: "0600"', tasks)
        clock = (role / "templates/chaldea-clock-health.service.j2").read_text()
        self.assertIn("systemd-timesyncd.service", clock)
        self.assertIn("-/run/chrony", clock)
        updater = (role / "templates/chaldea-access-trust.service.j2").read_text()
        for value in ["User=root", "NoNewPrivileges=true", "ProtectHome=true",
                      "ProtectSystem=strict", "PrivateDevices=true", "CapabilityBoundingSet="]:
            self.assertIn(value, updater)

    def test_activation_rejects_readable_seed_and_wrong_service_ownership(self):
        tasks = yaml.safe_load((ROOT / "roles/akasha/tasks/main.yml").read_text())
        task = next(t for t in tasks if t.get("name") == "Require private service-owned seed and verifier state")
        good = {"isreg": True, "islnk": False, "nlink": 1, "uid": 999, "gid": 998, "mode": "0600"}
        variants = [{}, {"mode":"0644"}, {"mode":"0640"}, {"uid":0}, {"gid":1000},
                    {"islnk":True}, {"nlink":2}]
        with tempfile.TemporaryDirectory(dir=os.environ["HOME"]) as d:
            play = Path(d) / "guard.yml"
            play.write_text(yaml.safe_dump([{"hosts":"localhost","gather_facts":False,"tasks":[task]}]))
            for index in (3,4):
                for variant in variants:
                    results = [{}, {}, {}, {"item":"verifier-state","stat":good}, {"item":"nats-seed","stat":good}]
                    results = copy.deepcopy(results)
                    results[index]["stat"].update(variant)
                    v = {"akasha_activate":True, "akasha_inputs":{"results":results}, "ansible_facts":{
                        "getent_passwd":{"akasha":["x","999","998"]}, "getent_group":{"akasha":["x","998"]}}}
                    proc = subprocess.run(["ansible-playbook","-i","localhost,","-c","local","--check",
                        str(play),"-e",json.dumps(v)],capture_output=True,text=True,
                        env={**os.environ,"ANSIBLE_LOCAL_TEMP":str(Path(d)/"ansible")})
                    self.assertEqual(proc.returncode, 2 if variant else 0, proc.stdout+proc.stderr)

    def test_canary_never_uses_environment_proxy_or_follows_a_redirect(self):
        spec = importlib.util.spec_from_file_location("akasha_canary", ROOT / "scripts/akasha-canary.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        hits = {"target":0, "other":0}
        class Other(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits["other"] += 1
                self.send_response(500)
                self.end_headers()
            do_POST = do_GET
            def log_message(self, *args):
                pass
        other = http.server.ThreadingHTTPServer(("127.0.0.1",0), Other)
        class Target(Other):
            def do_POST(self):
                hits["target"] += 1
                self.send_response(302)
                self.send_header("Location","http://127.0.0.1:%d/leak" % other.server_port)
                self.end_headers()
        target = http.server.ThreadingHTTPServer(("127.0.0.1",0), Target)
        for server in (other,target):
            threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with mock.patch.dict(os.environ, {"http_proxy":"http://127.0.0.1:%d" % other.server_port,
                                             "no_proxy":""}):
                with self.assertRaises(RuntimeError):
                    helper.run("synthetic.test.bearer","canary",target.server_port)
            self.assertEqual(hits,{"target":1,"other":0})
        finally:
            for server in (target,other):
                server.shutdown()
                server.server_close()

    def test_fresh_full_check_is_local_and_leaves_managed_paths_absent(self):
        # Chaldea's minimal namespace may intentionally omit passwd/group NSS.
        # CI's disposable root container exercises this complete check fixture.
        import pwd
        try:
            pwd.getpwnam("root")
        except KeyError:
            self.skipTest("root NSS absent in job namespace; full check runs in CI")
        # The actual check-mode play runs with a scratch inventory that has no
        # addresses or SSH users. Empty service facts model a cold host in a
        # namespace without init. The mocked local filesystem
        # type: a Chaldea job uses overlayfs while production requires ext4.
        home = Path(os.environ["HOME"]).resolve()
        self.assertIn(".scratch", str(home))
        self.assertEqual(home, Path(os.environ["ROLLOUT_TEST_HOME"]).resolve())
        with tempfile.TemporaryDirectory(dir=home) as d:
            d = Path(d)
            bindir = d / "bin"
            bindir.mkdir()
            probe = bindir / "findmnt"
            probe.write_text("#!/bin/sh\nprintf 'ext4\\n'\n")
            probe.chmod(0o700)
            inventory = d / "hosts.yml"
            inventory.write_text(yaml.safe_dump({"all": {"hosts": {"homelab": {
                "ansible_connection": "local", "ansible_become": False, "ansible_facts": {"services": {}}, "ansible_python_interpreter": os.sys.executable}}}}))
            before = {p: Path(p).exists() for p in [
                "/opt/chaldea/node", "/opt/chaldea-host", "/etc/akasha",
                "/etc/chaldea-access-trust", "/var/lib/akasha"]}
            # Inert metadata fixtures only; no private key or live SQLite state.
            config, state, public, units = [d / n for n in ("config","state","public","units")]
            for directory in (config,state,public,units):
                directory.mkdir(mode=0o700)
            for path, mode in [(config/"deploy-key",0o600),(config/"known_hosts",0o644),(state/"updater.sqlite",0o600)]:
                path.write_text("synthetic metadata fixture\\n")
                path.chmod(mode)
            enrolled = {
                "ansible_facts": {"services": {}},
                "chaldea_access_trust_enabled": True,
                # Public-only synthetic JWK. Production placeholders must remain invalid.
                "chaldea_deployment_anchor_public_jwk": {
                    "kty": "OKP", "crv": "Ed25519", "x": "A" * 43},
                "chaldea_access_trust_root": str(config),
                "chaldea_access_trust_state": str(state),
                "chaldea_access_trust_public": str(public),
                "chaldea_access_trust_unit_dir": str(units),
                "chaldea_access_paths": {
                    "root": str(config), "state": str(state), "public": str(public),
                    "run": "/run/chaldea-access-trust",
                    "bundle": str(public/"bundle.jws"), "updater_state": str(state/"updater.sqlite"),
                    "clock_health": "/run/chaldea-access-trust/clock-health.json",
                    "verifier_state": "/var/lib/akasha/access-state.db"},
            }
            for variables, tags in (({"ansible_facts": {"services": {}}}, []),
                                    (enrolled, []), (enrolled, ["--tags", "runtime,trust"])):
                result = subprocess.run(["ansible-playbook", "-i", str(inventory),
                str(ROOT / "playbooks/akasha.yml"), "--check", "--diff",
                    "-e", json.dumps({**variables, "chaldea_host_version": "host-v0.0.0-test",
                                      "chaldea_host_sha256": "a" * 64}), *tags],
                    env={**os.environ, "PATH": str(bindir) + ":" + os.environ["PATH"],
                     "ANSIBLE_LOCAL_TEMP": str(d / "local"), "ANSIBLE_REMOTE_TEMP": str(d / "remote")},
                    capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("failed=0", result.stdout)
            self.assertEqual(before, {p: Path(p).exists() for p in before})

if __name__ == "__main__":
    unittest.main()
