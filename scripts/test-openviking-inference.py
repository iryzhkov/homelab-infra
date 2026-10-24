#!/usr/bin/env python3
"""Tests for OpenViking's self-hosted embedding service.

Checks the invariants the deployment depends on, across the files that must
agree: stacks/openviking/stack.yml (the service), embeddings-prepare.yml (the
model and configuration it installs), the graph and model-list templates,
and inventory/group_vars/swarm/openviking-inference.yml (the pin and the
qualified bounds). The last test runs embeddings-prepare.yml itself with
ansible-playbook against a scratch directory and a file:// fixture model, to
show it installs exactly the pinned files, is idempotent, and refuses a
checksum mismatch or an unlisted file.

    OPENVIKING_INFERENCE_TEST_HOME=$HOME python3 scripts/test-openviking-inference.py -v

Needs PyYAML, Jinja2 and (for the playbook test) ansible-core 2.19, and a
scratch HOME with TMPDIR inside it: the playbook test writes only there.
"""

import getpass
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import jinja2
import yaml

REPO = Path(__file__).resolve().parents[1]
HOME = os.environ.get("OPENVIKING_INFERENCE_TEST_HOME", "")
assert HOME and os.environ.get("HOME") == HOME, "set OPENVIKING_INFERENCE_TEST_HOME to the scratch HOME"
assert Path(tempfile.gettempdir()).resolve().is_relative_to(Path(HOME).resolve()), "TMPDIR must be inside HOME"

class AnsibleLoader(yaml.SafeLoader):
    """SafeLoader that reads Ansible's !unsafe strings as plain strings."""


AnsibleLoader.add_constructor("!unsafe", lambda loader, node: loader.construct_scalar(node))

STACK_DIR = REPO / "stacks/openviking"
STACK = yaml.safe_load((STACK_DIR / "stack.yml").read_text())
PREPARE = yaml.safe_load((STACK_DIR / "embeddings-prepare.yml").read_text())
VARS = yaml.safe_load((REPO / "inventory/group_vars/swarm/openviking-inference.yml").read_text())
EMB = VARS["openviking_embeddings"]
SERVICE = STACK["services"]["embeddings"]

# The 2026-10-24 qualification ran the service under these caps; a larger
# limit is an unqualified configuration.
QUALIFIED_MEMORY_BYTES = 2 * 1024 ** 3
QUALIFIED_CPUS = 2
# The longest single input that stayed under that memory cap (6000 tokens
# peaked at 1.75 GB; 8000 was OOM-killed).
QUALIFIED_MAX_TOKENS = 6144


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def memory_bytes(value):
    match = re.fullmatch(r"(\d+)([KMG]?)", str(value))
    assert match, value
    return int(match.group(1)) * {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}[match.group(2)]


