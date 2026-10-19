#!/usr/bin/env python3
"""Render, enable, disable and proxy the opt-in Jocasta MCP canary route.

Source configuration only. Ansible runs against localhost into a scratch
directory and Traefik serves its built-in certificate: no inventory, SSH, ACME,
DNS or credentials, and no proof of the live edge.
"""
import hashlib
import http.client
import http.server
import io
import ipaddress
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.request

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
HOST = "jocasta.mcp.ryzhkov.dev"
ORIGIN = "https://" + HOST
UPSTREAM = "127.0.0.1:18935"
BACKEND = "http://10.42.0.99:18935"  # synthetic; only rendered, never contacted
OUT = "jocasta-mcp-canary.yml"
TRAEFIK = ("https://github.com/traefik/traefik/releases/download/v3.7.15/traefik_v3.7.15_linux_amd64.tar.gz",
           "5b9f51199fcb7a3b5f7ae0f9dae8dcf01efadafd92c5fca96ea2822eca81f8e6")
ENABLED = {"jocasta_mcp_canary_route_enabled": True,
           "jocasta_mcp_canary_backend_url": BACKEND, "jocasta_mcp_canary_upstream_host": UPSTREAM}


def scratch():
    home, tmp = Path(os.environ["HOME"]).resolve(), Path(os.environ["TMPDIR"]).resolve()
    assert os.environ["HOME"] == os.environ["JOCASTA_CANARY_TEST_HOME"]
    assert home.is_relative_to(tmp) and ".scratch" in home.name
    assert not tmp.is_relative_to("/tmp")
    return tmp


def loopback(authority):
    host = authority.rsplit(":", 1)[0].strip("[]")
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def traefik(tmp):
    binary = tmp / "bin" / "traefik"
    if not binary.exists():
        raw = urllib.request.urlopen(TRAEFIK[0], timeout=60).read()
        assert hashlib.sha256(raw).hexdigest() == TRAEFIK[1]
        binary.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            binary.write_bytes(archive.extractfile("traefik").read())
        binary.chmod(0o755)
    return binary


class CanaryRouteTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(dir=scratch()))
        self.dynamic = self.dir / "dynamic"
        self.dynamic.mkdir()
        self.sibling = self.dynamic / "generated.yml"
        self.sibling.write_text("http: {}\n")
        (self.dir / "ansible.cfg").write_text("[defaults]\n")
        (self.dir / "play.yml").write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local", "gather_facts": False,
            "tasks": [{"ansible.builtin.include_role": {"name": "traefik", "tasks_from": "jocasta-mcp-canary"}}]}]))

    def tearDown(self):
        shutil.rmtree(self.dir)

    def play(self, **extra):
        variables = {"traefik_dynamic_dir": str(self.dynamic), "traefik_cert_resolver": "letencrypt",
                     "ansible_python_interpreter": sys.executable, **extra}
        (self.dir / "vars.json").write_text(json.dumps(variables))
        env = {**os.environ, "ANSIBLE_CONFIG": str(self.dir / "ansible.cfg"),
               "ANSIBLE_ROLES_PATH": str(ROOT / "roles"), "LC_ALL": "C.UTF-8"}
        return subprocess.run([str(Path(sys.executable).parent / "ansible-playbook"), "-i", "localhost,",
                               "-e", "@vars.json", "play.yml"], cwd=self.dir, env=env,
                              capture_output=True, text=True, timeout=300)

    def enable(self):
        run = self.play(**ENABLED)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        return yaml.safe_load((self.dynamic / OUT).read_text())

    def test_default_is_disabled_and_global_wildcard_unchanged(self):
        defaults = yaml.safe_load((ROOT / "roles/traefik/defaults/main.yml").read_text())
        self.assertIs(defaults["jocasta_mcp_canary_route_enabled"], False)
        self.assertNotIn("jocasta_mcp_canary_backend_url", defaults)
        self.assertNotIn("jocasta_mcp_canary_upstream_host", defaults)
        tasks = yaml.safe_load((ROOT / "roles/traefik/tasks/main.yml").read_text())
        self.assertEqual([t["ansible.builtin.import_tasks"] for t in tasks if "ansible.builtin.import_tasks" in t],
                         ["jocasta-mcp-canary.yml"])
        run = self.play()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse((self.dynamic / OUT).exists())
        self.assertEqual(sorted(p.name for p in self.dynamic.iterdir()), ["generated.yml"])
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "roles/traefik/templates"),
                                 undefined=jinja2.StrictUndefined)
        env.filters["bool"] = bool
        tls = yaml.safe_load("x:\n" + env.get_template("router-tls.j2").render(
            **defaults, traefik_cert_resolver="letencrypt"))["x"]["tls"]
        self.assertEqual(tls["domains"], [{"main": "ryzhkov.dev", "sans": ["*.ryzhkov.dev"]}])

    def test_enable_requires_explicit_dedicated_backend(self):
        bad = [{}, {"jocasta_mcp_canary_backend_url": BACKEND}, {"jocasta_mcp_canary_upstream_host": UPSTREAM}]
        for url in ("http://10.42.0.2:1935", "http://10.42.0.2:01935", "http://127.0.0.1:18935",
                    "http://localhost:18935", BACKEND + "/mcp", "https://10.42.0.99:18935", "http://10.42.0.99",
                    "http://user@10.42.0.99:18935", [BACKEND]):
            bad.append({"jocasta_mcp_canary_backend_url": url, "jocasta_mcp_canary_upstream_host": UPSTREAM})
        for host in (HOST, "10.42.0.2:18935", "127.0.0.1", "localhost:18935/x", "evil.test:80"):
            bad.append({"jocasta_mcp_canary_backend_url": BACKEND, "jocasta_mcp_canary_upstream_host": host})
        for case in bad:
            with self.subTest(case=case):
                run = self.play(jocasta_mcp_canary_route_enabled=True, **case)
                self.assertNotEqual(run.returncode, 0)
                self.assertIn("dedicated", run.stdout, run.stdout + run.stderr)
                self.assertFalse((self.dynamic / OUT).exists())
        run = self.play(**{**ENABLED, "jocasta_mcp_canary_upstream_host": "localhost:18935"})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_enabled_route_shape_then_disable_removes_only_its_file(self):
        dynamic = self.enable()["http"]
        self.assertEqual((self.dynamic / OUT).stat().st_mode & 0o777, 0o644)
        self.assertEqual(set(dynamic), {"routers", "middlewares", "services"})
        self.assertEqual(list(dynamic["routers"]), ["jocasta-mcp-canary"])
        router = dynamic["routers"]["jocasta-mcp-canary"]
        self.assertEqual(router["entryPoints"], ["websecure"])
        self.assertEqual(router["rule"], "Host(`jocasta.mcp.ryzhkov.dev`) && !HeaderRegexp(`Cf-Ray`, `.*`)")
        self.assertNotIn("Path", router["rule"])
        self.assertNotIn("priority", router)
        self.assertEqual(router["tls"], {"certResolver": "letencrypt", "domains": [{"main": HOST}]})
        # The only middleware is the Host rewrite: no forward-auth, prefix strip or allowlist.
        self.assertEqual(router["middlewares"], ["jocasta-mcp-canary-host"])
        self.assertEqual(dynamic["middlewares"], {"jocasta-mcp-canary-host": {
            "headers": {"customRequestHeaders": {"Host": UPSTREAM}}}})
        balancer = dynamic["services"]["jocasta-mcp-canary"]["loadBalancer"]
        self.assertEqual(balancer["servers"], [{"url": BACKEND}])
        self.assertEqual(balancer["healthCheck"]["path"], "/healthz")
        self.assertNotIn("passHostHeader", balancer)
        self.assertEqual(self.sibling.read_text(), "http: {}\n")
        run = self.play(**{**ENABLED, "jocasta_mcp_canary_route_enabled": False})
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertFalse((self.dynamic / OUT).exists())
        self.assertEqual(sorted(p.name for p in self.dynamic.iterdir()), ["generated.yml"])
        self.assertEqual(self.sibling.read_text(), "http: {}\n")

    def test_traefik_sends_every_path_with_loopback_host_and_public_origin(self):
        binary = traefik(scratch())
        rendered = self.enable()
        calls = []

        class Backend(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def answer(self):
                if self.headers.get("Content-Length"):
                    self.rfile.read(int(self.headers["Content-Length"]))
                host = self.headers.get("Host", "")
                calls.append({"method": self.command, "path": self.path, "host": host,
                              "origin": self.headers.get("Origin")})
                # Same rule as the MCP SDK's DNS rebinding protection; this listener is loopback.
                status = 200 if loopback(host) else 403
                data = json.dumps({"ok": status == 200}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = answer
            do_POST = answer

        backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        threading.Thread(target=backend.serve_forever, daemon=True).start()
        dynamic = rendered["http"]
        dynamic["services"]["jocasta-mcp-canary"]["loadBalancer"]["servers"] = [
            {"url": f"http://127.0.0.1:{backend.server_port}"}]
        dynamic["routers"]["jocasta-mcp-canary"]["tls"] = {}  # built-in certificate, no ACME
        proxy_port = free_port()
        (self.dir / "dynamic.yml").write_text(yaml.safe_dump(rendered))
        (self.dir / "static.yml").write_text(yaml.safe_dump({
            "entryPoints": {"websecure": {"address": f"127.0.0.1:{proxy_port}"}},
            "providers": {"file": {"filename": str(self.dir / "dynamic.yml")}}}))

        def request(path, method="GET", headers=None, direct_port=None):
            if direct_port is None:
                connection = http.client.HTTPSConnection("127.0.0.1", proxy_port, timeout=3,
                                                         context=ssl._create_unverified_context())
            else:
                connection = http.client.HTTPConnection("127.0.0.1", direct_port, timeout=3)
            try:
                body = b'{"jsonrpc":"2.0","id":1,"method":"ping"}' if method == "POST" else None
                connection.request(method, path, body=body, headers={"Host": HOST, **(headers or {})})
                response = connection.getresponse()
                response.read()
                return response.status
            finally:
                connection.close()

        # Negative control: the backend refuses the public Host without the rewrite.
        self.assertEqual(request("/mcp", "POST", direct_port=backend.server_port), 403)
        calls.clear()
        with (self.dir / "proxy.log").open("w+") as log:
            process = subprocess.Popen([str(binary), "--configFile=" + str(self.dir / "static.yml")],
                                       stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        if request("/healthz") == 200:
                            break
                    except OSError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        log.seek(0)
                        self.fail("Traefik did not serve the route: " + log.read())
                    time.sleep(0.1)
                for method, path in (("GET", "/healthz"), ("POST", "/mcp"),
                                     ("GET", "/.well-known/oauth-protected-resource/mcp"),
                                     ("GET", "/.well-known/oauth-protected-resource")):
                    with self.subTest(path=path):
                        calls.clear()
                        self.assertEqual(request(path, method, {"Origin": ORIGIN}), 200)
                        self.assertEqual(calls, [{"method": method, "path": path, "host": UPSTREAM,
                                                  "origin": ORIGIN}])
                calls.clear()
                self.assertEqual(request("/mcp", "POST", {"Cf-Ray": "230b030023ae2822-SJC"}), 404)
                self.assertEqual(request("/mcp", "POST", {"Host": "jocasta.ryzhkov.dev"}), 404)
                self.assertEqual(request("/mcp", "POST", {"Host": "other.mcp.ryzhkov.dev"}), 404)
                self.assertEqual([c for c in calls if c["path"] != "/healthz"], [])
            finally:
                process.terminate()
                process.wait(timeout=10)
                backend.shutdown()
                backend.server_close()


if __name__ == "__main__":
    unittest.main()
