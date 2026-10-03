#!/usr/bin/env python3
"""Regressions for roles/backup/files/homelab-swarm-snapshot.

No SSH, Docker, Backblaze or root: rsync, docker, rclone and the SQLite
snapshot tool are stand-ins on PATH that record how they were called.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/backup/files/homelab-swarm-snapshot"


class SwarmSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.base = Path(self.scratch.name)
        self.pool = self.base / "pool"
        self.state = self.base / "state"
        self.bin = self.base / "bin"
        self.calls = self.base / "calls"
        for directory in (self.pool, self.state, self.bin):
            directory.mkdir()
        self.fake("rsync", 'printf "rsync %s\\n" "$*" >>"$CALLS"; exit "${RSYNC_STATUS:-0}"')
        self.fake("rclone", 'printf "rclone %s\\n" "$*" >>"$CALLS"')
        self.fake("sqlite-backup", 'printf "sqlite %s\\n" "$*" >>"$CALLS"')
        self.fake("docker", 'printf "docker %s\\n" "$*" >>"$CALLS"\n'
                            'case "$1 $2" in "service inspect") echo "${REPLICAS:-1}";; esac')

    def tearDown(self):
        self.scratch.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def own(self, stack, host="homelab"):
        directory = self.state / stack
        (directory / "data").mkdir(parents=True)
        (directory / ".swarm-owner").write_text(host + "\n")

    def run_case(self, stacks, offsite=(), **env):
        config = {
            "host": "homelab", "pool": str(self.pool), "state_root": str(self.state),
            "peer": {"name": "normandy", "address": "192.0.2.2", "user": "swarmsync"},
            "key": "/k", "known_hosts": "/kh", "rclone_config": "/rc",
            "offsite": list(offsite), "stacks": stacks,
        }
        path = self.base / "config.json"
        path.write_text(json.dumps(config))
        environment = dict(os.environ, CALLS=str(self.calls),
                           PATH=str(self.bin) + ":" + os.environ["PATH"],
                           HOMELAB_SQLITE_BACKUP=str(self.bin / "sqlite-backup"), **env)
        run = subprocess.run(["python3", str(SCRIPT), "--config", str(path)],
                             env=environment, capture_output=True, text=True)
        calls = self.calls.read_text().splitlines() if self.calls.exists() else []
        return run, calls

    def test_no_stacks_is_a_silent_no_op(self):
        (self.pool / "snapshots").mkdir()
        (self.pool / "snapshots" / "leftover").write_text("x")
        run, calls = self.run_case([], offsite=[{"source": str(self.pool / "snapshots"),
                                                 "remote": "b2:x"}])
        self.assertEqual((run.returncode, run.stdout, run.stderr, calls), (0, "", "", []))

    def test_missing_config_is_a_no_op(self):
        run = subprocess.run(["python3", str(SCRIPT), "--config", str(self.base / "none")],
                             capture_output=True, text=True)
        self.assertEqual((run.returncode, run.stdout), (0, ""))

    def test_stack_owned_elsewhere_is_left_alone(self):
        self.own("vaultwarden", host="normandy")
        run, calls = self.run_case([{"name": "vaultwarden", "snapshot": {
            "class": "sqlite", "sqlite": [{"name": "vw", "path": "/x"}],
            "files": [str(self.state / "vaultwarden/data")]}}])
        self.assertEqual(run.returncode, 0)
        self.assertEqual(calls, [])

    def test_sqlite_stack_is_snapshotted_and_sent(self):
        self.own("vaultwarden")
        run, calls = self.run_case([{"name": "vaultwarden", "snapshot": {
            "class": "sqlite", "sqlite": [{"name": "vw", "path": "/x", "keep": 48}],
            "files": [str(self.state / "vaultwarden/data")], "exclude": ["icon_cache/**"]}}])
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertTrue(calls[0].startswith("sqlite --config "))
        self.assertIn(f"--out {self.pool}/snapshots/vaultwarden --owner root", calls[0])
        self.assertIn("--delete", calls[1])
        self.assertTrue(calls[1].endswith(
            f"{self.pool}/snapshots/vaultwarden/ swarmsync@192.0.2.2:vaultwarden/snapshots/"))
        self.assertIn("--exclude=icon_cache/**", calls[2])
        self.assertTrue(calls[2].endswith(
            f"{self.state}/vaultwarden/data/ swarmsync@192.0.2.2:vaultwarden/files/data/"))
        self.assertTrue(calls[3].endswith("swarmsync@192.0.2.2:vaultwarden/sync/"))
        self.assertEqual(len(calls), 4)

    def test_missing_files_directory_fails_without_mirroring_it(self):
        self.own("cv-site")
        run, calls = self.run_case([{"name": "cv-site", "snapshot": {
            "class": "files", "files": [str(self.state / "cv-site/gone")]}}])
        self.assertEqual(run.returncode, 1)
        self.assertIn("is missing", run.stderr)
        self.assertEqual(calls, [])

    def test_files_outside_the_stack_directory_are_refused(self):
        self.own("cv-site")
        run, calls = self.run_case([{"name": "cv-site", "snapshot": {
            "class": "files", "files": [str(self.state)]}}])
        self.assertEqual(run.returncode, 1)
        self.assertEqual(calls, [])

    def test_postgres_scaled_to_zero_is_skipped_quietly(self):
        self.own("miniflux")
        run, calls = self.run_case([{"name": "miniflux", "snapshot": {
            "class": "postgres", "postgres": [
                {"service": "miniflux_db", "database": "miniflux", "user": "miniflux"}]}}],
            REPLICAS="0")
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("scaled to 0", run.stdout)

    def test_postgres_with_no_task_running_fails(self):
        self.own("miniflux")
        run, _ = self.run_case([{"name": "miniflux", "snapshot": {
            "class": "postgres", "postgres": [
                {"service": "miniflux_db", "database": "miniflux", "user": "miniflux"}]}}])
        self.assertEqual(run.returncode, 1)
        self.assertIn("no running task", run.stderr)

    def test_failed_peer_copy_fails_and_skips_the_timestamp(self):
        self.own("cv-site")
        run, calls = self.run_case([{"name": "cv-site", "snapshot": {
            "class": "files", "files": [str(self.state / "cv-site/data")]}}], RSYNC_STATUS="12")
        self.assertEqual(run.returncode, 1)
        self.assertFalse(any("/sync/" in call for call in calls))

    def test_offsite_only_ships_directories_that_hold_something(self):
        self.own("cv-site", host="normandy")
        full = self.pool / "peer"
        full.mkdir()
        (full / "x").write_text("x")
        (self.pool / "empty").mkdir()
        run, calls = self.run_case(
            [{"name": "cv-site", "snapshot": {"class": "files", "files": []}}],
            offsite=[{"source": str(self.pool / "empty"), "remote": "b2:a"},
                     {"source": str(full), "remote": "b2:b"}])
        self.assertEqual(run.returncode, 0)
        self.assertEqual(calls, [f"rclone sync {full} b2:b --config /rc"])


if __name__ == "__main__":
    unittest.main()
