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
                                             "192.168.90.0/24", "192.168.130.0/24"])
        for forbidden in ("8.8.8.8", "192.168.50.1", "10.42.0.5", "127.0.0.1"):
            self.assertFalse(any(ipaddress.ip_address(forbidden) in ipaddress.ip_network(net) for net in lan["sourceRange"]))
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
                lan["sourceRange"] = ["127.0.0.1/32"]  # fixture peers only
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
                        before = len(calls)
                        for headers in ({"Cf-Ray": "fixture"}, {"cF-rAy": ""}):
                            status, response_headers, _ = request(**headers)
                            self.assertEqual(status, 302)
                            self.assertEqual(response_headers["Location"], "https://igor.ryzhkov.dev/404")
                        self.assertEqual(request(peer="127.0.0.2", **{"X-Forwarded-For": "192.168.90.212"})[0], 403)
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
                (path / "main.yml").write_text("- name: Fixture canonical role\n  ansible.builtin.debug:\n    msg: fixture composition\n")
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
            def run(pin):
                values = dict(mission_control_fleet_checkout=str(canonical), mission_control_fleet_revision=pin)
                return subprocess.run(["ansible-playbook", "-i", str(inventory), str(ROOT / "playbooks/mission-control.yml"),
                    "--limit", "omarchy-normandy", "--check", "-e", json.dumps(values)], env=env, text=True, capture_output=True)
            good = run(revision)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("changed=0", good.stdout)
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
            block = rows[0]["block"]
            rows[0]["block"] = [row for row in block if not any(key in row for key in (
                "ansible.builtin.user", "ansible.builtin.group"))]
            for row in rows[0]["block"]:
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
                mission_control_runtime_enabled=True, mission_control_node="/bin/true",
                mission_control_release_root="/fixture", mission_control_version="client-v9.8.7")
            play = directory / "play.yml"
            def run(check=False):
                play.write_text(yaml.safe_dump([dict(hosts="omarchy-normandy", connection="local",
                    gather_facts=False, vars=values, roles=[str(role)])]))
                env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(directory / "local"), ANSIBLE_REMOTE_TEMP=str(directory / "remote"))
                return subprocess.run(["ansible-playbook", "-i", "omarchy-normandy,", str(play),
                    *(["--check", "--diff"] if check else [])], env=env, capture_output=True, text=True)
            cold = run(True)
            self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            rows[0]["block"] = [row for row in rows[0]["block"] if "ansible.builtin.fail" not in row]
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
            for suffix in ("service", "socket"):
                (directory / "units" / ("mission-control-ingress." + suffix)).write_text(
                    (ROOT / "roles/mission_control/templates" / ("mission-control-ingress." + suffix + ".j2")).read_text())
            runtime = directory / "xdg"
            runtime.mkdir(mode=0o700)
            rootfs = directory / "systemd-root"
            system = rootfs / "etc/systemd/system"
            system.mkdir(parents=True)
            for target in ("basic", "sysinit", "sockets", "multi-user", "shutdown"):
                (system / (target + ".target")).write_text("[Unit]\\nDescription=Fixture target\\n")
            for fixture_exec in ("bin/true", "usr/lib/systemd/systemd-socket-proxyd"):
                target = rootfs / fixture_exec
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile("/bin/true", target)
                target.chmod(0o755)
            for source in (directory / "units").glob("*"):
                shutil.copyfile(source, system / source.name)
            result = subprocess.run(["systemd-analyze", "--root=" + str(rootfs), "verify", "--man=no",
                *[p.name for p in (directory / "units").glob("*")]],
                env=dict(os.environ, XDG_RUNTIME_DIR=str(runtime)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
