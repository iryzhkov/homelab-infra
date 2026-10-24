#!/usr/bin/env python3
"""Update only the local ov-memory TOML credential; never enroll or restart."""
import argparse
import base64
import contextlib
import fcntl
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tomllib


class Refusal(Exception):
    pass


def require(ok, message):
    if not ok:
        raise Refusal(message)


def ancestors(path):
    require(path.is_absolute() and ".." not in path.parts, "absolute canonical path required")
    for parent in reversed(path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode), "unsafe parent type")
        require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                "unsafe parent ownership or permissions")


def read_file(path, private=True, missing=False):
    ancestors(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        require(missing, "required file missing")
        return None, None
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "unsafe file type or links")
        require(info.st_uid == os.geteuid(), "file owner must be the executing user")
        mode = stat.S_IMODE(info.st_mode)
        require(mode == 0o600 if private else mode in (0o600, 0o644),
                "unsafe file permissions")
        data = handle.read(65537)
        require(len(data) <= 65536, "file too large")
    return data, mode


def key_bytes(data):
    data = data.rstrip(b"\r\n")
    require(0 < len(data) <= 4096 and all(33 <= value <= 126 for value in data),
            "invalid credential format")
    return data


def source_key(path):
    path = Path(path)
    ancestors(path)
    require(not any((parent / ".git").exists() for parent in path.parents),
            "credential source must be outside repositories")
    return key_bytes(read_file(path)[0])


def render(before, key):
    text = (before or b"").decode("utf-8")
    parsed = tomllib.loads(text)
    lines = text.splitlines(keepends=True)
    root = True
    kept = []
    for line in lines:
        if line.lstrip().startswith("["):
            root = False
        if root and re.match(r"\s*api_key\s*=", line):
            one = tomllib.loads(line)
            require(set(one) == {"api_key"}, "unsupported credential assignment")
            continue
        kept.append(line)
    rendered = ("api_key = " + json.dumps(key.decode("ascii")) + "\n" + "".join(kept)).encode()
    expected = dict(parsed, api_key=key.decode("ascii"))
    require(tomllib.loads(rendered.decode()) == expected, "TOML preservation check failed")
    return rendered


def make_dir(path, mode):
    if not path.exists():
        ancestors(path)
        path.mkdir(mode=mode)
    ancestors(path / "leaf")
    require(path.is_dir() and not path.is_symlink(), "unsafe directory")
    if mode == 0o700:
        info = path.stat()
        require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == mode,
                "backup directory must be private")


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data, mode):
    ancestors(path)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def equal(left, right):
    return left is None and right is None or (
        left is not None and right is not None and hmac.compare_digest(left, right))


def unpack(value):
    return None if value is None else base64.b64decode(value, validate=True)


def pack(value):
    return None if value is None else base64.b64encode(value).decode("ascii")


def operate(home, host, approved_hosts, generation, action, key=None, apply=False):
    require(host in approved_hosts and len(set(approved_hosts)) == len(approved_hosts),
            "host is outside the explicit scope")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host) is not None,
            "invalid host alias")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", generation) is not None,
            "invalid generation")
    require(action in ("install", "restore"), "invalid action")
    home = Path(home)
    require(home == Path.home() and home.is_absolute(), "only the executing user's HOME is supported")
    folder = home / ".config" / "ov-mcp"
    target = folder / "config.toml"
    backups = folder / "credential-rollovers"
    backup = backups / (generation + ".json")
    if apply:
        make_dir(home / ".config", 0o755)
        make_dir(folder, 0o755)
        make_dir(backups, 0o700)
    # No directory or lock creation in dry-run.
    lock = contextlib.nullcontext()
    if apply:
        lock = open_lock(backups / ".lock")
    with lock:
        if folder.exists():
            current, mode = read_file(target, private=False, missing=True)
        else:
            ancestors(folder)
            current, mode = None, None
        if current is not None:
            parsed = tomllib.loads(current.decode())
            require(not parsed.get("api_key") or mode == 0o600,
                    "existing inline credential is not private")
        if backups.exists():
            saved, _ = read_file(backup, missing=True)
        else:
            saved = None
        record = json.loads(saved) if saved is not None else {}
        if saved is not None:
            require(set(record) == {"version", "host", "generation", "before", "after", "mode"}
                    and record["version"] == 1 and record["host"] == host
                    and record["generation"] == generation, "backup identity mismatch")
            before, after = unpack(record["before"]), unpack(record["after"])
            require(after is not None and record["mode"] in (None, 0o600, 0o644)
                    and (before is None) == (record["mode"] is None), "invalid backup")
            require(equal(current, before) or equal(current, after), "client changed since backup")
        else:
            require(action == "install", "rollback backup missing")
            before = current
            after = render(before, key_bytes(key))
            record = dict(version=1, host=host, generation=generation,
                          before=pack(before), after=pack(after), mode=mode)
        if action == "install":
            require(equal(render(before, key_bytes(key)), after),
                    "generation already belongs to a different credential")
            wanted, wanted_mode = after, 0o600
        else:
            wanted, wanted_mode = before, record["mode"]
        changed = not equal(current, wanted) or mode != wanted_mode
        if apply and changed:
            if saved is None:
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(json.dumps(record, sort_keys=True).encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                sync_dir(backups)
            check, check_mode = read_file(target, private=False, missing=True)
            require(equal(check, current) and check_mode == mode, "client changed before commit")
            if wanted is None:
                target.unlink()
                sync_dir(folder)
            else:
                atomic_write(target, wanted, wanted_mode)
        return dict(action=action, host=host, changed=changed, dry_run=not apply,
                    mcp_restart_required=True)


@contextlib.contextmanager
def open_lock(path):
    ancestors(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, "r+b") as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
                "unsafe lock")
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host")
    parser.add_argument("--approved-host", action="append", default=[])
    parser.add_argument("--generation")
    parser.add_argument("--action", choices=["install", "restore"], default="install")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source")
    source.add_argument("--stdin-key", action="store_true")
    parser.add_argument("--check-source", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        os.umask(0o077)
        if args.check_source:
            require(args.source is not None and not args.apply, "source path required")
            source_key(args.source)
            result = dict(source_valid=True)
        else:
            require(args.host and args.generation, "host and generation required")
            key = None
            if args.action == "install":
                require(args.source or args.stdin_key, "protected source required")
                key = source_key(args.source) if args.source else key_bytes(sys.stdin.buffer.read(4099))
            result = operate(Path.home(), args.host, args.approved_host, args.generation,
                             args.action, key, args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (Refusal, OSError, ValueError, TypeError, KeyError):
        # Never include exception text, TOML lines, a key, or a traceback.
        print("credential update refused; inspect protected inputs and binding state", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
