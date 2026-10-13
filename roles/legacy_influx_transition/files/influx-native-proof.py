#!/usr/bin/env python3
"""Native backup + isolated bucket restore; no ports, no live database mutation."""
import csv
import hashlib
import io
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from importlib.machinery import SourceFileLoader

IMAGE = "git.ryzhkov.dev/igor/mirror/docker.io/library/influxdb@sha256:cdf0f56ff18bc55c73c297d6a3092d461cb58009187e764def2ad89d6f3e0ac5"
canonical = SourceFileLoader("history", str(pathlib.Path(__file__).with_name("influx-history-proof.py"))).load_module().canonical

def run(args, data=None, timeout=120):
    r = subprocess.run(args, input=data, capture_output=True, text=True, timeout=timeout)
    if r.returncode:
        hints = [word for word in ("not found", "already exists", "unauthorized", "organization", "bucket", "manifest", "permission", "restore") if word in r.stderr.lower()]
        raise RuntimeError("command failed: " + args[0] + "; hints=" + ",".join(hints) + " (raw stderr suppressed)")
    return r.stdout

def source(container, args):
    return run(["docker", "exec", container, "sh", "-c",
                'export INFLUX_TOKEN="$(cat /run/secrets/influx_token)"; exec "$@"',
                "influx-proof"] + args, timeout=1200)

def scratch(container, token, args):
    return run(["docker", "exec", "-i", container, "sh", "-c",
                'IFS= read -r INFLUX_TOKEN; export INFLUX_TOKEN; exec "$@"',
                "influx-proof"] + args, data=token + "\n", timeout=1200)

