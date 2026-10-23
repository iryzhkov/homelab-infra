#!/usr/bin/env python3
"""Opt-in Jocasta HTTP MCP settings: all-or-none, canonical, gated, and off by default.

Runs the filter directly, then the actual prepare.yml validation and .env tasks
through ansible-playbook on localhost, writing only under a scratch HOME.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

REPO = Path(__file__).resolve().parents[1]
HOME = os.environ.get("JOCASTA_MCP_TEST_HOME", "")
assert HOME and os.environ.get("HOME") == HOME, "set JOCASTA_MCP_TEST_HOME to the scratch HOME"
assert Path(tempfile.gettempdir()).resolve().is_relative_to(Path(HOME).resolve()), "TMPDIR must be inside HOME"

spec = importlib.util.spec_from_file_location("jocasta_filter", REPO / "filter_plugins/jocasta.py")
assert spec and spec.loader
jocasta = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jocasta)

REVISION = "0123456789abcdef0123456789abcdef01234567"
OLD_PIN = "git.ryzhkov.dev/igor/jocasta:37ce657515092be80c5d49b4a3b30f482d86ed63"
IMAGE = "git.ryzhkov.dev/igor/jocasta:" + REVISION
ROUTES = ["jocasta.ryzhkov.dev"]
MCP = {
    "JOCASTA_OIDC_ISSUER": "https://auth.ryzhkov.dev/application/o/chaldea-jocasta/",
    "JOCASTA_OIDC_AUDIENCE": "jocasta-rest-client",
    "JOCASTA_OIDC_JWKS_URL": "https://auth.ryzhkov.dev/application/o/chaldea-jocasta/jwks/",
    "JOCASTA_MCP_RESOURCE_URL": "https://jocasta.ryzhkov.dev/mcp",
}
REFUSED = jocasta.AnsibleFilterError


def enabled(**changes):
    return {"JOCASTA_IMAGE": IMAGE, **MCP, **changes}


class FilterTest(unittest.TestCase):
    def setUp(self):
        accepted = jocasta.MCP_IMAGE_REVISIONS
        setattr(jocasta, "MCP_IMAGE_REVISIONS", frozenset({REVISION}))
        self.addCleanup(setattr, jocasta, "MCP_IMAGE_REVISIONS", accepted)

    def test_off_by_default_and_accepted_settings(self):
        self.assertEqual(jocasta.jocasta_mcp_env({"JOCASTA_IMAGE": OLD_PIN}, ROUTES), {})
        self.assertEqual(jocasta.jocasta_mcp_env(enabled(), ROUTES), MCP)
        digest = IMAGE + "@sha256:" + "a" * 64
        self.assertEqual(jocasta.jocasta_mcp_env(enabled(JOCASTA_IMAGE=digest), ROUTES), MCP)
        self.assertIn("jocasta_mcp_env", jocasta.FilterModule().filters())

    def test_all_or_none_and_no_unknown_keys(self):
        for omitted in MCP:
            partial = enabled()
            del partial[omitted]
            with self.subTest(omitted=omitted), self.assertRaisesRegex(REFUSED, "or none"):
                jocasta.jocasta_mcp_env(partial, ROUTES)
        for key in MCP:
            with self.subTest(empty=key), self.assertRaises(REFUSED):
                jocasta.jocasta_mcp_env(enabled(**{key: ""}), ROUTES)
        with self.assertRaisesRegex(REFUSED, "unknown"):
            jocasta.jocasta_mcp_env({"JOCASTA_IMAGE": IMAGE, "JOCASTA_OIDC_JWKS": "x"}, ROUTES)

    def test_noncanonical_values_are_refused(self):
        bad = {
            "JOCASTA_MCP_RESOURCE_URL": [
                "http://jocasta.ryzhkov.dev/mcp", "https://Jocasta.ryzhkov.dev/mcp",
                "https://jocasta.ryzhkov.dev:443/mcp", "https://jocasta.ryzhkov.dev:8443/mcp",
                "https://jocasta.ryzhkov.dev/mcp/", "https://jocasta.ryzhkov.dev/MCP",
                "https://jocasta.ryzhkov.dev/mcp?x=1", "https://jocasta.ryzhkov.dev/mcp#x",
                "https://u@jocasta.ryzhkov.dev/mcp", "https://jocasta.ryzhkov.dev./mcp",
                "https://jocasta.ryzhkov.dev/%6dcp", "https://jocasta.ryzhkov.dev//mcp",
                "https://other.ryzhkov.dev/mcp", " https://jocasta.ryzhkov.dev/mcp", 7,
            ],
            "JOCASTA_OIDC_ISSUER": [
                "https://auth.ryzhkov.dev/application/o/chaldea-jocasta",
                "http://auth.ryzhkov.dev/application/o/chaldea-jocasta/",
                "https://auth.ryzhkov.dev:443/application/o/chaldea-jocasta/",
                "https://auth.ryzhkov.dev/application/o/chaldea-jocasta/?a=b",
                "https://auth.ryzhkov.dev/application/o/../chaldea-jocasta/",
                "https://auth.ryzhkov.dev/application/o/chaldea%2djocasta/",
            ],
            "JOCASTA_OIDC_JWKS_URL": [
                "https://auth.ryzhkov.dev", "https://auth.ryzhkov.dev/",
                "https://keys.ryzhkov.dev/application/o/chaldea-jocasta/jwks/",
                "https://auth.ryzhkov.dev/application/o/chaldea-jocasta/jwks/#k",
                "https://auth.ryzhkov.dev/application/o/./jwks/",
            ],
            "JOCASTA_OIDC_AUDIENCE": ["a b", "a$b", "a#b", "'a'", "a" * 257, ["a"]],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(REFUSED):
                    jocasta.jocasta_mcp_env(enabled(**{key: value}), ROUTES)

    def test_only_an_accepted_mcp_image_can_enable(self):
        for image in (OLD_PIN, IMAGE.rsplit(":", 1)[0] + ":latest",
                      "git.ryzhkov.dev/igor/jocasta@sha256:" + "a" * 64,
                      "git.ryzhkov.dev/igor/other:" + REVISION):
            with self.subTest(image=image), self.assertRaisesRegex(REFUSED, "accepted"):
                jocasta.jocasta_mcp_env(enabled(JOCASTA_IMAGE=image), ROUTES)
        setattr(jocasta, "MCP_IMAGE_REVISIONS", frozenset())
        with self.assertRaisesRegex(REFUSED, "accepted"):
            jocasta.jocasta_mcp_env(enabled(), ROUTES)

    def test_source_accepts_no_image_yet(self):
        source = (REPO / "filter_plugins/jocasta.py").read_text()
        self.assertEqual(source.count("\nMCP_IMAGE_REVISIONS = frozenset()\n"), 1)


class StackTest(unittest.TestCase):
    def test_compose_passes_only_the_four_optional_variables(self):
        compose = yaml.safe_load((REPO / "stacks/jocasta/docker-compose.yml").read_text())
        environment = compose["services"]["jocasta"]["environment"]
        self.assertEqual(environment, {
            "JOCASTA_LISTEN": ":8080",
            "JOCASTA_DB": "/data/jocasta.db",
            "JOCASTA_TOKENS_FILE": "/run/secrets/jocasta-tokens.json",
            "JOCASTA_MAX_STORAGE_BYTES": "10737418240",
            "JOCASTA_MIN_FREE_BYTES": "5368709120",
            **{key: "${%s:-}" % key for key in jocasta.MCP_KEYS},
        })

    def run_prepare(self, env_extra, accepted=()):
        """Run the actual image, MCP and .env tasks of prepare.yml; return the .env."""
        tasks = yaml.safe_load((REPO / "stacks/jocasta/prepare.yml").read_text())
        names = ("Require an immutable Jocasta image reference",
                 "Validate opt-in HTTP MCP settings before writes",
                 "Render the non-secret Compose environment")
        selected = [task for task in tasks if task["name"] in names]
        self.assertEqual([task["name"] for task in selected], list(names))
        root = Path(tempfile.mkdtemp(prefix="jocasta-mcp-"))
        self.assertTrue(root.resolve().is_relative_to(Path(HOME).resolve()))
        filters = root / "filter_plugins"
        filters.mkdir()
        source = (REPO / "filter_plugins/jocasta.py").read_text()
        line = "\nMCP_IMAGE_REVISIONS = frozenset()\n"
        self.assertEqual(source.count(line), 1)
        (filters / "jocasta.py").write_text(
            source.replace(line, "\nMCP_IMAGE_REVISIONS = frozenset(%r)\n" % list(accepted)))
        (root / "stack").mkdir()
        (root / "ansible.cfg").write_text("[defaults]\nfilter_plugins = filter_plugins\n"
                                          "retry_files_enabled = False\n")
        play = [{"hosts": "localhost", "gather_facts": False, "tasks": selected, "vars": {
            "stack": {"name": "jocasta", "env_extra": env_extra,
                      "routes": [{"host": host} for host in ROUTES]},
            "mesh_host": "10.42.0.2", "stack_dir": str(root / "stack"),
            "ansible_python_interpreter": sys.executable}}]
        (root / "play.yml").write_text(json.dumps(play))
        env = dict(os.environ, ANSIBLE_CONFIG=str(root / "ansible.cfg"))
        result = subprocess.run(
            ["ansible-playbook", "-i", "localhost,", "-c", "local", "play.yml"],
            cwd=root, env=env, capture_output=True, text=True)
        dotenv = root / "stack/.env"
        return result, dotenv.read_text() if dotenv.exists() else None

    def test_off_renders_the_previous_env_exactly(self):
        result, dotenv = self.run_prepare({"JOCASTA_IMAGE": OLD_PIN})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(dotenv, "HOST_IP=10.42.0.2\nJOCASTA_IMAGE=%s\n" % OLD_PIN)

    def test_enabled_renders_all_four_values(self):
        result, dotenv = self.run_prepare(enabled(), accepted=[REVISION])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        expected = ["HOST_IP=10.42.0.2", "JOCASTA_IMAGE=" + IMAGE]
        expected += ["%s=%s" % item for item in sorted(MCP.items())]
        self.assertEqual(dotenv, "\n".join(expected) + "\n")

    def test_refusals_happen_before_any_write(self):
        partial = enabled()
        del partial["JOCASTA_OIDC_AUDIENCE"]
        cases = ((partial, [REVISION], "or none"),
                 (enabled(JOCASTA_IMAGE=OLD_PIN), [REVISION], "accepted"),
                 (enabled(), [], "accepted"))
        for env_extra, accepted, message in cases:
            with self.subTest(message=message, accepted=accepted):
                result, dotenv = self.run_prepare(env_extra, accepted)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stdout + result.stderr)
                self.assertIsNone(dotenv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
