#!/usr/bin/env python3
"""Every-start native Garage guard. Inert until separately installed."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tomllib

BINARY = "/usr/local/lib/garage/2.4.1/garage"
BINARY_SHA = "ae49f8de4aaee6b5ca305f7cbdccc8cd8a29b2804aac074649f18bfb83d0a2b6"


def require(value, reason):
    if not value:
        raise ValueError(reason)


def trusted(path, cap, *, secret=False):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(path), "canonical path required")
    for parent in reversed(path.parents):
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0
                and not value.st_mode & 0o022, "root directory custody required")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1
                and value.st_uid == (1000 if secret else 0)
                and not value.st_mode & (0o077 if secret else 0o022)
                and value.st_size <= cap, "private regular file custody required")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(cap + 1)
        require(len(raw) <= cap, "file bound")
        return raw
    finally:
        os.close(fd)


def check(storage, garage, *, probe=None):
    require(storage.get("enabled") is True and storage.get("uid") == 1000
            and storage.get("gid") == 1000, "enabled qualified 1000:1000 storage required")
    spec = importlib.util.spec_from_file_location(
        "garage_preflight", str(Path(__file__).with_name("garage-storage-preflight.py")))
    assert spec is not None and spec.loader is not None
    preflight = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preflight)
    result = preflight.validate(storage, probe)
    require(result["status"] == "passed-startup-preflight", "storage refused")
    expected = {
        "metadata_dir": storage["metadata"]["path"] + "/meta",
        "data_dir": storage["data"]["path"],
        "db_engine": "lmdb", "metadata_fsync": True, "data_fsync": True,
        "replication_factor": 1, "consistency_mode": "consistent",
        "compression_level": 1,
        "rpc_bind_addr": "127.0.0.1:3901", "rpc_public_addr": "127.0.0.1:3901",
        "rpc_secret_file": "/etc/garage/rpc.secret",
        "s3_api": {"s3_region": "garage", "api_bind_addr": "127.0.0.1:3900"},
    }
    require(json.dumps(garage, sort_keys=True) == json.dumps(expected, sort_keys=True),
            "actual Garage config differs from native contract")
    require(storage["data_fsync"] is True and storage["metadata_fsync"] is True,
            "fsync required")
    # Space admission is conservative and point in time. It reserves nothing.
    for label, minimum in (("data", 1024**3), ("metadata", 2 * 1024**3)):
        value = os.statvfs(storage[label]["path"])
        require(value.f_bavail * value.f_frsize >= minimum
                and value.f_favail >= 4096, "startup headroom refused")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        require(args.config == "/etc/garage/storage.json", "fixed storage config required")
        require(os.getuid() == 1000 and os.getgid() == 1000, "1000:1000 required")
        storage = json.loads(trusted(args.config, 65536))
        garage_path = storage["metadata"]["path"] + "/config/garage.toml"
        garage = tomllib.loads(trusted(garage_path, 16384).decode())
        require(hashlib.sha256(trusted(BINARY, 128 * 1024**2)).hexdigest()
                == BINARY_SHA, "pinned executable mismatch")
        secret = trusted("/etc/garage/rpc.secret", 128, secret=True).strip()
        require(len(secret) == 64 and all(c in b"0123456789abcdef" for c in secret),
                "RPC secret format")
        trusted(str(Path(__file__).with_name("garage-storage-preflight.py")), 65536)
        check(storage, garage)
        os.execv(BINARY, [BINARY, "-c", garage_path, "server"])
    except (OSError, ValueError, KeyError, TypeError):
        print("native Garage startup refused", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
