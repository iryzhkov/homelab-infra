#!/usr/bin/env python3
"""Freshness regressions: no SSH, cloud writes, or notifications."""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "roles/offsite_phone/files/homelab-offsite-run"


class FreshnessTests(unittest.TestCase):
    def run_case(self, timestamps, status=0):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            stage = home / "stage"
            stage.mkdir()
            binaries = home / "bin"
            binaries.mkdir()
            calls = home / "calls"
            config = home / "env"
            config.write_text(f'OFFSITE_STAGE="{stage}"\n'
                              'OFFSITE_TARGETS="oci:oci:a:7 phone:phone:a:7 travel-router:router:a:2"\n')
            for name, seconds_ago in timestamps.items():
                (stage / f".last-delivery-{name}").write_text(str(int(time.time()) - seconds_ago) + "\n")
            # Simulate a successful aggregate delivery regardless of phone age.
            (stage / ".last-delivery").write_text(str(int(time.time())) + "\n")
            for name, body in {
                "homelab-offsite-bundle": f"exit {status}\n",
                "feed": 'printf "%s\\n" "$*" >> "$CALLS"\n',
                "homelab-notify": 'cat >/dev/null\n',
            }.items():
                file = binaries / name
                file.write_text("#!/bin/sh\n" + body)
                file.chmod(0o755)
            env = dict(os.environ, HOME=str(home), HOMELAB_OFFSITE_CONFIG=str(config),
                       CALLS=str(calls), PATH=str(binaries) + ":" + os.environ["PATH"])
            run = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            return run.returncode, calls.read_text() if calls.exists() else ""

    def test_oci_success_does_not_hide_stale_phone(self):
        status, calls = self.run_case({"oci": 0, "phone": 4 * 86400})
        self.assertNotEqual(status, 0)
        self.assertIn("phone backup stale", calls)
        self.assertNotIn("oci backup stale", calls)

    def test_fresh_required_copies_and_absent_router_are_healthy(self):
        self.assertEqual(self.run_case({"oci": 0, "phone": 3600}), (0, ""))

    def test_never_delivered_phone_is_not_reported_as_999_days(self):
        status, calls = self.run_case({"oci": 0})
        self.assertNotEqual(status, 0)
        self.assertIn("never delivered", calls)
        self.assertNotIn("999d", calls)

    def test_build_failure_is_not_hidden(self):
        status, calls = self.run_case({"oci": 0, "phone": 0}, status=1)
        self.assertEqual(status, 1)
        self.assertIn("could not be built", calls)

    def test_zero_deliveries_is_a_service_failure(self):
        status, _ = self.run_case({"oci": 0, "phone": 0}, status=2)
        self.assertNotEqual(status, 0)


INFLUX_SCRIPT = ROOT / "roles/backup/files/homelab-influx-backup"
DOCKER_MOCK = r'''#!/usr/bin/env python3
import io,json,os,sys,tarfile,time,uuid
from pathlib import Path
args=sys.argv[1:]
with open(os.environ["DOCKER_CALLS"],"a") as f:
    f.write(json.dumps(args)+"\n")
if args[:2]==["exec","-i"]: args.pop(1)
if args[0]=="ps":
    if "legacy-influx_influxdb" in " ".join(args): print("database")
elif args[0]=="exec" and args[2]=="mktemp":
    print("/var/lib/influxdb2/influxdb-backup."+uuid.uuid4().hex)
elif args[0]=="exec" and args[2]=="sh":
    token=sys.stdin.readline().strip()
    assert token==os.environ["INFLUX_TOKEN"]
    if os.environ.get("BLOCK_EXPORT"):
        Path(os.environ["EXPORT_STARTED"]).touch()
        deadline=time.monotonic()+10
        while not Path(os.environ["EXPORT_RELEASE"]).exists():
            assert time.monotonic()<deadline
            time.sleep(.02)
elif args[0]=="exec" and args[2]=="rm":
    pass
elif args[0]=="cp":
    if os.environ.get("BROKEN_STREAM"):
        sys.stdout.buffer.write(os.urandom(180000))
    else:
        with tarfile.open(fileobj=sys.stdout.buffer,mode="w|") as archive:
            for name,data in [("influxdb-backup/test.manifest",b"{}"),
                              ("influxdb-backup/data.tar.gz",os.urandom(180000))]:
                info=tarfile.TarInfo(name);info.size=len(data)
                archive.addfile(info,io.BytesIO(data))
else:
    raise SystemExit(9)
'''


class NativeInfluxTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.out=self.root/"archives";self.out.mkdir()
        self.bin=self.root/"bin";self.bin.mkdir()
        docker=self.bin/"docker";docker.write_text(DOCKER_MOCK);docker.chmod(0o755)
        self.calls=self.root/"calls"
        self.started=self.root/"started";self.release=self.root/"release"
        self.env=dict(os.environ,PATH=str(self.bin)+":"+os.environ["PATH"],
                      INFLUX_TOKEN="dummy-only-native-backup-test-token",
                      DOCKER_CALLS=str(self.calls),EXPORT_STARTED=str(self.started),
                      EXPORT_RELEASE=str(self.release))

    def tearDown(self):
        self.temp.cleanup()

    def run_backup(self,**extra):
        return subprocess.run(["bash",str(INFLUX_SCRIPT),str(self.out),"7"],
                              env=dict(self.env,**extra),capture_output=True,text=True,timeout=15)

    def test_overlapping_export_is_refused_without_touching_first_scratch(self):
        first=subprocess.Popen(["bash",str(INFLUX_SCRIPT),str(self.out),"7"],
                               env=dict(self.env,BLOCK_EXPORT="1"),
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+5
            while not self.started.exists():
                self.assertIsNone(first.poll())
                self.assertLess(time.monotonic(),deadline)
                time.sleep(.02)
            rejected=self.run_backup()
            self.assertNotEqual(rejected.returncode,0)
            self.assertIn("another native export",rejected.stderr)
            self.assertEqual(sum('mktemp' in line for line in self.calls.read_text().splitlines()),1)
        finally:
            self.release.touch()
            stdout,stderr=first.communicate(timeout=15)
        self.assertEqual(first.returncode,0,(stdout,stderr))

    def test_corrupt_export_keeps_previous_archive_and_publishes_nothing(self):
        old=self.out/"influxdb_2020-01-01_0000.tar.gz";old.write_bytes(b"previous recovery copy")
        run=self.run_backup(BROKEN_STREAM="1")
        self.assertNotEqual(run.returncode,0)
        self.assertEqual(old.read_bytes(),b"previous recovery copy")
        self.assertEqual(list(self.out.glob("*.tar.gz")),[old])
        self.assertEqual(list(self.out.glob("*.partial")),[])
        self.assertIn('"rm", "-rf"',self.calls.read_text())

    def test_complete_archives_use_unique_scratch_and_keep_native_layout(self):
        for _ in range(2):
            run=self.run_backup()
            self.assertEqual(run.returncode,0,run.stderr)
        archives=list(self.out.glob("*.tar.gz"));self.assertEqual(len(archives),2)
        import tarfile,json
        for archive in archives:
            with tarfile.open(archive) as native:
                self.assertEqual(native.getnames(),["influxdb-backup/test.manifest","influxdb-backup/data.tar.gz"])
            self.assertEqual(archive.stat().st_mode & 0o777,0o600)
        calls=[json.loads(line) for line in self.calls.read_text().splitlines()]
        exports=[call[-1] for call in calls if call[0]=="exec" and "influx-backup" in call]
        self.assertEqual(len(set(exports)),2)
        self.assertNotIn(self.env["INFLUX_TOKEN"],self.calls.read_text())
        self.assertEqual(list(self.out.glob("*.partial")),[])

    def test_daily_cloud_transport_filters_out_coordination_files(self):
        home=self.root/"home";scripts=home/".local/bin";scripts.mkdir(parents=True)
        helper=scripts/"homelab-influx-backup";helper.write_text("#!/bin/sh\nexit 0\n");helper.chmod(0o755)
        capture=self.root/"rclone-args"
        rclone=self.bin/"rclone"
        rclone.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$RCLONE_ARGS"\n')
        rclone.chmod(0o755)
        wrapper=ROOT/"roles/backup/files/homelab-backup-run"
        run=subprocess.run(["bash",str(wrapper),"daily"],
                           env=dict(self.env,HOME=str(home),RCLONE_ARGS=str(capture)),
                           capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(capture.read_text().splitlines()[-2:],["--include","influxdb_*.tar.gz"])


if __name__ == "__main__":
    unittest.main()
