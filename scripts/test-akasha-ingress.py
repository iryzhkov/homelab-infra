#!/usr/bin/env python3
"""Exercise the rendered ingress with a disposable Traefik and backend."""
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


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class IngressTest(unittest.TestCase):
    def test_paths_auth_body_rate_and_connection_peer_allowlist(self):
        self.assertEqual(os.environ["HOME"], os.environ["AKASHA_TEST_HOME"])
        self.assertIn(".scratch", os.environ["HOME"])
        binary = shutil.which("traefik")
        self.assertIsNotNone(binary, "install the checksum-pinned test Traefik")
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "roles/traefik/templates"),
                                 undefined=jinja2.StrictUndefined)
        env.filters["bool"] = bool
        defaults = yaml.safe_load((ROOT / "roles/traefik/defaults/main.yml").read_text())
        inventory = yaml.safe_load((ROOT / "inventory/group_vars/all.yml").read_text())
        rendered = yaml.safe_load(env.get_template("akasha.yml.j2").render(
            **defaults, traefik_cert_resolver=inventory["traefik_cert_resolver"],
            hostvars={"homelab": {"ansible_host": "192.168.70.223"}}))
        dynamic = rendered["http"]
        router = dynamic["routers"]["akasha"]
        self.assertEqual(router["tls"]["certResolver"], "letencrypt")
        self.assertNotIn("ipStrategy", dynamic["middlewares"]["akasha-lan"]["ipAllowList"])
        ranges = dynamic["middlewares"]["akasha-lan"]["ipAllowList"]["sourceRange"]
        self.assertTrue(all(ipaddress.ip_network(r).is_private for r in ranges))
        self.assertFalse(any(ipaddress.ip_address("10.42.0.5") in ipaddress.ip_network(r) for r in ranges))
        calls = []

        class Backend(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def answer(self):
                calls.append(self.path)
                if self.headers.get("Content-Length"):
                    self.rfile.read(int(self.headers["Content-Length"]))
                status = 200 if self.path == "/healthz" or self.headers.get("Authorization") == "Bearer fixture" else 401
                data = json.dumps({"ok": status == 200}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = answer
            do_POST = answer

        backend = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Backend)
        thread = threading.Thread(target=backend.serve_forever, daemon=True)
        thread.start()
        proxy_port = port()
        dynamic["services"]["akasha"]["loadBalancer"]["servers"] = [{"url": f"http://127.0.0.1:{backend.server_port}"}]
        dynamic["middlewares"]["akasha-lan"]["ipAllowList"]["sourceRange"] = ["127.0.0.1/32"]
        router["tls"] = {}  # Built-in disposable certificate, no ACME/network/credentials.
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "dynamic.yml").write_text(yaml.safe_dump(rendered))
                (root / "static.yml").write_text(yaml.safe_dump({
                    "entryPoints": {"websecure": {"address": f"127.0.0.1:{proxy_port}"}},
                    "providers": {"file": {"filename": str(root / "dynamic.yml")}},
                }))
                with (root / "proxy.log").open("w+") as log:
                    process = subprocess.Popen([binary, "--configFile=" + str(root / "static.yml")], stdout=log, stderr=log)
                    try:
                        def request(path, method="GET", body=None, peer="127.0.0.1", **headers):
                            connection = http.client.HTTPSConnection("127.0.0.1", proxy_port, timeout=3,
                                context=ssl._create_unverified_context(), source_address=(peer, 0))
                            try:
                                connection.request(method, path, body=body, headers={"Host": "akasha.ryzhkov.dev", **headers})
                                response = connection.getresponse()
                                response.read()
                                return response.status
                            finally:
                                connection.close()
                        deadline = time.monotonic() + 10
                        while True:
                            try:
                                if request("/healthz") == 200:
                                    break
                            except OSError:
                                pass
                            if process.poll() is not None or time.monotonic() > deadline:
                                log.seek(0)
                                self.fail("Traefik did not load the route: " + log.read())
                            time.sleep(0.05)
                        self.assertEqual(request("/api/entries"), 401)
                        self.assertEqual(request("/healthz", Authorization="Bearer fixture"), 200)
                        for path in ("/api/entries", "/api/entries/42", "/api/v1/events", "/api/v1/receipts"):
                            self.assertEqual(request(path, Authorization="Bearer fixture"), 200, path)
                        for path in ("/", "/index.html", "/api/entries/nope", "/api/v1/health", "/metrics", "/debug/pprof"):
                            self.assertEqual(request(path), 404, path)
                        self.assertEqual(request("/api/entries", method="DELETE"), 404)
                        self.assertEqual(request("/api/entries", method="POST", Host="other.test"), 404)
                        before = len(calls)
                        # A tunnel connector is an allowed LAN peer. Its Cloudflare
                        # marker must exclude the route even with a valid bearer.
                        for header in ({"Cf-Ray": "230b030023ae2822-SJC"},
                                       {"cF-rAy": "230b030023ae2822-SJC"}, {"Cf-Ray": ""}):
                            for method, path in (("GET", "/healthz"), ("GET", "/api/entries"),
                                                 ("POST", "/api/v1/events")):
                                self.assertEqual(request(path, method=method, Authorization="Bearer fixture",
                                                        **header), 404, (method, path, header))
                        self.assertEqual(len(calls), before)
                        self.assertEqual(request("/api/v1/events", peer="127.0.0.2",
                                                **{"X-Forwarded-For": "192.168.90.212"}), 403)
                        self.assertEqual(len(calls), before)
                        self.assertEqual(request("/api/v1/events", method="POST", body=b"x" * 262145,
                                                Authorization="Bearer fixture"), 413)
                        self.assertEqual(len(calls), before)
                        self.assertEqual(request("/api/v1/events", **{"X-Forwarded-For": "8.8.8.8"}), 401)
                        statuses = [request("/api/entries") for _ in range(130)]
                        self.assertIn(429, statuses)
                    finally:
                        process.terminate()
                        process.wait(timeout=10)
        finally:
            backend.shutdown()
            thread.join()
            backend.server_close()

    def test_socket_keeps_the_gateway_loopback_and_allows_only_edges(self):
        hostvars = {"public-api": {"ansible_host": "192.168.20.33"},
                    "public-api-b": {"ansible_host": "192.168.20.4"}}
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(ROOT / "roles/akasha_ingress/templates"),
                                 undefined=jinja2.StrictUndefined)
        for name in ("socket", "service"):
            text = env.get_template(f"akasha-ingress.{name}.j2").render(
                ansible_host="192.168.70.223", groups={"edge_proxy": list(hostvars)}, hostvars=hostvars)
            if name == "socket":
                self.assertIn("ListenStream=192.168.70.223:1936", text)
                self.assertIn("FreeBind=true", text)
            self.assertIn("IPAddressDeny=any", text)
            self.assertIn("IPAddressAllow=192.168.20.33/32", text)
            self.assertIn("IPAddressAllow=192.168.20.4/32", text)
        self.assertIn("127.0.0.1:1935", text)
        config = (ROOT / "roles/akasha/templates/config.json.j2").read_text()
        self.assertIn("'bind': '127.0.0.1'", config)


if __name__ == "__main__":
    unittest.main()
