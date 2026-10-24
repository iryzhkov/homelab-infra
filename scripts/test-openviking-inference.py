#!/usr/bin/env python3
"""Protocol, failover, provisioning and capacity checks. Run in an asserted scratch HOME.

OPENVIKING_NATIVE_TEST=1 requires the real OpenViking 0.5.2 package, never a stub.
The ordinary CI command runs all other tests; native checks use a separate pinned uv job.
"""
import asyncio
import contextlib
import hashlib
import http.client
import importlib.util
import json
import math
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from typing import Any, cast
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
STACK = ROOT / "stacks/openviking"
HOME = os.environ.get("OPENVIKING_INFERENCE_TEST_HOME", "")
assert HOME and os.environ.get("HOME") == HOME, "set and assert the scratch HOME"
assert Path(tempfile.gettempdir()).resolve().is_relative_to(Path(HOME).resolve()), "TMPDIR must be inside HOME"


def load(name, path) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


adapter = load("embedding_adapter", "stacks/openviking/files/embedding-adapter.py")
pc = load("pc_preflight", "roles/ov_pc_coexistence/files/preflight.py")
RECIPE = json.loads((STACK / "recipes/models.json").read_text())
CONFIG = json.loads((STACK / "recipes/providers-052.json").read_text())


class Runtime(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cast(Any, self.server).calls.append((self.path, request))
        cast(Any, self.server).entered.set()
        cast(Any, self.server).release.wait(2)
        time.sleep(cast(Any, self.server).delay)
        status, body = cast(Any, self.server).reply
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        with contextlib.suppress(OSError):
            self.wfile.write(raw)

    def log_message(self, format, *args):
        pass


def start(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def stop(server):
    server.shutdown()
    server.server_close()


class Protocol(unittest.TestCase):
    def setUp(self):
        self.upstream: Any = ThreadingHTTPServer(("127.0.0.1", 0), Runtime)
        self.primary = None
        self.upstream.calls = []
        self.upstream.entered = threading.Event()
        self.upstream.release = threading.Event()
        self.upstream.release.set()
        self.upstream.delay = 0
        self.upstream.reply = (200, {"embeddings": [[1 / 32] * 1024], "prompt_eval_count": 17})
        start(self.upstream)
        self.old = adapter.UPSTREAMS
        adapter.UPSTREAMS = {route: ("127.0.0.1", self.upstream.server_port, 1)
                             for route in ("pc", "cpu")}
        self.bridge = start(adapter.Server(("127.0.0.1", 0)))
        self.base = f"http://127.0.0.1:{self.bridge.server_port}"

    def tearDown(self):
        self.upstream.release.set()
        stop(self.bridge)
        stop(self.upstream)
        if self.primary is not None:
            stop(self.primary)
        adapter.UPSTREAMS = self.old

    def request(self, data=None, route="pc", raw=None, method="POST", path=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.bridge.server_port, timeout=3)
        body = raw if raw is not None else json.dumps(data or self.body()).encode()
        connection.request(method, path or f"/{route}/v1/embeddings", body,
                           headers or {"Content-Type": "application/json"})
        response = connection.getresponse()
        status, result = response.status, json.loads(response.read())
        connection.close()
        return status, result

    def body(self, **kwargs):
        return dict(model=adapter.MODEL, input="plain text", input_type="document",
                    encoding_format="float", **kwargs)

    def test_query_prefix_documents_usage_and_fixed_native_options(self):
        for route in ("pc", "cpu"):
            for kind in ("query", "document"):
                body = self.body()
                body["input_type"] = kind
                status, response = self.request(body, route=route)
                self.assertEqual(status, 200)
                self.assertEqual(len(response["data"][0]["embedding"]), 1024)
                self.assertEqual(response["usage"], {"prompt_tokens": 17, "total_tokens": 17})
                path, native = self.upstream.calls[-1]
                self.assertEqual(path, "/api/embed")
                self.assertEqual(native, {"input": (adapter.PREFIX if kind == "query" else "") + "plain text",
                    "model": adapter.MODEL, "truncate": False, "keep_alive": -1,
                    "options": {"num_ctx": 4096, "num_thread": 2}})

    def test_rejects_arrays_tokens_empty_unknown_fields_and_prefixed_queries(self):
        for change in [
            {"input": ["one"]}, {"input": [1, 2]}, {"input": []}, {"input": ""}, {"input": " "},
            {"input": "\x00"}, {"input": {"url": "http://elsewhere"}},
            {"model": "other"}, {"input_type": "passage"}, {"encoding_format": "base64"},
            {"dimensions": 512}, {"dimensions": True}, {"upstream": "http://elsewhere"},
            {"prefix": "arbitrary"}, {"input_type": "query", "input": adapter.PREFIX + "already"}
        ]:
            with self.subTest(change=change):
                body = self.body()
                body.update(change)
                self.assertEqual(self.request(body)[0], 400)
        self.assertEqual(self.upstream.calls, [])

    def test_bounded_http_body_and_strict_json(self):
        for raw, status in [(b"x" * 65537, 413), (b'{"input":"one","input":"two"}', 400),
                            (b"NaN", 400), (b"[]", 400), (b"null", 400), (b"\xff", 400)]:
            self.assertEqual(self.request(raw=raw)[0], status)
        self.assertEqual(self.request(path="/cpu/v1/embeddings?url=http://other")[0], 404)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.upstream.calls, [])

    def test_wrong_vector_counts_dimensions_values_norm_and_usage_are_never_successful(self):
        for body in [
            {"embeddings": []}, {"embeddings": [[0.1] * 1024] * 2},
            {"embeddings": [[0.1] * 512]}, {"embeddings": [[0] * 1024]},
            {"embeddings": [[math.nan] * 1024]}, {"embeddings": [[True] * 1024]},
            {"embeddings": [[1 / 32] * 1024], "prompt_eval_count": -1},
            {"embeddings": [[1 / 32] * 1024], "prompt_eval_count": 4097},
            {"embeddings": [[1 / 32] * 1024], "prompt_eval_count": True},
            b"not-json", b"x" * 131073
        ]:
            self.upstream.reply = (200, body)
            self.assertEqual(self.request()[0], 502)

    def test_native_oversize_is_permanent_while_endpoint_failures_are_transient(self):
        for upstream, body, expected in [
            (400, {"error": "the input length exceeds the context length"}, 400),
            (404, {"error": "model absent secret document"}, 502),
            (401, {"error": "private credential"}, 502),
            (429, {"error": "busy"}, 503), (500, {"error": "private text"}, 503),
            (302, {"error": "redirect"}, 502),
        ]:
            self.upstream.reply = upstream, body
            status, response = self.request()
            self.assertEqual(status, expected)
            self.assertNotIn("private", json.dumps(response))
            self.assertNotIn("secret", json.dumps(response))

    def test_refused_connection_and_read_timeout_fail_promptly(self):
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        unused.close()
        adapter.UPSTREAMS["pc"] = ("127.0.0.1", port, 1)
        began = time.monotonic()
        self.assertEqual(self.request()[0], 503)
        self.assertLess(time.monotonic() - began, 1)
        adapter.UPSTREAMS["cpu"] = ("127.0.0.1", self.upstream.server_port, 0.03)
        self.upstream.delay = 0.15
        self.assertEqual(self.request(route="cpu")[0], 504)

    def test_connect_timeout_is_separate_from_long_cpu_read_timeout(self):
        with patch.object(adapter.http.client, "HTTPConnection") as connection:
            connection.return_value.connect.side_effect = socket.timeout()
            with self.assertRaises(adapter.Rejected) as caught:
                adapter.embed("cpu", adapter.payload(self.body()))
            self.assertEqual(caught.exception.status, 504)
            self.assertEqual(connection.call_args.kwargs["timeout"], 2)

    def test_concurrency_is_one_and_health_never_infers_or_queues(self):
        self.upstream.release.clear()
        result = []
        worker = threading.Thread(target=lambda: result.append(self.request()))
        worker.start()
        self.assertTrue(self.upstream.entered.wait(1))
        self.assertEqual(self.request(route="cpu")[0], 429)
        self.assertEqual(self.request(method="GET", path="/health")[0], 200)
        self.assertEqual(len(self.upstream.calls), 1)
        self.upstream.release.set()
        worker.join(3)
        self.assertEqual(result[0][0], 200)


class SourceContracts(unittest.TestCase):
    def test_manifest_pins_and_model_layer_match(self):
        for name in ("embeddings", "generation"):
            recipe = RECIPE[name]
            data = (STACK / recipe["manifest"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), recipe["manifest_sha256"])
            manifest = json.loads(data)
            pins = {file["sha256"]: file for file in recipe["files"]}
            for entry in [manifest["config"], *manifest["layers"]]:
                self.assertEqual(pins[entry["digest"].split(":")[1]]["size"], entry["size"])
        self.assertEqual(RECIPE["embeddings"]["files"][1]["size"], 639150592)
        for item in RECIPE["embeddings"]["files"]:
            if "bundled" in item:
                data = (STACK / item["bundled"]).read_bytes()
                self.assertEqual(len(data), item["size"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), item["sha256"])

    def test_cpu_compose_and_overlay_adapter_have_no_ports_and_zero_swap_allowance(self):
        variables = yaml.safe_load((ROOT / "inventory/group_vars/swarm/openviking-inference.yml").read_text())
        template = jinja2.Environment(undefined=jinja2.StrictUndefined).from_string(
            (STACK / "templates/inference-compose.yml.j2").read_text())
        for name, value in variables["openviking_inference"].items():
            rendered = template.render(ov_provider={"key": name, "value": value},
                ov_inference_recipes=RECIPE, ov_runtime_root="/srv/provider",
                openviking_inference_network=variables["openviking_inference_network"])
            service = yaml.safe_load(rendered)["services"][name]
            self.assertNotIn("ports", service)
            self.assertEqual(service["mem_limit"], service["memswap_limit"])
            self.assertEqual(service["mem_limit"], 2 * 1024 ** 3)
            self.assertEqual(service["cpus"], 2)
        stack = yaml.safe_load((STACK / "stack.yml").read_text())
        self.assertEqual(set(stack["services"]), {"openviking", "embedding-adapter"})
        self.assertNotIn("ports", stack["services"]["embedding-adapter"])
        self.assertTrue(stack["services"]["embedding-adapter"]["read_only"])
        self.assertIn("e9a7222f", stack["services"]["openviking"]["image"])

    def test_pc_preflight_preserves_game_and_generation(self):
        local = {"name": "local:latest", "size_vram": 6_000_000_000, "digest": "stable"}
        self.assertFalse(pc.check([local], 1600)["embedding_loaded"])
        with self.assertRaises(ValueError):
            pc.check([local], 1500)
        with self.assertRaises(ValueError):
            pc.check([], 20000)
        with self.assertRaises(ValueError):
            pc.check([dict(local, size_vram=0)], 20000)
        self.assertTrue(pc.check([local, {"name": adapter.MODEL}], 300)["embedding_loaded"])
        with self.assertRaises(ValueError):
            pc.check([local, {"name": adapter.MODEL}], 255)


class Provisioning(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="model-pin-"))
        self.source = self.work / "source"
        self.dest = self.work / "installed"
        self.manifest = self.work / "manifest.json"
        self.manifest.write_text('{"fixture":true}\n')
        data = b"small checksummed model fixture"
        digest = hashlib.sha256(data).hexdigest()
        path = "blobs/sha256-" + digest
        (self.source / "blobs").mkdir(parents=True)
        (self.source / path).write_bytes(data)
        bundled = b"bundled metadata"
        (self.work / "bundled.json").write_bytes(bundled)
        bundled_sha = hashlib.sha256(bundled).hexdigest()
        self.recipe = {"files": [{"path": path, "sha256": digest, "size": len(data)},
            {"path": "blobs/sha256-" + bundled_sha, "sha256": bundled_sha,
             "size": len(bundled), "bundled": "bundled.json"}],
            "manifest_path": "manifests/registry.ollama.ai/library/fixture/latest",
            "manifest": "manifest.json", "manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest()}
        self.variables = {"ov_recipe": self.recipe, "ov_model_root": str(self.dest),
            "ov_model_owner": str(os.getuid()), "ov_model_host": "localhost",
            "ov_model_source": "file://" + str(self.source), "swarm_stack_src": str(self.work)}
        play = [{"hosts": "localhost", "gather_facts": False, "tasks": [
            {"ansible.builtin.include_tasks": str(STACK / "model-prepare.yml")}]}]
        (self.work / "play.yml").write_text(yaml.safe_dump(play))

    def tearDown(self):
        shutil.rmtree(self.work)

    def run_play(self):
        (self.work / "vars.json").write_text(json.dumps(self.variables))
        env = dict(os.environ, ANSIBLE_CONFIG=str(self.work / "ansible.cfg"),
            ANSIBLE_LOCAL_TEMP=str(self.work / "local"), ANSIBLE_REMOTE_TEMP=str(self.work / "remote"),
            ANSIBLE_NOCOLOR="1")
        return subprocess.run(["ansible-playbook", "-i", "localhost,", "-c", "local",
            str(self.work / "play.yml"), "-e", "@" + str(self.work / "vars.json"),
            "-e", "ansible_become=false", "-e", "ansible_python_interpreter=" + str(shutil.which("python3"))],
            env=env, capture_output=True, text=True, timeout=120)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout[-5000:] + result.stderr[-1000:])

    def test_installs_verified_bytes_and_is_idempotent(self):
        self.assert_ok(self.run_play())
        self.assertEqual((self.dest / self.recipe["manifest_path"]).read_bytes(), self.manifest.read_bytes())
        for item in self.recipe["files"]:
            self.assertEqual(hashlib.sha256((self.dest / item["path"]).read_bytes()).hexdigest(), item["sha256"])
        self.assert_ok(second := self.run_play())
        self.assertRegex(second.stdout, r"changed=0\s")

    def test_rejects_bad_source_checksum(self):
        (self.source / self.recipe["files"][0]["path"]).write_bytes(b"tampered")
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", result.stdout.lower())
        self.assertFalse((self.dest / self.recipe["manifest_path"]).exists())

    def test_rejects_symlinked_blob_and_parent_directory(self):
        self.dest.mkdir()
        (self.dest / "blobs").symlink_to(self.source / "blobs", target_is_directory=True)
        self.assertNotEqual(self.run_play().returncode, 0)
        (self.dest / "blobs").unlink()
        (self.dest / "blobs").mkdir()
        (self.dest / self.recipe["files"][0]["path"]).symlink_to(self.source / self.recipe["files"][0]["path"])
        self.assertNotEqual(self.run_play().returncode, 0)

    def test_rejects_symlinked_manifest(self):
        self.assert_ok(self.run_play())
        installed = self.dest / self.recipe["manifest_path"]
        installed.unlink()
        installed.symlink_to(self.manifest)
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refuse a symlinked or nonregular manifest", result.stdout)

    def test_pc_role_refuses_before_any_service_command_without_restart_authority(self):
        play = [{"hosts": "localhost", "gather_facts": False, "tasks": [
            {"ansible.builtin.include_role": {"name": str(ROOT / "roles/ov_pc_coexistence")}}
        ]}]
        (self.work / "play.yml").write_text(yaml.safe_dump(play))
        result = self.run_play()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Require the exact host and explicit restart authority", result.stdout)
        self.assertNotIn("Read the existing system service custody", result.stdout)

    def test_rejects_tampered_manifest(self):
        self.manifest.write_text('{"fixture":false}')
        self.assertNotEqual(self.run_play().returncode, 0)
        self.assertFalse((self.dest / self.recipe["manifest_path"]).exists())


