#!/usr/bin/env python3
"""Prepare an isolated OV 0.5.2 candidate from an operator's quiescent backup.

Never stops services, changes production, issues credentials, or submits reindex.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

IMAGE = "ghcr.io/volcengine/openviking@sha256:2ae2799dac1e606218d9ffeb06af3b65c8218b3556555620a369ed81b7c5aa1f"
SUFFIX = (".cache", "ov-selfhost-20261024", "upgrade-stage")
CANDIDATE = "candidate-0.5.2"


def stage_path(value):
    path = Path(value)
    if not path.is_absolute() or tuple(path.parts[-3:]) != SUFFIX or ".." in path.parts:
        raise ValueError("stage must be an absolute .cache/ov-selfhost-20261024/upgrade-stage path")
    for node in [path, *path.parents]:
        if node.is_symlink():
            raise ValueError("symlink in stage path")
    if not path.is_dir():
        raise ValueError("operator-created stage directory is required")
    return path


def regular_tree(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError("expected a real directory")
    for path in sorted(root.rglob("*")):
        mode = path.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("backup/candidate contains a symlink or special file")
        if stat.S_ISREG(mode):
            yield path


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in regular_tree(root)}


def verify_files(root, expected):
    actual = manifest(root)
    if any(actual.get(name) != digest for name, digest in expected.items()):
        raise ValueError("original Viking content changed or disappeared")
    return {"original_files": len(expected), "added_files": len(actual.keys() - expected.keys()),
            "abstracts": sum(Path(p).name == ".abstract.md" for p in expected),
            "overviews": sum(Path(p).name == ".overview.md" for p in expected)}


def write_private(path, value):
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def compose(stage):
    candidate = stage / CANDIDATE
    return {"services": {"candidate": {
        "image": IMAGE, "container_name": "ov-upgrade-052",
        "restart": "no", "mem_limit": "1536m", "memswap_limit": "1536m", "cpus": 1,
        "ports": ["127.0.0.1:19335:1933"],
        "environment": {"OPENVIKING_CONFIG_FILE": "/app/ov.conf",
                        "OPENVIKING_WITH_BOT": "0", "TMPDIR": "/app/data/temp"},
        "volumes": [
            {"type": "bind", "source": str(candidate / "config/ov.conf"),
             "target": "/app/ov.conf", "read_only": True},
            {"type": "bind", "source": str(candidate / "data"), "target": "/app/data"}]}}}


def prepare(stage):
    backup = stage / "backup-0.2.6"
    list(regular_tree(backup))
    # The operator creates this marker only AFTER a stopped-service copy completes.
    if (backup / "complete").is_symlink() or (backup / "complete").read_text().strip() != "quiescent-copy":
        raise ValueError("completed quiescent backup is required")
    original = manifest(backup / "data/viking")
    if not original:
        raise ValueError("empty Viking backup")
    config = json.loads((backup / "config/ov.conf").read_text())
    if config.get("storage", {}).get("workspace") != "/app/data":
        raise ValueError("only the qualified /app/data workspace is supported")
    target = stage / CANDIDATE
    expected_compose = compose(stage)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_dir():
            raise ValueError("candidate must be a real directory")
        list(regular_tree(target))
        saved = json.loads((target / "original-viking.json").read_text())
        if saved != original or json.loads((target / "compose.json").read_text()) != expected_compose:
            raise ValueError("existing candidate does not match this backup/release")
        return verify_files(target / "data/viking", original) | {"changed": False}
    # Publish the complete candidate with one rename. Failure leaves backup untouched.
    pending = Path(tempfile.mkdtemp(prefix=".candidate-", dir=stage))
    try:
        shutil.copytree(backup / "data", pending / "data")
        shutil.copytree(backup / "config", pending / "config")
        (pending / "config/ov.conf").chmod(0o600)
        write_private(pending / "original-viking.json", original)
        write_private(pending / "compose.json", expected_compose)
        result = verify_files(pending / "data/viking", original)
        pending.rename(target)
        return result | {"changed": True}
    finally:
        if pending.exists():
            shutil.rmtree(pending)


def verify(stage):
    target = stage / CANDIDATE
    if target.is_symlink():
        raise ValueError("candidate symlink")
    list(regular_tree(target))
    json.loads((target / "config/ov.conf").read_text())
    expected = json.loads((target / "original-viking.json").read_text())
    # Compare with immutable input too; never trust a replaced candidate manifest.
    if expected != manifest(stage / "backup-0.2.6/data/viking"):
        raise ValueError("backup manifest changed")
    return verify_files(target / "data/viking", expected)


def reindex_plan():
    # Plan only. Root must first install the local embedding provider and a tenant
    # admin credential. ROOT keys are forbidden on data APIs in 0.5.2 api_key mode.
    return {"method": "POST", "path": "/api/v1/content/reindex",
            "body": {"uri": "viking://resources/", "mode": "vectors_only",
                     "recursive": True, "force": True, "wait": False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "reindex-plan"))
    parser.add_argument("--stage-root")
    args = parser.parse_args()
    if args.action == "reindex-plan":
        result = reindex_plan()
    else:
        if not args.stage_root:
            parser.error("--stage-root is required")
        stage = stage_path(args.stage_root)
        os.umask(0o077)
        fd = os.open(stage / ".migration.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = prepare(stage) if args.action == "prepare" else verify(stage)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError):
        # Paths and configuration contents can be sensitive; never dump exceptions.
        raise SystemExit("migration refused; inspect private staging inputs")
