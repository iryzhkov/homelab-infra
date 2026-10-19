#!/usr/bin/env python3
"""Isolated web/config/pin/Traefik fixtures. No production identities or endpoints."""
import http.client
import http.server
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]


def isolated():
    home, scratch = Path.home(), Path(os.environ["TMPDIR"])
    assert os.environ["HOME"] == os.environ["MC_TEST_HOME"]
    assert home.is_relative_to(scratch)
    return home


def render():
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "roles/traefik/templates"),
                             undefined=jinja2.StrictUndefined)
    env.filters["bool"] = bool
    defaults = yaml.safe_load((ROOT / "roles/traefik/defaults/main.yml").read_text())
    return yaml.safe_load(env.get_template("mission-control.yml.j2").render(
        **defaults, traefik_cert_resolver="letencrypt"))


class MissionControlTest(unittest.TestCase):
    def test_actual_peer_gate_cfray_catchall_and_streaming(self):
        isolated()
        dynamic = render()
        lan = dynamic["http"]["middlewares"]["mission-control-lan"]["ipAllowList"]
        self.assertNotIn("ipStrategy", lan)
        self.assertEqual(lan["sourceRange"], ["192.168.20.0/24", "192.168.70.0/24",
                                             "192.168.90.0/24", "192.168.130.0/24", "10.42.0.0/24"])
        defaults = yaml.safe_load((ROOT / "roles/traefik/defaults/main.yml").read_text())
        self.assertFalse(any(ipaddress.ip_address("10.42.0.6") in ipaddress.ip_network(net)
                             for net in defaults["akasha_lan_cidrs"]))
        for forbidden in ("8.8.8.8", "192.168.50.1", "10.43.0.5", "127.0.0.1"):
            self.assertFalse(any(ipaddress.ip_address(forbidden) in ipaddress.ip_network(net) for net in lan["sourceRange"]))
        evidence = os.environ.get("MC_UNIT_EVIDENCE")
        if evidence:
            out = Path(evidence).resolve()
            assert out.is_relative_to(Path(os.environ["TMPDIR"]))
            out.mkdir(parents=True, exist_ok=True)
            for suffix in ("socket", "service"):
                template = (ROOT / "roles/mission_control/templates" / ("mission-control-ingress." + suffix + ".j2")).read_text()
                (out / ("mission-control-ingress." + suffix)).write_text(jinja2.Template(template).render() + "\n")
            (out / "mission-control-traefik.yml").write_text(yaml.safe_dump(dynamic))
        router = dynamic["http"]["routers"]["mission-control"]
        self.assertEqual(router["tls"]["certResolver"], "letencrypt")
        self.assertNotIn("buffering", dynamic["http"]["middlewares"]["mission-control-lan"])
        calls = []

        class Backend(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                calls.append(self.path)
                self.send_response(200)
                if self.path == "/events":
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(b": fixture heartbeat\n\n")
                    self.wfile.flush()
                else:
                    data = b"fixture"
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            proxy_port = sock.getsockname()[1]
        try:
            with tempfile.TemporaryDirectory(dir=isolated()) as directory:
                directory = Path(directory)
                # Two actual fixture peers stand for LAN and trusted Nebula clients.
                lan["sourceRange"] = ["127.0.0.1/32", "127.0.0.2/32"]
                router["tls"] = {}
                service = dynamic["http"]["services"]["mission-control"]["loadBalancer"]
                service["servers"] = [{"url": f"http://127.0.0.1:{backend.server_port}"}]
                del service["healthCheck"]
                catchall = yaml.safe_load((ROOT / "stacks/traefik/dynamic/catchall.yml").read_text())["http"]
                catchall["routers"]["catchall-router"]["tls"] = {}
                for key in ("routers", "middlewares"):
                    dynamic["http"][key].update(catchall[key])
                (directory / "dynamic.yml").write_text(yaml.safe_dump(dynamic))
                (directory / "static.yml").write_text(yaml.safe_dump(dict(
                    entryPoints=dict(websecure=dict(address=f"127.0.0.1:{proxy_port}")),
                    providers=dict(file=dict(filename=str(directory / "dynamic.yml"))))))
                with (directory / "proxy.log").open("w+") as log:
                    process = subprocess.Popen(["traefik", "--configFile=" + str(directory / "static.yml")],
                                               stdout=log, stderr=log)
                    try:
                        def request(path="/healthz", peer="127.0.0.1", method="GET", **headers):
                            c = http.client.HTTPSConnection("127.0.0.1", proxy_port, timeout=3,
                                context=ssl._create_unverified_context(), source_address=(peer, 0))
                            try:
                                c.request(method, path, headers={"Host": "chaldeas.ryzhkov.dev", **headers})
                                response = c.getresponse()
                                data = response.readline() if path == "/events" and response.status == 200 else response.read()
                                return response.status, dict(response.headers), data
                            finally:
                                c.close()
                        deadline = time.monotonic() + 10
                        while True:
                            try:
                                if request()[0] == 200:
                                    break
                            except OSError:
                                pass
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail("Traefik fixture failed: " + log.read())
                            time.sleep(0.05)
                        self.assertEqual(request("/events")[2], b": fixture heartbeat\n")
                        self.assertEqual(request(peer="127.0.0.2")[0], 200)
                        self.assertEqual(request("/events", peer="127.0.0.2")[2], b": fixture heartbeat\n")
                        before = len(calls)
                        for headers in ({"Cf-Ray": "fixture"}, {"cF-rAy": ""}):
                            status, response_headers, _ = request(**headers)
                            self.assertEqual(status, 302)
                            self.assertEqual(response_headers["Location"], "https://igor.ryzhkov.dev/404")
                        self.assertEqual(request(peer="127.0.0.3", **{"X-Forwarded-For": "10.42.0.6"})[0], 403)
                        self.assertEqual(request(method="POST")[0], 307)
                        self.assertEqual(len(calls), before)
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
        finally:
            backend.shutdown()
            thread.join()
            backend.server_close()

    def test_controller_pin_checked_against_real_fixture_git(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as directory:
            directory = Path(directory)
            canonical = directory / "canonical"
            canonical.mkdir()
            (canonical / "library").mkdir()
            (canonical / "library/.keep").write_text("fixture")
            for role in ("mission_control_runtime", "mission_control_reader"):
                path = canonical / "roles" / role / "tasks"
                path.mkdir(parents=True)
                (path / "main.yml").write_text(yaml.safe_dump([{"name": "Assert effective inventory selection",
                    "ansible.builtin.assert": {"that": [
                        "mission_control_runtime_enabled == fixture_selected | default(false)",
                        "mission_control_reader_enabled == fixture_selected | default(false)",
                        "mission_control_runtime_node == '/fixture/node'",
                        "mission_control_runtime_release_root == '/fixture/releases'"], "quiet": True}}]))
            defaults = canonical / "roles/mission_control_runtime/defaults"
            defaults.mkdir()
            (defaults / "main.yml").write_text(yaml.safe_dump(dict(
                mission_control_runtime_node="/fixture/node",
                mission_control_runtime_release_root="/fixture/releases")))
            def git(*args):
                return subprocess.run(["git", "-C", str(canonical), *args], check=True, text=True, capture_output=True).stdout.strip()
            git("init", "--quiet")
            git("add", ".")
            git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")
            revision = git("rev-parse", "HEAD")
            inventory = directory / "hosts.yml"
            inventory.write_text(yaml.safe_dump(dict(all=dict(hosts={"omarchy-normandy": dict(ansible_connection="local")}))))
            env = dict(os.environ, ANSIBLE_LIBRARY=str(canonical / "library"), ANSIBLE_ROLES_PATH=str(ROOT / "roles"),
                ANSIBLE_LOCAL_TEMP=str(directory / "local"), ANSIBLE_REMOTE_TEMP=str(directory / "remote"))
            def run(pin, limit="omarchy-normandy", library=None, check=False):
                values = dict(mission_control_fleet_checkout=str(canonical), mission_control_fleet_revision=pin,
                    ansible_become=False)
                return subprocess.run(["ansible-playbook", "-i", str(inventory), str(ROOT / "playbooks/mission-control.yml"),
                    "--limit", limit, *(["--check"] if check else []), "-e", json.dumps(values)],
                    env=env | ({"ANSIBLE_LIBRARY": library} if library is not None else {}), text=True, capture_output=True)
            good = run(revision)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("changed=0", good.stdout)
            inventory.write_text(yaml.safe_dump(dict(all=dict(hosts={"omarchy-normandy": dict(
                ansible_connection="local", fixture_selected=True, mission_control_runtime_enabled=True,
                mission_control_reader_enabled=True, mission_control_enabled=True)}))))
            selected = run(revision, check=True)
            self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
            self.assertNotEqual(run(revision, limit="all").returncode, 0)
            self.assertNotEqual(run(revision, library="/wrong/library").returncode, 0)
            self.assertNotEqual(run("a" * 40).returncode, 0)
            (canonical / "library/.keep").write_text("drift")
            self.assertNotEqual(run(revision).returncode, 0)

    def test_web_real_fixture_second_apply_and_offline_units(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as directory:
            directory = Path(directory)
            role = directory / "roles/mission_control"
            shutil.copytree(ROOT / "roles/mission_control", role)
            # Only file destinations/account ownership are relocated in this fixture.
            for path in role.rglob("*"):
                if path.is_file():
                    text = path.read_text().replace("/etc/mission-control", str(directory / "config")).replace(
                        "/etc/systemd/system", str(directory / "units"))
                    path.write_text(text)
            rows = yaml.safe_load((role / "tasks/main.yml").read_text())
            for row in rows:
                if "ansible.builtin.service_facts" in row:
                    row.pop("ansible.builtin.service_facts")
                    row["ansible.builtin.set_fact"] = dict(ansible_facts=dict(services={}))
            web = next(row for row in rows if row.get("name") == "Configure the independent Normandy web service")
            block = web["block"]
            web["block"] = [row for row in block if not any(key in row for key in (
                "ansible.builtin.user", "ansible.builtin.group"))]
            for row in web["block"]:
                for module in ("ansible.builtin.file", "ansible.builtin.template"):
                    if module in row:
                        row[module]["owner"] = str(os.getuid())
                        row[module]["group"] = str(os.getgid())
                if "ansible.builtin.systemd_service" in row:
                    row["ansible.builtin.fail"] = dict(msg="cold unit manager must not run")
                    del row["ansible.builtin.systemd_service"]
            (role / "tasks/main.yml").write_text(yaml.safe_dump(rows))
            (directory / "units").mkdir()
            values = dict(mission_control_enabled=True, mission_control_ingress_enabled=False,
                mission_control_runtime_enabled=True, mission_control_runtime_node="/bin/true",
                mission_control_runtime_release_root="/fixture", mission_control_runtime_version="client-v9.8.7")
            play = directory / "play.yml"
            def run(check=False):
                play.write_text(yaml.safe_dump([dict(hosts="omarchy-normandy", connection="local",
                    gather_facts=False, vars=values, roles=[str(role)])]))
                env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(directory / "local"), ANSIBLE_REMOTE_TEMP=str(directory / "remote"))
                return subprocess.run(["ansible-playbook", "-i", "omarchy-normandy,", str(play),
                    *(["--check", "--diff"] if check else [])], env=env, capture_output=True, text=True)
            cold = run(True)
            self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            web["block"] = [row for row in web["block"] if "ansible.builtin.fail" not in row]
            (role / "tasks/main.yml").write_text(yaml.safe_dump(rows))
            real = run()
            self.assertEqual(real.returncode, 0, real.stdout + real.stderr)
            again = run()
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            self.assertIn("changed=0", again.stdout)
            config = json.loads((directory / "config/server.json").read_text())
            self.assertEqual(config, dict(mode="cache", siteOrigin="https://chaldeas.ryzhkov.dev",
                listen="127.0.0.1", port=7392, cacheFile="/var/cache/mission-control/latest.json"))
            self.assertEqual((directory / "config/server.json").stat().st_mode & 0o777, 0o640)
            unit = directory / "units/mission-control.service"
            self.assertNotIn("ReadWritePaths", unit.read_text())
            self.assertIn("NoNewPrivileges=yes", unit.read_text())
            # Full rendered units are qualified together by --system-units in SYSTEM mode.


    def test_staged_four_unit_gate_refusal_failure_restart_and_idempotence(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as scratch:
            directory = Path(scratch)
            role = directory / "roles/mission_control"
            shutil.copytree(ROOT / "roles/mission_control", role)
            units = directory / "units"
            units.mkdir()
            # Without MC_FLEET_CHECKOUT this is an infra-only boundary fixture.
            fleet = os.environ.get("MC_FLEET_CHECKOUT")
            reader = directory / "roles/mission_control_reader" if fleet else None
            if reader:
                assert fleet is not None
                shutil.copytree(Path(fleet) / "roles/mission_control_reader", reader)
            else:
                (units / "mission-control-reader.service").write_text("[Service]\nExecStart=/bin/true\n")
            helper = directory / "manager.py"
            helper.write_text("""import json, os, pathlib, sys
root = pathlib.Path(os.environ['MC_FIXTURE_ROOT'])
events = root / 'events.jsonl'
mode = pathlib.Path(sys.argv[0]).name
def event(row):
    with events.open('a') as out: out.write(json.dumps(row) + '\\n')
if mode == 'systemd-analyze':
    paths = [pathlib.Path(p) for p in sys.argv[3:]]
    assert sys.argv[1:3] == ['verify', '--man=no']
    assert [p.name for p in paths] == ['mission-control-reader.service', 'mission-control.service', 'mission-control-ingress.socket', 'mission-control-ingress.service']
    assert all(p.is_file() for p in paths)
    event(dict(verify=sys.argv[1:], success=not bool(os.environ.get('MC_FIXTURE_FAIL'))))
    sys.exit(1 if os.environ.get('MC_FIXTURE_FAIL') else 0)
if mode == 'systemctl':
    service = json.loads(os.environ['MC_FIXTURE_SERVICES']).get('mission-control-ingress.socket', {})
    state = service.get('state', 'inactive')
    print('ActiveState=' + ('active' if state == 'running' else state))
    print('UnitFileState=' + service.get('status', 'disabled'))
    sys.exit(0)
if mode == 'getent':
    print('disposable fixture identity')
    sys.exit(0)
assert events.exists() and any(json.loads(x).get('success') for x in events.read_text().splitlines())
if mode == 'ufw':
    event(dict(ufw=sys.argv[1:]))
    path = root / ('ufw-' + sys.argv[6])
    changed = not path.exists()
    path.touch()
    print('Rule added' if changed else 'Skipping existing rule')
else:
    args = json.loads(sys.argv[1])
    event(dict(manager=args))
    path = root / 'manager.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    name = args.get('name', '')
    changed = bool(args.get('daemon_reload', False))
    if name:
        prior = state.get(name, {})
        desired = dict(enabled=args.get('enabled', prior.get('enabled', False)),
                       state='stopped' if args.get('state') == 'stopped' else 'started')
        changed |= prior != desired or args.get('state') == 'restarted'
        state[name] = desired
    path.write_text(json.dumps(state))
    print('CHANGED' if changed else 'UNCHANGED')
""")
            binary = directory / "bin"
            binary.mkdir()
            for name in ("systemd-analyze", "ufw", "systemctl", "getent"):
                target = binary / name
                target.write_text("#!" + sys.executable + "\n" + helper.read_text())
                target.chmod(0o755)
            def rewrite(rows):
                result = []
                for row in rows:
                    if "become" in row:
                        row["become"] = False
                    if any(key in row for key in ("ansible.builtin.user", "ansible.builtin.group")):
                        continue
                    if "block" in row:
                        row["block"] = rewrite(row["block"])
                    if "ansible.builtin.service_facts" in row:
                        row.pop("ansible.builtin.service_facts")
                        row["ansible.builtin.set_fact"] = dict(ansible_facts=dict(
                            services="{{ fixture_services | default({}) | dict2items | rejectattr('key', 'equalto', 'mission-control-ingress.socket') | items2dict }}"))
                    for module in ("ansible.builtin.file", "ansible.builtin.template"):
                        if module in row:
                            row[module]["owner"] = str(os.getuid())
                            row[module]["group"] = str(os.getgid())
                    if "ansible.builtin.systemd_service" in row:
                        args = row.pop("ansible.builtin.systemd_service")
                        row["ansible.builtin.command"] = dict(argv=[
                            sys.executable, str(helper), "{{ fixture_manager_args | to_json }}"])
                        row["vars"] = dict(fixture_manager_args=args)
                        row["register"] = "fixture_manager"
                        row["changed_when"] = "fixture_manager.stdout == 'CHANGED'"
                    result.append(row)
                return result
            for selected_role in [role] + ([reader] if reader else []):
                for path in selected_role.rglob("*"):
                    if path.is_file():
                        text = path.read_text().replace("/etc/mission-control-reader", str(directory / "reader-config")).replace(
                            "/etc/mission-control", str(directory / "config")).replace(
                            "/etc/systemd/system", str(units)).replace(
                            "/var/lib/mission-control-reader", str(directory / "reader-state")).replace(
                            "/var/cache/mission-control", str(directory / "cache"))
                        path.write_text(text)
                        if path.parent.name == "tasks":
                            path.write_text(yaml.safe_dump(rewrite(yaml.safe_load(text))))
            values = dict(mission_control_enabled=True, mission_control_ingress_enabled=True,
                mission_control_reader_enabled=True, mission_control_runtime_enabled=True,
                mission_control_runtime_node="/bin/true", mission_control_runtime_release_root="/fixture",
                mission_control_runtime_version="client-v9.8.7",
                mission_control_reader_uid=940, mission_control_reader_allowed_source_ids=["agent-a--mission-control"],
                mission_control_reader_cache_file=str(directory / "cache/latest.json"),
                mission_control_reader_grants=[dict(uid=940, audience="akasha", action="read",
                    source="mission-control-reader", host="omarchy-normandy", kind="agent_status",
                    profile="mission-control-reader", project="chaldea")])
            inventory = directory / "hosts.yml"
            inventory.write_text(yaml.safe_dump(dict(all=dict(children=dict(edge_proxy=dict(hosts={
                "edge1": dict(ansible_host="192.168.20.33"), "edge2": dict(ansible_host="192.168.20.4")}),
                backend=dict(hosts={"omarchy-normandy": dict(ansible_connection="local", ansible_host="192.168.70.234")}))))))
            env = dict(os.environ, PATH=str(binary) + ":" + os.environ["PATH"], MC_FIXTURE_ROOT=str(directory),
                ANSIBLE_LOCAL_TEMP=str(directory / "local"), ANSIBLE_REMOTE_TEMP=str(directory / "remote"))
            play = directory / "play.yml"
            def run(activate=False, check=False, warm=None, fail=False):
                params = values | dict(mission_control_activate=activate, fixture_services=warm or {})
                tasks = [{"ansible.builtin.import_role": dict(name=str(role), public=True)},
                         {"ansible.builtin.include_role": dict(name=str(role), tasks_from="verify")},
                         {"ansible.builtin.include_role": dict(name=str(role), tasks_from="activate"),
                          "when": "mission_control_activate | bool"}]
                if reader:
                    tasks.insert(1, {"ansible.builtin.include_role": dict(name=str(reader), public=True)})
                    tasks.insert(-1, {"ansible.builtin.include_role": dict(name=str(reader), tasks_from="activate"),
                                      "when": "mission_control_activate | bool"})
                play.write_text(yaml.safe_dump([dict(hosts="omarchy-normandy", gather_facts=False,
                    vars=params, tasks=tasks)]))
                return subprocess.run(["ansible-playbook", "-i", str(inventory), str(play),
                    *(["--check", "--diff"] if check else [])],
                    env=env | dict(MC_FIXTURE_SERVICES=json.dumps(warm or {}))
                        | (dict(MC_FIXTURE_FAIL="1") if fail else {}), capture_output=True, text=True)
            def passed(proc):
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            passed(run(check=True))
            self.assertFalse((directory / "config").exists())
            for unit in ("mission-control-reader.service", "mission-control.service",
                         "mission-control-ingress.socket", "mission-control-ingress.service"):
                for state in (dict(state="running", status="disabled"), dict(state="stopped", status="enabled")):
                    proc = run(warm={unit: state})
                    self.assertNotEqual(proc.returncode, 0, proc.stdout)
                    self.assertFalse((directory / "config").exists())
                    self.assertFalse((directory / "events.jsonl").exists())
            passed(run())
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
            self.assertEqual(len(events), 1)
            self.assertIn("verify", events[0])
            for name in ("mission-control.service", "mission-control-ingress.socket", "mission-control-ingress.service"):
                self.assertEqual((units / name).stat().st_mode & 0o777, 0o644)
            self.assertEqual((directory / "config/server.json").stat().st_mode & 0o777, 0o640)
            (directory / "events.jsonl").unlink()
            failed = run(activate=True, fail=True)
            self.assertNotEqual(failed.returncode, 0, failed.stdout)
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
            self.assertEqual(len(events), 1)
            self.assertFalse(events[0]["success"])
            self.assertFalse((directory / "manager.json").exists())
            (directory / "events.jsonl").unlink()
            passed(run(activate=True))
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
            self.assertTrue(events[0]["success"])
            rules = [row["ufw"] for row in events if "ufw" in row]
            self.assertEqual(rules, [
                ["allow", "in", "proto", "tcp", "from", peer, "to", "192.168.70.234", "port", "7393"]
                for peer in ("192.168.20.33", "192.168.20.4")])
            (directory / "config/server.json").write_text("{}")
            (units / "mission-control.service").write_text("[Service]\n")
            (units / "mission-control-ingress.socket").write_text("[Socket]\n")
            if reader:
                (directory / "reader-config/reader.json").write_text("{}")
                (units / "mission-control-reader.service").write_text("[Service]\n")
            before = (directory / "events.jsonl").read_text()
            proposed = run(activate=True, check=True)
            passed(proposed)
            self.assertIn('"mode": "cache"', proposed.stdout)
            self.assertIn("NoNewPrivileges=yes", proposed.stdout)
            self.assertIn("ListenStream=192.168.70.234:7393", proposed.stdout)
            if reader:
                self.assertIn("NoNewPrivileges=no", proposed.stdout)
                self.assertEqual((directory / "reader-config/reader.json").read_text(), "{}")
                self.assertEqual((units / "mission-control-reader.service").read_text(), "[Service]\n")
            self.assertEqual((units / "mission-control.service").read_text(), "[Service]\n")
            self.assertEqual((units / "mission-control-ingress.socket").read_text(), "[Socket]\n")
            self.assertEqual((directory / "config/server.json").read_text(), "{}")
            self.assertEqual((directory / "events.jsonl").read_text(), before)
            passed(run(activate=True))
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
            web_events = [row["manager"] for row in events if row.get("manager", {}).get("name") == "mission-control.service"]
            self.assertEqual(web_events[-1]["state"], "restarted")
            if reader:
                (directory / "reader-config/reader.json").write_text("{}")
                passed(run(activate=True))
                events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
                self.assertEqual([row["manager"] for row in events if row.get("manager", {}).get("name")
                    == "mission-control-reader.service"][-1]["state"], "restarted")
            again = run(activate=True)
            passed(again)
            self.assertIn("changed=0", again.stdout)
            (units / "mission-control-ingress.socket").write_text("[Socket]\n")
            passed(run(activate=True))
            events = [json.loads(x) for x in (directory / "events.jsonl").read_text().splitlines()]
            self.assertEqual([row["manager"] for row in events if row.get("manager", {}).get("name") == "mission-control-ingress.socket"][-1]["state"], "restarted")
            original_inventory = inventory.read_text()
            for old, bad in (("192.168.20.33", "192.168.20.99"), ("192.168.70.234", "192.168.70.235")):
                inventory.write_text(original_inventory.replace(old, bad))
                before = (directory / "events.jsonl").read_text()
                rejected = run(activate=True)
                self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
                self.assertEqual((directory / "events.jsonl").read_text(), before)
            inventory.write_text(original_inventory)



    def test_pc_existing_collector_registry_is_inert_until_permission(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as scratch:
            directory = Path(scratch)
            source = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]
            defaults = {}
            for name in source["vars_files"]:
                defaults.update(yaml.safe_load((ROOT / "playbooks" / name).read_text()))
            self.assertIs(defaults["mission_control_pc_permission"], False)
            defaults.update(akasha_enrollment_registry_enabled=True,
                akasha_quota_laptop_publisher_enabled=True, akasha_quota_pc_reader_enabled=True)
            old = (defaults["akasha_registry"] + defaults["akasha_enrollment_registry_additions"]
                + defaults["akasha_enrollment_quota_laptop_publisher"] + defaults["akasha_enrollment_quota_pc_reader"])
            self.assertEqual(len(old), 25)
            for enabled in (False, True):
                target = directory / ("pc.json" if enabled else "old.json")
                values = defaults | dict(mission_control_pc_permission=enabled)
                play = directory / "contract.yml"
                play.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
                    vars=values, pre_tasks=source["pre_tasks"], tasks=[
                        {"ansible.builtin.copy": dict(content="{{ akasha_registry | to_json }}",
                            dest=str(target), mode="0600")}])]))
                result = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                    env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(directory / "local"),
                        ANSIBLE_REMOTE_TEMP=str(directory / "remote")), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                registry = json.loads(target.read_text())
                if not enabled:
                    self.assertEqual(registry, old)
                else:
                    self.assertEqual(len(registry), 26)
                    self.assertTrue(all(row in registry for row in old if row["profile"] != "mission-control-reader"))
                    pc = next(row for row in registry if row["source"] == "omarchy-pc.mission-control")
                    self.assertEqual(pc["source_id"], "omarchy-pc--mission-control")
                    reader = next(row for row in registry if row["profile"] == "mission-control-reader")
                    self.assertEqual(reader["mission_control_read_sources"], ["agent-a--mission-control", "omarchy-pc--mission-control"])
                render = directory / "render.json"
                render.write_text(json.dumps(dict(registry=registry[len(defaults["akasha_registry"]):])))
                checked = subprocess.run([sys.executable, str(ROOT / "scripts/check-enrollment-registry.py"),
                    str(render), "--laptop-quota", "--pc-quota-reader", *(["--pc-mission-control"] if enabled else [])],
                    capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)



    def test_remaining_registry_scopes_and_laptop_qualification(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as scratch:
            directory = Path(scratch)
            source = yaml.safe_load((ROOT / "playbooks/akasha.yml").read_text())[0]
            defaults = {}
            for name in source["vars_files"]:
                defaults.update(yaml.safe_load((ROOT / "playbooks" / name).read_text()))
            self.assertIs(defaults["mission_control_agent_b_permission"], False)
            self.assertIs(defaults["mission_control_laptop_permission"], False)
            old = defaults["akasha_registry"] + defaults["akasha_enrollment_registry_additions"]
            seen = {}
            cases = [(pc, b, laptop, qualified, {}, False) for pc, b, laptop, qualified in [
                (False, False, False, False), (True, False, False, False),
                (False, True, False, False), (True, True, False, False),
                (True, False, True, True), (True, True, True, True),
                (True, True, True, False)]]
            cases += [
                (False, False, False, True, {}, False),
                (True, False, False, True, {}, False),
                (True, False, False, False, {"akasha_host_version": "host-v9.8.7"}, True),
                (True, False, False, True, {"mission_control_laptop_host_version": "host-v9.8.6"}, True),
                (True, False, False, True, {"mission_control_laptop_host_sha256": "b" * 64}, True),
                (True, False, True, True, {"akasha_host_version": "host-v0.2.4"}, True),
            ]
            for pc, b, laptop, qualified, overrides, reject in cases:
                target = directory / "registry.json"
                target.unlink(missing_ok=True)
                values = defaults | dict(akasha_enrollment_registry_enabled=True,
                    mission_control_pc_permission=pc, mission_control_agent_b_permission=b,
                    mission_control_laptop_permission=laptop, mission_control_laptop_qualified=qualified)
                if qualified:
                    # Fixture only. A real new artifact packet remains ROOT-owned.
                    values.update(akasha_host_version="host-v9.8.7", akasha_host_sha256="a" * 64,
                        mission_control_laptop_host_version="host-v9.8.7",
                        mission_control_laptop_host_sha256="a" * 64)
                values.update(overrides)
                play = directory / "contract.yml"
                play.write_text(yaml.safe_dump([dict(hosts="localhost", gather_facts=False,
                    vars=values, pre_tasks=source["pre_tasks"], tasks=[
                        {"ansible.builtin.copy": dict(content="{{ akasha_registry | to_json }}",
                            dest=str(target), mode="0600")}])]))
                result = subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local", str(play)],
                    env=dict(os.environ, ANSIBLE_LOCAL_TEMP=str(directory / "local"),
                        ANSIBLE_REMOTE_TEMP=str(directory / "remote")), capture_output=True, text=True)
                if reject or (laptop and not qualified):
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertFalse(target.exists())
                    continue
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                registry = json.loads(target.read_text())
                selection = (pc, b, laptop)
                self.assertEqual(registry, seen.setdefault(selection, registry))
                self.assertTrue(all(row in registry for row in old if row["profile"] != "mission-control-reader"))
                self.assertEqual(len(registry), len(old) + pc + b + laptop)
                reader = next(row for row in registry if row["profile"] == "mission-control-reader")
                self.assertEqual(reader["mission_control_read_sources"], ["agent-a--mission-control"] +
                    (["omarchy-pc--mission-control"] if pc else []) + (["agent-b--mission-control"] if b else []) +
                    (["omarchy-macbook-air-igor--mission-control"] if laptop else []))
                render = directory / "render.json"
                render.write_text(json.dumps(dict(registry=registry[len(defaults["akasha_registry"]):])))
                checked = subprocess.run([sys.executable, str(ROOT / "scripts/check-enrollment-registry.py"),
                    str(render), *(["--pc-mission-control"] if pc else []),
                    *(["--agent-b-mission-control"] if b else []), *(["--laptop-mission-control"] if laptop else [])],
                    capture_output=True, text=True)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