@unittest.skipUnless(os.environ.get("OPENVIKING_NATIVE_TEST") == "1", "real OV checks run in the pinned native job")
class NativeOV(Protocol):
    """Use real SDK, schema, native ordered switchers and error classification."""

    def test_actual_embedding_failover_and_failback_sync_and_async(self):
        from openviking_cli.utils.config.embedding_config import EmbeddingConfig
        conf = json.loads(json.dumps(CONFIG["embedding"]))
        for cred in conf["dense"]["credentials"]:
            cred["api_base"] = self.base + "/" + cred["id"] + "/v1"
        conf["dense"]["failback_request_count"] = 1
        config = EmbeddingConfig.model_validate(conf)
        embedder = config.get_embedder()
        # Primary connection refusal must advance via an actual HTTP 503 from the adapter.
        adapter.UPSTREAMS["pc"] = ("127.0.0.1", 1, 1)
        self.assertEqual(len(embedder.embed("query", is_query=True).dense_vector), 1024)
        self.assertEqual(self.upstream.calls[-1][1]["input"], adapter.PREFIX + "query")
        adapter.UPSTREAMS["pc"] = adapter.UPSTREAMS["cpu"]
        self.assertEqual(len(asyncio.run(embedder.embed_async("recovered")).dense_vector), 1024)
        # 400 input-too-large is request-specific and must never try the fallback.
        self.upstream.calls.clear()
        self.upstream.reply = (400, {"error": "input length exceeds context length"})
        with self.assertRaises(Exception):
            embedder.embed("oversize")
        self.assertEqual(len(self.upstream.calls), 1)
        self.upstream.calls.clear()
        with self.assertRaises(Exception):
            embedder.embed("a" * 300000)
        self.assertEqual(self.upstream.calls, [])

    def test_actual_vlm_ordered_failover_and_timeout_schema_boundary(self):
        from importlib.metadata import version
        from openviking_cli.utils.config.vlm_config import VLMConfig
        self.assertEqual(version("openviking"), "0.5.2")
        conf = json.loads(json.dumps(CONFIG["vlm"]))
        conf["timeout"] = 1
        conf["failback_request_count"] = 1
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        primary_port = unused.getsockname()[1]
        unused.close()
        conf["credentials"][0]["api_base"] = f"http://127.0.0.1:{primary_port}/v1"
        conf["credentials"][1]["api_base"] = f"http://127.0.0.1:{self.upstream.server_port}/v1"
        self.upstream.reply = (200, {"id": "fixture", "object": "chat.completion", "created": 1,
            "model": "ov-gen-4k", "choices": [{"index": 0, "message": {"role": "assistant", "content": "summary"},
                                             "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2510, "completion_tokens": 20, "total_tokens": 2530}})
        config = VLMConfig.model_validate(conf)
        client = config.get_vlm_instance()
        self.assertEqual(client.get_completion("summarize"), "summary")
        fallback_call = self.upstream.calls[-1][1]
        self.assertEqual(fallback_call["model"], "ov-gen-4k")
        self.assertEqual(fallback_call["max_tokens"], 512)
        # Recovery goes back to primary via the native request-count policy.
        self.primary = ThreadingHTTPServer(("127.0.0.1", primary_port), Runtime)
        for key in ("reply", "entered", "release", "delay"):
            setattr(self.primary, key, getattr(self.upstream, key))
        self.primary.calls = []
        start(self.primary)
        # One fallback request increments the native counter; the next probes the primary.
        for _ in range(2):
            self.assertEqual(asyncio.run(client.get_completion_async("again")), "summary")
        self.assertTrue(self.primary.calls)
        self.assertEqual(self.primary.calls[-1][1]["model"], "local:latest")
        self.assertEqual(self.primary.calls[-1][1]["reasoning_effort"], "none")
        self.assertEqual(self.primary.calls[-1][1]["presence_penalty"], 0)
        self.assertNotIn("timeout", config.credentials[0].model_fields)
        self.assertIsInstance(VLMConfig.model_fields["timeout"].default, float)
        client.close()


if __name__ == "__main__":
    unittest.main()
