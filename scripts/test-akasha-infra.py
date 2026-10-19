#!/usr/bin/env python3
"""Lane I: disposable broker ACL/convergence and SQLite restore checks.

Requires nats-server 2.15.1, nats CLI, PyYAML, Jinja2 and nats-py[nkeys].
All generated test identities and state live below TMPDIR, never on a host.
"""
import asyncio
import base64
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

import jinja2
import nats
import nkeys
import yaml

ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    loader = importlib.machinery.SourceFileLoader(
        "akasha_maintenance", str(ROOT / "roles/akasha/files/akasha-maintenance"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SnapshotTest(unittest.TestCase):
    def test_wal_snapshot_restores_receipts_and_cursors_and_private_mode(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "events.db") as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE receipts (id TEXT PRIMARY KEY, state TEXT)")
                db.execute("CREATE TABLE cursors (sequence INTEGER)")
                db.execute("INSERT INTO receipts VALUES ('stable-id', 'visible')")
                db.execute("INSERT INTO cursors VALUES (42)")
                db.commit()
                helper.snapshot(root)
                with sqlite3.connect(root / "backup/events.db") as restored:
                    self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
                    self.assertEqual(restored.execute("SELECT * FROM receipts").fetchall(),
                                     [("stable-id", "visible")])
                    self.assertEqual(restored.execute("SELECT * FROM cursors").fetchall(), [(42,)])
                self.assertEqual((root / "backup/events.db").stat().st_mode & 0o777, 0o600)
                self.assertFalse((root / "backup/events.db.partial").exists())
                # A later consistent generation replaces the first.
                db.execute("INSERT INTO cursors VALUES (43)")
                db.commit()
                helper.snapshot(root)
                with sqlite3.connect(root / "backup/events.db") as restored:
                    self.assertEqual(restored.execute("SELECT count(*) FROM cursors").fetchone(), (2,))

    def test_capacity_and_missing_snapshot_fail_health(self):
        helper = load_helper()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with sqlite3.connect(root / "events.db") as db:
                db.execute("CREATE TABLE t (x)")
            with self.assertRaisesRegex(RuntimeError, "snapshot absent"):
                helper.health(root, "http://127.0.0.1:1/healthz")
            # Sparse file tests the physical file bound without allocating 4 GiB.
            with (root / "events.db-wal").open("wb") as wal:
                wal.truncate(helper.WAL_LIMIT + 1)
            with self.assertRaisesRegex(RuntimeError, "WAL exceeds"):
                helper.check_storage(root)

    def test_actual_deployment_guards_refuse_host_users_and_live_restaging(self):
        cases = [
            ("stacks/nats/swarm-prepare.yml", "Refuse host identities in the Akasha v1 account",
             {"nats_chaldea_hosts": []}, 0),
            ("stacks/nats/swarm-prepare.yml", "Refuse host identities in the Akasha v1 account",
             {"nats_chaldea_hosts": ["agent-a"]}, 2),
            ("roles/akasha/tasks/main.yml", "Refuse staging over a running or enabled service",
             {"akasha_activate": False, "ansible_facts": {"services": {}}}, 0),
            ("roles/akasha/tasks/main.yml", "Refuse staging over a running or enabled service",
             {"akasha_activate": False, "ansible_facts": {"services": {
                 "akasha.service": {"state": "running", "status": "disabled"}}}}, 2),
            ("roles/akasha/tasks/main.yml", "Refuse staging over a running or enabled service",
             {"akasha_activate": False, "ansible_facts": {"services": {
                 "akasha.service": {"state": "stopped", "status": "enabled"}}}}, 2),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for source, name, variables, expected in cases:
                task = next(t for t in yaml.safe_load((ROOT / source).read_text())
                            if t.get("name") == name)
                play = Path(directory) / "guard.yml"
                play.write_text(yaml.safe_dump([{"hosts": "localhost", "gather_facts": False,
                                                "tasks": [task]}]))
                proc = subprocess.run(
                    ["ansible-playbook", "-i", "localhost,", "-c", "local", "--check",
                     str(play), "-e", json.dumps(variables)],
                    env={**os.environ, "ANSIBLE_LOCAL_TEMP": directory + "/ansible"},
                    capture_output=True, text=True)
                self.assertEqual(proc.returncode, expected, name + proc.stdout + proc.stderr)


    def test_mission_control_registry_requires_qualified_host_before_writes(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        self.assertTrue(Path.home().resolve().is_relative_to(scratch))
        source = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]
        defaults = {}
        for name in source["vars_files"]:
            defaults.update(yaml.safe_load((ROOT / "playbooks" / name).read_text()))
        cases = [(v, "enabled", 0 if v == "host-v0.2.4" else 2)
                 for v in ["host-v0.2.3", "host-v0.2.4", "host-v0.2.1", "host-v0.2.2", "host-v0.2.5"]]
        cases += [("host-v0.2.3", "disabled", 0), ("host-v0.2.3", "legacy", 0),
                  ("host-v0.2.3", "base-only", 2)]
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            for index, (version, mode, expected) in enumerate(cases):
                for tagged in [False, True]:
                    with self.subTest(version=version, mode=mode, runtime_tags=tagged):
                        marker = root / f"effect-{index}-{tagged}"
                        values = {**defaults, "akasha_host_version": version,
                                  "akasha_enrollment_registry_enabled": mode not in ["disabled", "base-only"]}
                        if mode == "legacy":
                            values["akasha_enrollment_registry_additions"] = [
                                row for row in defaults["akasha_enrollment_registry_additions"]
                                if "mission_control_read_sources" not in row]
                        if mode == "base-only":
                            reader = next(row for row in defaults["akasha_enrollment_registry_additions"]
                                          if "mission_control_read_sources" in row)
                            values["akasha_registry"] = defaults["akasha_registry"] + [reader]
                        play = root / "guard.yml"
                        # Execute the real ordered pre-tasks; replace all host-writing roles
                        # with one scratch sentinel selected by the same runtime tag.
                        play.write_text(yaml.safe_dump([{
                            "hosts": "localhost", "gather_facts": False, "vars": values,
                            "pre_tasks": source["pre_tasks"], "tasks": [{
                                "name": "Fixture artifact or configuration effect",
                                "ansible.builtin.copy": {"content": "effect", "dest": str(marker), "mode": "0600"},
                                "tags": ["runtime"]}]}]))
                        proc = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local",
                                               str(play), *(["--tags", "runtime"] if tagged else [])],
                            env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(root / "ansible")},
                            capture_output=True, text=True)
                        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
                        self.assertEqual(marker.exists(), expected == 0)
                        if expected:
                            self.assertNotIn("TASK [Compose explicitly enabled", proc.stdout)

    def test_config_only_reload_requires_activation(self):
        tasks = yaml.safe_load((ROOT / "roles/akasha/tasks/main.yml").read_text())
        config = next(t for t in tasks if t.get("name") == "Render Akasha public configuration with exact source registry and anchor")
        self.assertEqual(config["register"], "akasha_config")
        start = next(t for t in tasks if t.get("name") == "Start only the explicitly approved runtime")
        env = jinja2.Environment(undefined=jinja2.StrictUndefined)
        env.filters["bool"] = bool
        env.tests["changed"] = lambda result: result["changed"]
        for activate in [False, True]:
            for units, config_changed in [(False, False), (False, True), (True, False)]:
                values = dict(akasha_activate=activate, akasha_units=dict(changed=units), akasha_config=dict(changed=config_changed))
                enabled = env.compile_expression(start["when"])(**values)
                state = env.from_string(start["ansible.builtin.systemd_service"]["state"]).render(**values)
                self.assertEqual(enabled, activate)
                self.assertEqual(state, "restarted" if units or config_changed else "started")

    def test_staged_role_has_no_activation_without_external_contracts(self):
        defaults = yaml.safe_load((ROOT / "roles/akasha/defaults/main.yml").read_text())
        self.assertIs(defaults["akasha_activate"], False)
        self.assertIs(defaults["akasha_interfaces_verified"], False)
        self.assertIs(defaults["akasha_backup_packaging_verified"], False)
        play = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())
        self.assertEqual(play[0]["hosts"], "homelab")
        self.assertEqual(play[0]["roles"][-1]["role"], "akasha")



    def test_installed_system_verification_precedes_activation_and_fails_closed(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        self.assertTrue(Path.home().resolve().is_relative_to(scratch))
        source = yaml.safe_load((ROOT / "roles/akasha/tasks/main.yml").read_text())
        first = next(i for i, task in enumerate(source)
                     if task.get("name") == "Install service and maintenance units")
        defaults = yaml.safe_load((ROOT / "roles/akasha/defaults/main.yml").read_text())
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            root = Path(directory)
            for mode in ("ok", "exit", "stdout", "stderr", "whitespace", "check"):
                with self.subTest(mode=mode):
                    case = root / mode
                    case.mkdir()
                    events = case / "events"
                    validator = case / "validator"
                    validator.write_text(
                        "#!" + sys.executable + "\n"
                        "import pathlib,sys\n"
                        f"root=pathlib.Path({str(case)!r})\n"
                        "assert sys.argv[1:3] == ['verify', '--man=no']\n"
                        "assert len(sys.argv[3:]) == 5\n"
                        "for arg in sys.argv[3:]:\n"
                        " assert arg.startswith('/etc/systemd/system/')\n"
                        " assert (root/pathlib.Path(arg).name).is_file()\n"
                        "assert 'host-v0.2.7/host/akasha-service.mjs' in (root/'akasha.service').read_text()\n"
                        "with (root/'events').open('a') as out: out.write('verify\\n')\n"
                        f"mode={mode!r}\n"
                        "if mode == 'stdout': print('diagnostic')\n"
                        "if mode == 'stderr': print('diagnostic',file=sys.stderr)\n"
                        "if mode == 'whitespace': print(' ',file=sys.stderr)\n"
                        "sys.exit(1 if mode == 'exit' else 0)\n")
                    validator.chmod(0o700)
                    tasks = json.loads(json.dumps(source[first:]))
                    # Only host effects cross a scratch boundary. The real ordered tasks,
                    # template rendering, register values, conditions and failure rule run.
                    for task in tasks:
                        if "ansible.builtin.template" in task:
                            task["ansible.builtin.template"].update({
                                "src": str(ROOT / "roles/akasha/templates") + "/{{ item }}.j2",
                                "dest": str(case) + "/{{ item }}"})
                        if task.get("name") == "Verify actual installed SYSTEM units before activation":
                            task["ansible.builtin.command"]["argv"][0] = str(validator)
                        if "ansible.builtin.systemd_service" in task:
                            args = task.pop("ansible.builtin.systemd_service")
                            event = "reload" if args.get("daemon_reload") else args["name"]
                            task["ansible.builtin.command"] = {"argv": [
                                sys.executable, "-c",
                                "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                                "p.open('a').write(sys.argv[2]+'\\n')",
                                str(events), event]}
                    play = case / "play.yml"
                    play.write_text(yaml.safe_dump([{
                        "hosts": "localhost", "gather_facts": False,
                        "vars": {**defaults, "akasha_activate": True,
                                 "akasha_config": {"changed": False}, "ansible_host": "127.0.0.1",
                                 "akasha_exec_start": "/opt/chaldea/node/bin/node /opt/chaldea-host/releases/host-v0.2.7/host/akasha-service.mjs serve --config /etc/akasha/config.json"},
                        "tasks": tasks}]))
                    proc = subprocess.run([
                        "ansible-playbook", "-i", "localhost,", "-c", "local", str(play),
                        *(["--check"] if mode == "check" else [])],
                        env={**os.environ, "ANSIBLE_LOCAL_TEMP": str(case / "local"),
                             "ANSIBLE_REMOTE_TEMP": str(case / "remote")},
                        capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0 if mode in ("ok", "check") else 2,
                                     proc.stdout + proc.stderr)
                    actual = events.read_text().splitlines() if events.exists() else []
                    if mode == "ok":
                        self.assertEqual(actual, ["verify", "reload", "akasha.service",
                                                 "akasha-health.timer", "akasha-backup.timer"])
                    elif mode == "check":
                        self.assertEqual(actual, [])
                        self.assertIn("qualification deferred", proc.stdout)
                    else:
                        self.assertEqual(actual, ["verify"])
                        self.assertNotIn("TASK [Start only", proc.stdout)

    def test_homelab_runtime_pin_is_role_local_and_keeps_trust_on_shared_release(self):
        shared = yaml.safe_load((ROOT / "vars/chaldea-runtime.yml").read_text())
        pin = yaml.safe_load((ROOT / "vars/akasha-runtime.yml").read_text())
        self.assertEqual(shared["chaldea_host_version"], "host-v0.2.2")
        self.assertEqual(pin["akasha_host_version"], "host-v0.2.7")
        self.assertEqual(pin["akasha_host_sha256"],
                         "fabf83d5814a8c997f57691dea4b711b245ffd37b29783b1e0fd1b0ec122e218")
        old_root = "/opt/chaldea-host/releases/host-v0.2.2/host"
        new_root = "/opt/chaldea-host/releases/host-v0.2.7/host"
        checks = {
            "chaldea_node": ["chaldea_host_version == 'host-v0.2.2'"],
            "chaldea_host_artifact": [
                "chaldea_host_version == ('host-v0.2.2' if fixture_artifact_calls | default([]) | length else 'host-v0.2.7')",
                "chaldea_host_sha256 == (fixture_shared_sha if fixture_artifact_calls | default([]) | length else akasha_host_sha256)",
                "chaldea_host_artifact_preserve_versions == (['host-v0.2.3', 'host-v0.2.4', 'host-v0.2.6', 'host-v0.2.7'] if fixture_artifact_calls | default([]) | length else ['host-v0.2.2', 'host-v0.2.3', 'host-v0.2.4', 'host-v0.2.6'])",
                "chaldea_host_artifact_root == '/opt/chaldea-host/releases/' ~ chaldea_host_version ~ '/host'"],
            "chaldea_access_trust": [
                "chaldea_host_version == 'host-v0.2.2'",
                "chaldea_host_sha256 == '" + shared["chaldea_host_sha256"] + "'",
                "chaldea_host_artifact_root == '" + old_root + "'",
                "chaldea_access_trust_runtime == '" + old_root + "/access-maintenance.mjs'"],
            "akasha": [
                "chaldea_host_version == 'host-v0.2.7'",
                "chaldea_host_artifact_root == '" + new_root + "'",
                "'" + new_root + "/akasha-service.mjs' in akasha_exec_start"],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            releases, cache = root / "releases", root / "cache"
            releases.mkdir()
            cache.mkdir()
            for index, version in enumerate(("host-v0.2.1", "host-v0.2.7", "host-v0.2.2", "host-v0.2.3", "host-v0.2.4", "host-v0.2.6")):
                release = releases / version
                release.mkdir()
                os.utime(release, (index + 1, index + 1))
                for suffix in (".tar.gz", ".json"):
                    (cache / (version + suffix)).write_text(version)
            artifact_tasks = yaml.safe_load((ROOT / "roles/chaldea_host_artifact/tasks/main.yml").read_text())
            gc_start = next(i for i, task in enumerate(artifact_tasks)
                            if task.get("name") == "Find installed host releases without following links")
            trust_include = yaml.safe_load((ROOT / "roles/chaldea_access_trust/tasks/main.yml").read_text())[0]
            for role, assertions in checks.items():
                target = root / "roles" / role
                (target / "tasks").mkdir(parents=True)
                defaults = ROOT / "roles" / role / "defaults"
                if defaults.exists():
                    shutil.copytree(defaults, target / "defaults")
                tasks = [{"ansible.builtin.assert": {"that": assertions, "quiet": True}}]
                if role == "chaldea_host_artifact":
                    tasks.extend([
                        {"ansible.builtin.set_fact": {"fixture_artifact_calls":
                            "{{ fixture_artifact_calls | default([]) + [chaldea_host_version] }}"}},
                        {"block": artifact_tasks[gc_start:], "vars": {
                            "chaldea_host_artifact_release_root": str(releases),
                            "chaldea_host_artifact_cache": str(cache),
                            "chaldea_host_artifact_become": False}}])
                if role == "chaldea_access_trust":
                    tasks.insert(0, trust_include)
                if role == "akasha":
                    tasks.append({"ansible.builtin.template": {
                        "src": str(ROOT / "roles/akasha/templates/akasha.service.j2"),
                        "dest": str(root / "akasha.service"), "mode": "0600"}})
                (target / "tasks/main.yml").write_text(yaml.safe_dump(tasks))
            play = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]
            play["become"] = False
            play["vars"] = {"chaldea_access_trust_enabled": True,
                            "fixture_shared_sha": shared["chaldea_host_sha256"],
                            "mission_control_laptop_qualified": True,
                            "mission_control_laptop_host_version": pin["akasha_host_version"],
                            "mission_control_laptop_host_sha256": pin["akasha_host_sha256"]}
            play["vars_files"] = [str((ROOT / "playbooks" / path).resolve())
                                  for path in play["vars_files"]]
            play["post_tasks"] = [{"ansible.builtin.assert": {"that": [
                "chaldea_host_version == 'host-v0.2.2'",
                "fixture_artifact_calls == ['host-v0.2.7', 'host-v0.2.2']",
                "chaldea_host_artifact_root == '" + old_root + "'"], "quiet": True}}]
            path = root / "play.yml"
            path.write_text(yaml.safe_dump([play]))
            proc = subprocess.run(
                ["ansible-playbook", "-i", "homelab,", "-c", "local", str(path)],
                env={**os.environ, "ANSIBLE_ROLES_PATH": str(root / "roles"),
                     "ANSIBLE_LOCAL_TEMP": str(root / "local"),
                     "ANSIBLE_REMOTE_TEMP": str(root / "remote")},
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn(new_root + "/akasha-service.mjs",
                          (root / "akasha.service").read_text())
            for version in ("host-v0.2.2", "host-v0.2.3", "host-v0.2.4", "host-v0.2.6", "host-v0.2.7"):
                self.assertTrue((releases / version).is_dir())
                for suffix in (".tar.gz", ".json"):
                    self.assertEqual((cache / (version + suffix)).read_text(), version)
            self.assertFalse((releases / "host-v0.2.1").exists())
            self.assertFalse((cache / "host-v0.2.1.tar.gz").exists())
            self.assertFalse((cache / "host-v0.2.1.json").exists())


class BrokerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.keys = {}
        self.seeds = {}
        for name in ["sys-admin", "chaldea-admin", "akasha", "agent-a"]:
            seed = nkeys.encode_seed(os.urandom(32), nkeys.PREFIX_BYTE_USER)
            self.keys[name] = nkeys.from_seed(seed).public_key.decode()
            path = self.directory / (name + ".nk")
            path.write_bytes(seed)
            path.chmod(0o600)
            self.seeds[name] = path
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = "nats://127.0.0.1:%d" % port
        source = (ROOT / "stacks/nats/templates/nats.conf.j2").read_text()
        conf = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(source).render(
            nats_nkeys=self.keys, nats_chaldea_hosts=[])
        conf = conf.replace('listen: "0.0.0.0:4222"', 'listen: "127.0.0.1:%d"' % port)
        conf = conf.replace('http: "0.0.0.0:8222"', 'http: "127.0.0.1:0"')
        conf = conf.replace("store_dir: /data/jetstream", "store_dir: " + str(self.directory / "js"))
        conf = conf.replace('listen: "0.0.0.0:9222"', 'listen: "127.0.0.1:0"')
        path = self.directory / "nats.conf"
        path.write_text(conf)
        self.log = (self.directory / "server.log").open("w")
        self.addCleanup(self.log.close)
        self.server = subprocess.Popen(["nats-server", "-c", str(path)], stdout=self.log, stderr=self.log)
        self.addCleanup(self.stop_server)
        for _ in range(100):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                if self.server.poll() is not None:
                    self.fail((self.directory / "server.log").read_text())
                await asyncio.sleep(0.02)
        else:
            self.fail("scratch broker did not become ready")
        self.clients = []
        self.addAsyncCleanup(self.close_clients)
        self.admin = await self.connect("chaldea-admin")
        # A wrapper fixes the disposable endpoint and identity even if the
        # invoking operator has a real default NATS context configured.
        cli = shutil.which("nats")
        wrapper = self.directory / "nats-test"
        wrapper.write_text("#!/usr/bin/env python3\nimport os,sys\nos.execv(%r, [%r, '--no-context', '--server', %r, '--nkey', %r] + sys.argv[1:])\n"
                           % (cli, cli, self.url, str(self.seeds["chaldea-admin"])))
        wrapper.chmod(0o700)
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith("NATS_")},
                    "NATS_BIN": str(wrapper), "NATS_CONTEXT": "",
                    "XDG_CONFIG_HOME": str(self.directory / "config")}
        # Also isolate CLI home; no operator contexts/credentials are read.
        self.env["NATS_CONTEXTS_DIR"] = str(self.directory / "contexts")

    def stop_server(self):
        if self.server.poll() is None:
            self.server.terminate()
            self.server.wait(timeout=5)

    async def close_clients(self):
        for client in self.clients:
            await client.close()

    async def connect(self, name, errors=None):
        async def on_error(error):
            if errors is not None:
                errors.append(str(error))
        client = await nats.connect(self.url, nkeys_seed=str(self.seeds[name]),
                                    inbox_prefix=("_INBOX_" + name).encode(),
                                    error_cb=on_error, allow_reconnect=False, connect_timeout=2)
        self.clients.append(client)
        return client

    async def apply(self, *args, expect=0):
        proc = await asyncio.create_subprocess_exec(
            "bash", str(ROOT / "scripts/nats-jetstream-apply.sh"), *args,
            env=self.env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(), 20)
        self.assertEqual(proc.returncode, expect, (out + err).decode())
        return out.decode()

    async def test_required_keys_render_without_optional_host_keys(self):
        source = (ROOT / "stacks/nats/templates/nats.conf.j2").read_text()
        keys = {k: v for k, v in self.keys.items() if k != "agent-a"}
        conf = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(source).render(
            nats_nkeys=keys, nats_chaldea_hosts=[])
        self.assertNotIn(self.keys["agent-a"], conf)
        path = self.directory / "required-only.conf"
        path.write_text(conf)
        proc = subprocess.run(["nats-server", "-t", "-c", str(path)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    async def test_host_cannot_connect_or_redirect_api_replies_into_events(self):
        await self.apply()
        # Pipeline authenticated CONNECT plus a forged reply-subject request.
        # No host identity is admitted at all in v1, including one with a
        # perfectly valid NKey. The broker must reject before processing PUB.
        for subject, reply in [
            ("akasha.agent-a.report", "_INBOX_agent-a.x"),
            ("akasha.agent-b.report", "_INBOX_agent-a.x"),
            ("$JS.API.INFO", "akasha.forged-api.report"),
            ("$JS.API.DIRECT.GET.KV_CHALDEA_QUOTA", "akasha.agent-a-daily-health.report"),
            ("$JS.API.CONSUMER.MSG.NEXT.CHALDEA_WORK.fleet", "akasha.forged-work.report"),
        ]:
            port = int(self.url.rsplit(":", 1)[1])
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            info = json.loads((await reader.readline()).decode().removeprefix("INFO "))
            key = nkeys.from_seed(self.seeds["agent-a"].read_bytes())
            signature = base64.urlsafe_b64encode(key.sign(info["nonce"].encode())).decode().rstrip("=")
            auth = json.dumps({"nkey": self.keys["agent-a"], "sig": signature, "verbose": True})
            wire = "CONNECT " + auth + "\r\nPUB " + subject + " " + reply + " 2\r\n{}\r\nPING\r\n"
            writer.write(wire.encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), 2)
            self.assertIn(b"Authorization Violation", response)
            writer.close()
            await writer.wait_closed()
        self.assertEqual((await self.admin.jetstream().stream_info("AKASHA_EVENTS")).state.messages, 0)

    async def test_selector_check_idempotence_and_drift(self):
        js = self.admin.jetstream()
        await self.apply("--check", "--stream", "AKASHA_EVENTS")
        self.assertEqual(await js.streams_info(), [])
        await self.apply()  # Safe default is exactly the required subset.
        self.assertEqual([s.config.name for s in await js.streams_info()], ["AKASHA_EVENTS"])
        info = await js.consumer_info("AKASHA_EVENTS", "akasha-ingest")
        self.assertEqual(info.config.max_ack_pending, 1000)
        self.assertEqual(info.config.ack_wait, 60)
        second = await self.apply()
        self.assertIn("stream AKASHA_EVENTS: matches", second)
        self.assertIn("consumer AKASHA_EVENTS/akasha-ingest: matches", second)
        config = (await js.stream_info("AKASHA_EVENTS")).config
        config.max_msg_size = 65536
        await js.update_stream(config=config)
        await self.apply("--check")
        self.assertEqual((await js.stream_info("AKASHA_EVENTS")).config.max_msg_size, 65536)
        await self.apply("--stream", "AKASHA_EVENTS")
        self.assertEqual((await js.stream_info("AKASHA_EVENTS")).config.max_msg_size, 327680)
        await self.apply("--stream", "AKASHA_EVENT", expect=2)
        self.assertEqual([s.config.name for s in await js.streams_info()], ["AKASHA_EVENTS"])

    async def test_gateway_ack_replay_size_and_host_forgery_denials(self):
        await self.apply()
        gateway_errors = []
        gateway = await self.connect("akasha", gateway_errors)
        js = gateway.jetstream()
        ack = await js.publish("akasha.agent-a-daily-health.report", b"x" * 327000,
                               headers={"Nats-Msg-Id": "stable-event", "Nats-Expected-Stream": "AKASHA_EVENTS"})
        self.assertEqual(ack.stream, "AKASHA_EVENTS")
        duplicate = await js.publish("akasha.agent-a-daily-health.report", b"x",
                                     headers={"Nats-Msg-Id": "stable-event"})
        self.assertTrue(duplicate.duplicate)
        with self.assertRaises(nats.js.errors.APIError):
            await js.publish("akasha.agent-a-daily-health.report", b"x" * 327681)
        consumer = await js.pull_subscribe("akasha.>", durable="akasha-ingest", stream="AKASHA_EVENTS")
        messages = await consumer.fetch(1, timeout=2)
        self.assertEqual(len(messages[0].data), 327000)
        await messages[0].ack_sync()
        replay = await js.get_msg("AKASHA_EVENTS", seq=ack.seq, direct=True)
        self.assertEqual(len(replay.data), 327000)
        # Concrete protocol denials, including stream administration and
        # another principal's inbox, must be emitted by the real broker.
        for subject in ["$JS.API.STREAM.CREATE.FORGED", "$JS.API.STREAM.DELETE.AKASHA_EVENTS",
                        "$JS.API.CONSUMER.CREATE.AKASHA_EVENTS.evil"]:
            with self.assertRaises(nats.errors.TimeoutError):
                await gateway.request(subject, b"{}", timeout=0.15)
        await gateway.subscribe("_INBOX_agent-a.forged")
        await gateway.flush()
        await asyncio.sleep(0.1)
        self.assertGreaterEqual(len(gateway_errors), 4, gateway_errors)
        self.assertEqual((await self.admin.jetstream().stream_info("AKASHA_EVENTS")).state.messages, 1)


if __name__ == "__main__":
    if not os.environ.get("TMPDIR"):
        raise SystemExit("Set TMPDIR to task scratch; never use host /tmp")
    if not shutil.which("nats-server") or not shutil.which("nats"):
        raise SystemExit("Pinned nats-server and nats CLI are required (see CI)")
    os.umask(0o077)
    unittest.main()
