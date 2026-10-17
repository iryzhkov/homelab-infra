#!/usr/bin/env python3
"""Real Git data fixtures; no SSH, private keys, runtime or host services."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader("trust_fetcher", str(
    ROOT / "roles/chaldea_access_trust/files/chaldea-access-trust-fetch"))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
fetcher = importlib.util.module_from_spec(spec)
loader.exec_module(fetcher)


class FetcherTest(unittest.TestCase):
    def setUp(self):
        scratch = Path(os.environ["TMPDIR"]).resolve()
        home = Path(os.environ["HOME"]).resolve()
        self.assertTrue(home.is_relative_to(scratch))
        self.assertNotEqual(home, Path("/home/igor"))
        self.temp = tempfile.TemporaryDirectory(dir=home)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.config = {"state": str(self.root), "node": "/synthetic/node",
                       "runtime": "/synthetic/runtime", "key": str(self.root / "key"),
                       "known_hosts": str(self.root / "known_hosts"),
                       "anchors": str(self.root / "anchors.json"),
                       "health": str(self.root / "clock.json"),
                       "output": str(self.root / "public.jws"),
                       "repository": fetcher.REMOTE, "bundle_path": fetcher.BUNDLE}
        for name in ("key", "known_hosts"):
            (self.root / name).write_text("metadata fixture only, never a key")
            (self.root / name).chmod(0o600)
        (self.root / "public.jws").write_text("previous")
        self.calls = []
        self.git_env = dict(os.environ, GIT_AUTHOR_NAME="fixture", GIT_COMMITTER_NAME="fixture",
                            GIT_AUTHOR_EMAIL="fixture@example.invalid",
                            GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.cache = self.root / "git"
        self.real_run = subprocess.run
        self.git("init", "--bare", str(self.cache))
        self.envelope = b"synthetic.envelope.fixture"
        self.install_fail = False
        self.commit()

    def git(self, *args, data=None):
        return self.real_run(["/usr/bin/git", "--git-dir=" + str(self.cache), *args],
                             input=data, env=self.git_env, check=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def commit(self, mode="100644", path="bundle.jws"):
        self.git("read-tree", "--empty")
        blob = self.git("hash-object", "-w", "--stdin", data=self.envelope).decode().strip()
        self.git("update-index", "--add", "--cacheinfo", mode + "," + blob + "," + path)
        tree = self.git("write-tree").decode().strip()
        self.head = self.git("commit-tree", tree, data=b"fixture\n").decode().strip()
        self.git("update-ref", fetcher.REF, self.head)

    def command(self, args, **kwargs):
        self.calls.append(args)
        if "fetch" in args:
            self.assertIn("ssh://git@github.com/iryzhkov/chaldea-trust.git", args)
            self.assertIn("+refs/heads/main:" + fetcher.REF, args)
            env = kwargs["env"]
            self.assertIn("IdentityAgent=none", env["GIT_SSH_COMMAND"])
            self.assertIn("-F /dev/null", env["GIT_SSH_COMMAND"])
            self.assertIn("StrictHostKeyChecking=yes", env["GIT_SSH_COMMAND"])
            return subprocess.CompletedProcess(args, 0, b"", b"")
        if args[0] == "/synthetic/node":
            if self.install_fail:
                raise subprocess.CalledProcessError(1, args)
            opts = dict(zip(args[3::2], args[4::2]))
            self.assertEqual(Path(opts["--bundle-file"]).read_bytes(), self.envelope)
            self.assertEqual(opts["--state-file"], str(self.root / "updater.sqlite"))
            Path(opts["--output"]).write_bytes(self.envelope)
            return subprocess.CompletedProcess(args, 0)
        self.assertEqual(args[0], "/usr/bin/git")
        self.assertNotIn("push", args)
        return self.real_run(args, **kwargs)

    def fetch(self):
        with mock.patch.object(fetcher, "trusted_runtime"):
            with mock.patch.object(fetcher.subprocess, "run", side_effect=self.command):
                fetcher.fetch(self.config)

    def test_fetches_root_bundle_from_data_repo_then_calls_installer(self):
        self.fetch()
        self.assertEqual((self.root / "public.jws").read_bytes(), self.envelope)
        self.assertFalse((self.root / "candidate.jws").exists())
        self.assertFalse(any("checkout" in c or "push" in c for c in self.calls))

    def test_old_path_executable_and_symlink_refused_without_install(self):
        for mode, path in (("100644", "access-trust/bundle.jws"),
                           ("100755", "bundle.jws"), ("120000", "bundle.jws")):
            self.commit(mode, path)
            with self.assertRaisesRegex(ValueError, "nonregular"):
                self.fetch()
            self.assertEqual((self.root / "public.jws").read_text(), "previous")
        self.assertFalse(any(c[0] == "/synthetic/node" for c in self.calls))

    def test_failed_verification_preserves_previous_publication(self):
        self.install_fail = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.fetch()
        self.assertEqual((self.root / "public.jws").read_text(), "previous")
        self.assertFalse((self.root / "candidate.jws").exists())

    def test_legacy_repo_pin_refused_before_any_command(self):
        self.config["repository"] = "ssh://git@github.com/iryzhkov/chaldea-fleet-config.git"
        with self.assertRaisesRegex(ValueError, "transport pin"):
            self.fetch()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