def main():
    if os.geteuid() != 0:
        raise RuntimeError("run as root")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    state = pathlib.Path("/var/lib/legacy-influx-transition")
    state.mkdir(mode=0o700, exist_ok=True)
    root = pathlib.Path("/mnt/docker-data") / ("influx-native-proof-" + stamp)
    if shutil.disk_usage(root.parent).free < 10 * 1024**3:
        raise RuntimeError("less than 10GiB free")
    root.mkdir(mode=0o700)
    cid = run(["docker", "ps", "-q", "--filter",
               "label=com.docker.swarm.service.name=logging_influxdb"]).split()
    if len(cid) != 1:
        raise RuntimeError("exactly one original Influx task required")
    cid = cid[0]
    backup_path = "/tmp/influx-native-proof-" + stamp
    name = "influx-native-restore-" + stamp.lower()
    archive_dir = pathlib.Path("/mnt/docker-data/snapshots/legacy-influx/native")
    archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    archive = archive_dir / ("influxdb_" + stamp + ".tar.gz")
    restored = False
    try:
        buckets = json.loads(source(cid, ["influx", "bucket", "list", "--org", "ryzhkov", "--json"]))
        proofs = []
        for bucket in buckets:
            # Fixed full historical day; finite window and one record per hour,
            # measurement and tag set; limits only the comparison sample.
            query = ('from(bucket: ' + json.dumps(bucket["name"]) + ')'
                     ' |> range(start: 2026-10-12T00:00:00Z, stop: 2026-10-13T00:00:00Z)'
                     ' |> aggregateWindow(every: 1h, fn: last, createEmpty: false)'
                     ' |> sort(columns: ["_time"]) |> limit(n: 3)')
            raw = source(cid, ["influx", "query", "--raw", "--org", "ryzhkov", query])
            try:
                historical = canonical(raw)
            except ValueError:
                # Explicit empty-day inventory is retained in the manifest.
                proofs.append({"bucket": bucket["name"], "query": query, "rows": 0})
                continue
            proofs.append({"bucket": bucket["name"], "query": query, **historical})
        if not any(p["bucket"] == "homeassistant" and p["rows"] for p in proofs):
            raise RuntimeError("Home Assistant historical proof absent")
        cached = os.environ.get("INFLUX_PROOF_ARCHIVE")
        if cached:
            archive = pathlib.Path(cached)
            with tarfile.open(archive) as tar:
                for member in tar.getmembers():
                    pieces = pathlib.PurePosixPath(member.name).parts
                    if not pieces or pieces[0] != "influxdb-backup" or ".." in pieces or not (member.isfile() or member.isdir()):
                        raise RuntimeError("unsafe archive member")
                tar.extractall(root)
            (root / "influxdb-backup").rename(root / "native")
        else:
            print("Historical source samples collected; starting native backup", flush=True)
            source(cid, ["influx", "backup", backup_path])
            run(["docker", "cp", cid + ":" + backup_path, str(root / "native")], timeout=1200)
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(root / "native", arcname="influxdb-backup")
        with archive.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        token = secrets.token_urlsafe(40)
        print("Native archive complete; starting isolated server", flush=True)
        run(["docker", "run", "-d", "--name", name, "--network", "none",
             "--memory", "1g", "--cpus", "0.5", "--log-driver", "local",
             "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
             "--mount", "type=bind,source=" + str(root / "native") + ",target=/backup,readonly",
             IMAGE, "influxd"])
        for _ in range(60):
            r = subprocess.run(["docker", "exec", name, "curl", "-fsS", "http://127.0.0.1:8086/health"],
                               capture_output=True, timeout=10)
            if r.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("scratch server not healthy")
        body = json.dumps({"username": "restore-proof", "password": secrets.token_urlsafe(32),
                           "org": "ryzhkov", "bucket": "scratch-bootstrap", "token": token})
        run(["docker", "exec", "-i", name, "curl", "-fsS", "-X", "POST",
             "-H", "Content-Type: application/json", "--data-binary", "@-",
             "http://127.0.0.1:8086/api/v2/setup"], data=body)
        for proof in proofs:
            if not proof["rows"]:
                continue
            target = "proof-" + proof["bucket"].lstrip("_")
            print("Restoring native bucket " + proof["bucket"], flush=True)
            scratch(name, token, ["influx", "restore", "--org", "ryzhkov",
                                  "--bucket", proof["bucket"], "--new-bucket", target, "/backup"])
            query = proof["query"].replace(json.dumps(proof["bucket"]), json.dumps(target), 1)
            actual = canonical(scratch(name, token, ["influx", "query", "--raw", "--org", "ryzhkov", query]))
            if actual["sha256"] != proof["sha256"] or actual["rows"] != proof["rows"]:
                raise RuntimeError("isolated history comparison failed")
            proof["native_restore_passed"] = True
            print("native restore matched " + proof["bucket"] + ": " + str(proof["rows"]) + " historical records", flush=True)
        restored = True
        manifest = {"created_at": int(time.time()), "archive": str(archive),
                    "archive_sha256": digest, "native_bucket_restore_passed": True,
                    "image": IMAGE, "historical": proofs}
        (state / "proof.json").write_text(json.dumps(manifest, indent=2) + "\n")
        os.chmod(state / "proof.json", 0o600)
        print("Native proof manifest: " + str(state / "proof.json"), flush=True)
        print("Archive SHA256: " + digest, flush=True)
    finally:
        # Only the unique scratch container can be killed; no live task restart.
        result = subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        gone = subprocess.run(["docker", "inspect", name], capture_output=True).returncode != 0
        if gone:
            shutil.rmtree(root)
        else:
            print("Scratch retained: container removal not confirmed", file=sys.stderr)
        subprocess.run(["docker", "exec", cid, "rm", "-rf", backup_path], capture_output=True)
    return restored

if __name__ == "__main__":
    try:
        if not main():
            sys.exit(1)
    except Exception as exc:
        detail = str(exc) if isinstance(exc, RuntimeError) else "details suppressed"
        print(type(exc).__name__ + ": native restore proof failed: " + detail, file=sys.stderr)
        sys.exit(1)
