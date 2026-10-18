#!/usr/bin/env python3
"""Focused drift/fetch contracts using tiny synthetic data, no copied role fixtures."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "installer_role_drift", Path(__file__).with_name("check-installer-role-drift.py"))
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class InstallerRoleDriftTest(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        home = Path(os.environ["HOME"]).resolve()
        self.assertEqual(home, scratch / ".scratch-home")
        self.assertNotEqual(home, Path("/home/igor"))
        self.directory = tempfile.TemporaryDirectory(dir=home)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.reference = {
            check.HANDLER: check.FLEET_HANDLER,
            "roles/chaldea_host_artifact/tasks/main.yml": b"synthetic artifact task\n",
        }
        for path, content in self.reference.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(check.INFRA_HANDLER if path == check.HANDLER else content)

    def test_approved_difference_passes(self):
        check.compare(self.root, self.reference)

    def test_unexpected_edits_missing_and_extra_files_fail(self):
        target = self.root / "roles/chaldea_host_artifact/tasks/main.yml"
        original = target.read_bytes()
        target.write_bytes(original + b"# unexpected mutation\n")
        with self.assertRaisesRegex(ValueError, "unexpected installer role content"):
            check.compare(self.root, self.reference)
        target.unlink()
        with self.assertRaisesRegex(ValueError, "missing="):
            check.compare(self.root, self.reference)
        target.write_bytes(original)
        extra = target.with_name("extra.yml")
        extra.write_bytes(b"unexpected extra file\n")
        with self.assertRaisesRegex(ValueError, "extra="):
            check.compare(self.root, self.reference)

    def test_handler_exception_is_exact_on_both_sides(self):
        handler = self.root / check.HANDLER
        for data in (check.FLEET_HANDLER, check.INFRA_HANDLER + b"# drift\n",
                     check.INFRA_HANDLER.replace(b"daemon_reload: true", b"daemon_reload: false")):
            handler.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "unexpected installer role content"):
                check.compare(self.root, self.reference)
        handler.write_bytes(check.INFRA_HANDLER)
        self.reference[check.HANDLER] += b"# upstream drift\n"
        with self.assertRaisesRegex(ValueError, "reference handler changed"):
            check.compare(self.root, self.reference)

    def test_symlink_fails(self):
        path = self.root / "roles/chaldea_host_artifact/tasks/main.yml"
        path.unlink()
        path.symlink_to(self.root / check.HANDLER)
        with self.assertRaisesRegex(ValueError, "non-regular local path"):
            check.compare(self.root, self.reference)

    def test_fetch_exact_commit_and_fail_closed(self):
        # A real Git exchange exercises pin resolution without network or a fixture mirror.
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(self.root), *args], check=True,
                capture_output=True).stdout.decode().strip()
        git("init", "--quiet")
        git("add", "roles")
        git("-c", "user.name=Drift test", "-c", "user.email=drift@example.invalid",
            "commit", "--quiet", "-m", "synthetic reference")
        pin = git("rev-parse", "HEAD")
        fetched = check.fetch_reference(pin, str(self.root))
        self.assertEqual(fetched[check.HANDLER], check.INFRA_HANDLER)
        self.assertEqual(set(fetched), set(self.reference))
        for invalid in ("main", "a" * 39, "0" * 40, pin.upper()):
            with self.assertRaisesRegex(ValueError, "full nonzero lowercase commit SHA"):
                check.fetch_reference(invalid, str(self.root))
        with self.assertRaises(subprocess.CalledProcessError):
            check.fetch_reference("f" * 40, str(self.root))
        with self.assertRaises(subprocess.CalledProcessError):
            check.fetch_reference(pin, str(self.root / "missing-repository"))
        git("rm", "-r", "--quiet", "roles/chaldea_host_artifact")
        git("-c", "user.name=Drift test", "-c", "user.email=drift@example.invalid",
            "commit", "--quiet", "-m", "missing role")
        with self.assertRaisesRegex(ValueError, "both installer roles"):
            check.fetch_reference(git("rev-parse", "HEAD"), str(self.root))


if __name__ == "__main__":
    unittest.main()
