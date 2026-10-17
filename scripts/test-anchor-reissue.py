#!/usr/bin/python3
"""No real key generation, SSH or service actions; OpenSSL and systemctl are stubbed."""
import base64
from contextlib import redirect_stdout
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("reissue", str(ROOT / "scripts/chaldea-anchor-reissue"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
r = importlib.util.module_from_spec(spec)
loader.exec_module(r)


class ReissueTests(unittest.TestCase):
    def setUp(self):
        home = Path(os.environ["HOME"]).resolve()
        self.assertEqual(home, Path(os.environ["ROLLOUT_TEST_HOME"]).resolve())
        self.assertIn(".scratch", str(home))
        self.temp = tempfile.TemporaryDirectory(dir=home)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("keys", "state"):
            (self.root / name).mkdir(mode=0o700)
        self.kid = "deployment-2026-10c"

    def identity(self):
        return mock.patch.object(r.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_name="chaldea-publisher"))

    def test_existing_key_or_symlink_is_never_overwritten(self):
        path = self.root / "keys" / (self.kid + ".pem")
        path.write_text("existing metadata")
        with self.identity(), mock.patch.object(r.subprocess, "run") as run:
            with self.assertRaises(FileExistsError):
                r.generate(self.root, self.kid, "deployment-2026-10b")
            self.assertEqual(path.read_text(), "existing metadata")
            path.unlink()
            path.symlink_to(self.root / "absent")
            with self.assertRaises(FileExistsError):
                r.generate(self.root, self.kid, "deployment-2026-10b")
            run.assert_not_called()

    def test_output_contains_only_public_pins_and_key_mode_is_private(self):
        config = self.root / "config.json"
        config.write_text(json.dumps({"root": str(self.root), "kid": "deployment-2026-10b"}))
        marker = b"metadata fixture, NOT a private key"
        der = r.SPKI + bytes(range(32))
        def openssl(args, **kwargs):
            self.assertEqual(args[0], "/usr/bin/openssl")
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            if args[1] == "genpkey":
                os.write(kwargs["stdout"], marker)
                return types.SimpleNamespace(returncode=0)
            self.assertIn("-pubout", args)
            return types.SimpleNamespace(stdout=der, returncode=0)
        output = io.StringIO()
        with self.identity(), mock.patch.object(r.subprocess, "run", side_effect=openssl):
            with mock.patch("sys.argv", ["helper", "--config", str(config), "generate", "--kid", self.kid]):
                with redirect_stdout(output):
                    self.assertEqual(r.main(), 0)
        pins = json.loads(output.getvalue())
        self.assertEqual(set(pins), {"chaldea_deployment_anchor_kid",
                         "chaldea_deployment_anchor_public_jwk", "chaldea_deployment_anchor_spki_sha256"})
        self.assertEqual(pins["chaldea_deployment_anchor_public_jwk"]["x"],
                         base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("="))
        self.assertEqual(pins["chaldea_deployment_anchor_spki_sha256"], hashlib.sha256(der).hexdigest())
        self.assertNotIn(marker.decode(), output.getvalue())
        self.assertEqual((self.root / "keys" / (self.kid + ".pem")).stat().st_mode & 0o777, 0o600)

    def test_generate_requires_new_valid_kid_and_dedicated_identity(self):
        with self.identity(), mock.patch.object(r.subprocess, "run") as run:
            for kid in ("deployment-2026-10b", "deployment-2026-13c", "../outside"):
                with self.assertRaises(RuntimeError):
                    r.generate(self.root, kid, "deployment-2026-10b")
            run.assert_not_called()
        with mock.patch.object(r.pwd, "getpwuid", return_value=types.SimpleNamespace(pw_name="root")):
            with self.assertRaises(RuntimeError):
                r.generate(self.root, self.kid, "deployment-2026-10b")

    def test_emergency_unit_retains_the_normal_sandbox(self):
        from jinja2 import Environment, FileSystemLoader, StrictUndefined
        env = Environment(loader=FileSystemLoader(ROOT / "roles/chaldea_publisher/templates"),
                          undefined=StrictUndefined)
        variables = {"chaldea_publisher_root": str(self.root), "chaldea_publisher_config_root": "/etc/fixture",
                     "chaldea_publisher_program": "/usr/local/libexec/chaldea-publisher"}
        normal = env.get_template("chaldea-publisher.service.j2").render(variables)
        emergency = env.get_template("chaldea-publisher-emergency.service.j2").render(variables)
        self.assertNotIn("--emergency-empty", normal)
        self.assertIn("--emergency-empty", emergency)
        self.assertEqual(normal.strip(), emergency.replace(" --emergency-empty", "").strip())
        self.assertIn("User=chaldea-publisher", emergency)
        self.assertIn("ProtectSystem=strict", emergency)

    def test_high_water_is_read_only_and_refuses_missing_or_exhausted_state(self):
        import sqlite3
        path = self.root / "state/receipt.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            r.high_water(path)
        self.assertFalse(path.exists())
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE access_meta (name TEXT PRIMARY KEY, value TEXT)")
            db.execute("INSERT INTO access_meta VALUES ('format', 'chaldea-access-state-v1')")
            db.execute("INSERT INTO access_meta VALUES ('trust', ?)", (json.dumps({"sequence": 92}),))
        self.assertEqual(r.high_water(path), 92)
        with sqlite3.connect(path) as db:
            db.execute("UPDATE access_meta SET value=? WHERE name='trust'", (json.dumps({"sequence": r.MAX}),))
        with self.assertRaises(RuntimeError):
            r.high_water(path)

    def test_resume_requires_deployed_kid_and_measured_floor(self):
        config = {"kid": self.kid, "sequence_floor": 92}
        with mock.patch.object(r.os, "geteuid", return_value=0), mock.patch.object(r.subprocess, "run") as run:
            for kid, floor in (("deployment-2026-10b", 92), (self.kid, 93)):
                with self.assertRaises(RuntimeError):
                    r.resume(config, kid, floor, False)
            run.assert_not_called()
            r.resume(config, self.kid, 92, False)
            self.assertEqual(run.call_args.args[0], ["/usr/bin/systemctl", "restart", "chaldea-publisher.service"])
            r.resume(config, self.kid, 92, True)
            self.assertEqual(run.call_args.args[0][-1], "chaldea-publisher-emergency.service")


if __name__ == "__main__":
    unittest.main()