def render(template, variables):
    """Render a stack template with the few Ansible filters it uses."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    env.filters["to_json"] = json.dumps
    env.filters["to_nice_json"] = lambda value, indent=4: json.dumps(value, indent=indent, sort_keys=True)
    env.filters["bool"] = lambda value: value if isinstance(value, bool) else str(value).lower() in ("true", "yes", "1")
    return env.from_string((STACK_DIR / "templates" / template).read_text()).render(**variables)


def mount(target):
    for volume in SERVICE["volumes"]:
        if volume["target"] == target:
            return volume
    raise AssertionError(f"no mount at {target}")


class StackFile(unittest.TestCase):
    def test_stack_passes_the_swarm_lint(self):
        lint = load("stack_lint", "scripts/stack-lint.py")
        errors, _ = lint.lint(str(STACK_DIR / "stack.yml"))
        self.assertEqual(errors, [])

    def test_images_are_pinned_by_digest(self):
        for name, service in STACK["services"].items():
            self.assertRegex(service["image"], r"@sha256:[0-9a-f]{64}$", name)

    def test_embeddings_publish_nothing_and_share_the_stack_overlay(self):
        self.assertNotIn("ports", SERVICE)
        self.assertEqual(SERVICE.get("networks"), STACK["services"]["openviking"].get("networks"))

    def test_embeddings_cannot_write_or_reach_devices(self):
        self.assertIs(SERVICE.get("read_only"), True)
        for key in ("devices", "privileged", "cap_add"):
            self.assertNotIn(key, SERVICE)
        for volume in SERVICE["volumes"]:
            if volume["type"] == "bind":
                self.assertIs(volume.get("read_only"), True, volume["target"])

    def test_runs_on_the_node_that_holds_the_model(self):
        self.assertEqual(SERVICE["deploy"]["placement"]["constraints"],
                         [f"node.labels.host == {EMB['host']}"])

    def test_resources_stay_within_the_qualified_bounds(self):
        resources = SERVICE["deploy"]["resources"]
        limit = memory_bytes(resources["limits"]["memory"])
        self.assertLessEqual(limit, QUALIFIED_MEMORY_BYTES)
        self.assertLessEqual(memory_bytes(resources["reservations"]["memory"]), limit)
        self.assertEqual(float(resources["limits"]["cpus"]), QUALIFIED_CPUS)
        self.assertLessEqual(int(resources["limits"]["pids"]), 128)
        # One inference stream on as many threads as the CPU limit allows.
        self.assertEqual(EMB["graph"]["threads"], QUALIFIED_CPUS)
        self.assertEqual(SERVICE["environment"]["OMP_NUM_THREADS"], str(QUALIFIED_CPUS))
        self.assertEqual(EMB["graph"]["num_streams"], 1)
        self.assertEqual(SERVICE["deploy"]["update_config"]["order"], "stop-first")

    def test_health_check_asks_for_readiness_not_an_inference(self):
        test = " ".join(SERVICE["healthcheck"]["test"])
        self.assertNotRegex(test, r"/v[13]/embeddings")
        self.assertIn(f"/v2/models/{EMB['served_name']}/ready", test)

    def test_every_stack_variable_comes_from_the_prepare_step(self):
        text = (STACK_DIR / "stack.yml").read_text()
        needed = set(re.findall(r"\$\{(OPENVIKING_EMBEDDINGS_[A-Z0-9_]+)", text))
        provided = set()
        for task in PREPARE:
            env = (task.get("ansible.builtin.set_fact") or {}).get("swarm_stack_env", "")
            provided |= set(re.findall(r"'(OPENVIKING_EMBEDDINGS_[A-Z0-9_]+)'", env))
        self.assertTrue(needed)
        self.assertEqual(needed, provided)

    def test_swarm_prepare_includes_the_embedding_step_outside_the_cutover(self):
        steps = yaml.load((STACK_DIR / "swarm-prepare.yml").read_text(), Loader=AnsibleLoader)
        include = [s for s in steps if "embeddings-prepare.yml" in str(s.get("ansible.builtin.include_tasks", ""))]
        self.assertEqual(len(include), 1)
        self.assertNotIn("when", include[0])


class ModelPin(unittest.TestCase):
    def test_revision_and_source_are_fixed(self):
        self.assertRegex(EMB["model"]["revision"], r"^[0-9a-f]{40}$")
        source = EMB["model"]["source"]
        self.assertIn(f"/resolve/{EMB['model']['revision']}", source)
        self.assertIn(EMB["model"]["repository"], source)

    def test_every_file_has_a_checksum_and_a_size(self):
        paths = [f["path"] for f in EMB["model"]["files"]]
        self.assertEqual(len(paths), len(set(paths)))
        for entry in EMB["model"]["files"]:
            self.assertRegex(entry["sha256"], r"^[0-9a-f]{64}$", entry["path"])
            self.assertGreater(entry["size"], 0, entry["path"])
            self.assertFalse(entry["path"].startswith("/") or ".." in entry["path"].split("/"))

    def test_the_files_ovms_loads_are_pinned(self):
        paths = {f["path"] for f in EMB["model"]["files"]}
        for required in ("config.json", "openvino_model.xml", "openvino_model.bin",
                         "openvino_tokenizer.xml", "openvino_tokenizer.bin"):
            self.assertIn(required, paths)

    def test_state_directory_is_not_the_openviking_state(self):
        # swarm-move replaces /srv/swarm/openviking with --delete.
        self.assertNotEqual(EMB["state_dir"], "openviking")
        self.assertNotIn("/", EMB["state_dir"])


class RenderedConfiguration(unittest.TestCase):
    def setUp(self):
        self.graph = render("embeddings-graph.pbtxt.j2", {"openviking_embeddings": EMB})
        self.models = json.loads(render("embeddings-config.json.j2", {"openviking_embeddings": EMB}))

    def option(self, name):
        match = re.search(rf"^\s+{name}: (.+?),?$", self.graph, re.M)
        self.assertIsNotNone(match, name)
        return match.group(1)

    def test_long_input_fails_instead_of_being_truncated(self):
        self.assertEqual(self.option("truncate"), "false")
        self.assertEqual(int(self.option("max_length")), EMB["graph"]["max_length"])
        self.assertLessEqual(EMB["graph"]["max_length"], QUALIFIED_MAX_TOKENS)

    def test_pooling_and_normalisation_match_the_model(self):
        self.assertEqual(self.option("pooling"), "LAST")
        self.assertEqual(self.option("normalize_embeddings"), "true")
        self.assertEqual(self.option("target_device"), '"CPU"')

    def test_plugin_threads_follow_the_cpu_limit(self):
        plugin = json.loads(self.option("plugin_config").strip("'"))
        self.assertEqual(plugin, {"NUM_STREAMS": "1", "INFERENCE_NUM_THREADS": str(QUALIFIED_CPUS)})

    def test_paths_inside_the_container_line_up(self):
        self.assertEqual(self.option("models_path"), '"' + mount("/model")["target"] + '"')
        config = self.models["model_config_list"][0]["config"]
        self.assertEqual(config["name"], EMB["served_name"])
        self.assertEqual(config["base_path"], f"{mount('/workspace')['target']}/{EMB['served_name']}")
        command = SERVICE["command"]
        self.assertEqual(command[command.index("--config_path") + 1], "/workspace/config.json")


class PreparePlaybook(unittest.TestCase):
    """Run embeddings-prepare.yml unprivileged against a scratch state root."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("ansible-playbook"):
            raise unittest.SkipTest("ansible-playbook is not installed")

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="ov-inference-"))
        self.assertTrue(self.work.resolve().is_relative_to(Path(HOME).resolve()))
        self.source = self.work / "source"
        files = {"config.json": b'{"model_type": "fixture"}\n', "sub/weights.bin": b"\x00\x01fixture"}
        self.files = []
        for path, data in files.items():
            (self.source / path).parent.mkdir(parents=True, exist_ok=True)
            (self.source / path).write_bytes(data)
            self.files.append({"path": path, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        self.state = self.work / "srv"
        self.result = self.work / "env.json"

    def tearDown(self):
        for path in self.work.rglob("*"):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() else 0o644)
        shutil.rmtree(self.work)

    def run_prepare(self):
        pin = dict(EMB, model=dict(EMB["model"], source=f"file://{self.source}", files=self.files,
                                   revision="0" * 40))
        (self.work / "vars.json").write_text(json.dumps({
            "openviking_embeddings": pin,
            "openviking_embeddings_test_owner": getpass.getuser(),
            "swarm_state_root": str(self.state),
            "swarm_stack": {"name": "openviking"},
            "swarm_stack_src": str(STACK_DIR),
            "result_path": str(self.result),
        }))
        (self.work / "inventory.yml").write_text(yaml.safe_dump({"all": {"children": {"swarm_workers": {
            "hosts": {EMB["host"]: {"ansible_connection": "local",
                                    "ansible_python_interpreter": shutil.which("python3")}}}}}}))
        (self.work / "play.yml").write_text(yaml.safe_dump([{
            "hosts": EMB["host"], "gather_facts": False,
            # A play variable, as roles/swarm/tasks/stack.yml's fact is: an
            # extra variable would outrank the prepare step's set_fact.
            "vars": {"swarm_stack_env": {"STACK_NAME": "openviking"}},
            "tasks": [
                {"ansible.builtin.include_tasks": str(STACK_DIR / "embeddings-prepare.yml")},
                {"ansible.builtin.copy": {"content": "{{ swarm_stack_env | to_json }}", "dest": "{{ result_path }}"}},
            ]}]))
        env = dict(os.environ, ANSIBLE_LOCAL_TEMP=str(self.work / "ansible-tmp"),
                   ANSIBLE_REMOTE_TEMP=str(self.work / "ansible-tmp"), ANSIBLE_NOCOLOR="1",
                   ANSIBLE_RETRY_FILES_ENABLED="0", ANSIBLE_CONFIG=str(self.work / "none.cfg"))
        return subprocess.run(
            ["ansible-playbook", "-i", str(self.work / "inventory.yml"), str(self.work / "play.yml"),
             "-e", f"@{self.work / 'vars.json'}", "-e", "ansible_become=false"],
            cwd=self.work, env=env, capture_output=True, text=True, timeout=300)

    def model_dir(self):
        return self.state / EMB["state_dir"] / "models" / EMB["model"]["repository"] / ("0" * 40)

    def assertRan(self, result):
        self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-2000:])

    def test_installs_exactly_the_pin_and_is_idempotent(self):
        first = self.run_prepare()
        self.assertRan(first)
        for entry in self.files:
            installed = self.model_dir() / entry["path"]
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), entry["sha256"])
            self.assertEqual(installed.stat().st_mode & 0o777, 0o444)
        config = self.state / EMB["state_dir"] / "config"
        graph = (config / EMB["served_name"] / "graph.pbtxt").read_text()
        self.assertEqual(graph, render("embeddings-graph.pbtxt.j2", {"openviking_embeddings": dict(
            EMB, model=dict(EMB["model"], revision="0" * 40))}))
        json.loads((config / "config.json").read_text())
        env = json.loads(self.result.read_text())
        self.assertEqual(env["OPENVIKING_EMBEDDINGS_MODEL_DIR"], str(self.model_dir()))
        self.assertEqual(env["OPENVIKING_EMBEDDINGS_CONFIG_DIR"], str(config))
        self.assertRegex(env["OPENVIKING_EMBEDDINGS_CONFIG_SHA256"], r"^[0-9a-f]{64}$")
        self.assertEqual(env["STACK_NAME"], "openviking")

        second = self.run_prepare()
        self.assertRan(second)
        self.assertRegex(second.stdout, r"changed=0\s")
        self.assertEqual(json.loads(self.result.read_text()), env)

    def test_refuses_a_source_that_does_not_match_the_checksum(self):
        (self.source / "config.json").write_bytes(b"tampered\n")
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum", (result.stdout + result.stderr).lower())
        self.assertFalse(self.result.exists())

    def test_refuses_a_file_the_pin_does_not_list(self):
        self.assertRan(self.run_prepare())
        self.result.unlink()
        stray = self.model_dir() / "extra.bin"
        stray.write_bytes(b"stray")
        result = self.run_prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("extra.bin: not part of the pinned revision", result.stdout)
        self.assertFalse(self.result.exists())

    def test_replaces_a_damaged_file_from_the_pinned_source(self):
        self.assertRan(self.run_prepare())
        damaged = self.model_dir() / "sub/weights.bin"
        damaged.chmod(0o644)
        damaged.write_bytes(b"damaged")
        self.assertRan(self.run_prepare())
        self.assertEqual(hashlib.sha256(damaged.read_bytes()).hexdigest(), self.files[1]["sha256"])


if __name__ == "__main__":
    unittest.main()