class SystemUnitsQualification(unittest.TestCase):
    """Separate required system-mode qualification; never counted as a source pass."""
    def test_exact_system_units_in_qualified_environment(self):
        with tempfile.TemporaryDirectory(dir=isolated()) as scratch:
            directory = Path(scratch)
            (directory / "units").mkdir()
            fleet = Path(os.environ["MC_FLEET_CHECKOUT"])
            values = dict(mission_control_runtime_node="/usr/bin/node",
                mission_control_runtime_release_root="/opt/mission-control",
                mission_control_runtime_version="client-v9.8.7")
            sources = {
                "mission-control-reader.service": fleet / "roles/mission_control_reader/templates/mission-control-reader.service.j2",
                **{name: ROOT / "roles/mission_control/templates" / (name + ".j2") for name in (
                    "mission-control.service", "mission-control-ingress.socket", "mission-control-ingress.service")},
            }
            for name, source in sources.items():
                rendered = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(source.read_text()).render(**values)
                (directory / "units" / name).write_text(rendered + "\n")
            runtime = directory / "xdg"
            runtime.mkdir(mode=0o700)
            rootfs = directory / "systemd-root"
            system = rootfs / "etc/systemd/system"
            system.mkdir(parents=True)
            for target in ("basic", "sysinit", "sockets", "multi-user", "shutdown"):
                (system / (target + ".target")).write_text("[Unit]\nDescription=Fixture target\n")
            for fixture_exec in ("usr/bin/node", "usr/lib/systemd/systemd-socket-proxyd"):
                target = rootfs / fixture_exec
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile("/bin/true", target)
                target.chmod(0o755)
            for source in (directory / "units").glob("*"):
                shutil.copyfile(source, system / source.name)
            result = subprocess.run(["systemd-analyze", "--root=" + str(rootfs), "verify", "--man=no",
                *sources],
                env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    # Explicit mandatory qualification is retained even where the job's /run is
    # read-only. It is not skipped or represented as a source-test success.
    system_units = "--system-units" in sys.argv
    if system_units:
        sys.argv.remove("--system-units")
    unittest.main(defaultTest="SystemUnitsQualification" if system_units else "MissionControlTest")
