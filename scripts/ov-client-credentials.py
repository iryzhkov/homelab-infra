#!/usr/bin/env python3
"""Roll over an existing live ov-memory keyring item or keyfile."""
import argparse
import base64
import contextlib
import fcntl
import hmac
import importlib
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


def check_overrides(folder):
    require("OV_API_KEY" not in os.environ, "OV_API_KEY override present; dynamic rollover refused")
    require("OV_CONFIG" not in os.environ, "OV_CONFIG override present; binding needs inspection")
    config, _ = read_file(folder / "config.toml", private=False, missing=True)
    if config is not None:
        require("api_key" not in tomllib.loads(config.decode()),
                "cached TOML api_key present; dynamic rollover refused")


class Keyfile:
    def __init__(self, folder):
        self.path = folder / "key"

    def read(self):
        data, _ = read_file(self.path)
        key_bytes(data)
        return data, {"path": str(self.path), "mode": 0o600}

    def write(self, data):
        atomic_write(self.path, data, 0o600)


class Keyring:
    """No unlock, prompt, store creation, secret-tool fallback, or secret argv."""
    def __init__(self):
        try:
            import gi
            gi.require_version("Secret", "1")
            self.secret = importlib.import_module("gi.repository.Secret")
            self.service = self.secret.Service.get_sync(self.secret.ServiceFlags.OPEN_SESSION, None)
            require(self.service is not None, "existing keyring unavailable")
        except Refusal:
            raise
        except Exception:
            raise Refusal("existing keyring unavailable; no fallback") from None

    def read(self):
        try:
            items = self.service.search_sync(
                None, {"service": "openviking", "key": "api"},
                self.secret.SearchFlags.ALL, None)
            require(len(items) == 1, "existing keyring item missing or ambiguous")
            item = items[0]
            require(not item.get_locked(), "existing keyring item locked")
            require(item.load_secret_sync(None), "existing keyring secret unavailable")
            value = item.get_secret()
            if value is None or value.get_text() is None:
                raise Refusal("existing keyring secret is not text")
            data = bytes(value.get())
            key_bytes(data)
            self.item = item
            return data, {"item": item.get_object_path(), "attributes": dict(item.get_attributes()),
                          "label": item.get_label()}
        except Refusal:
            raise
        except Exception:
            raise Refusal("existing keyring read failed; no fallback") from None

    def write(self, data):
        try:
            value = self.secret.Value.new(data.decode("ascii"), len(data), "text/plain")
            require(self.item.set_secret_sync(value, None), "existing keyring update failed")
        except Refusal:
            raise
        except Exception:
            raise Refusal("existing keyring update failed; no fallback") from None


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


def operate(home, host, approved_hosts, generation, action, backend,
            key=None, apply=False, keyring_factory=Keyring):
    require(host in approved_hosts and len(set(approved_hosts)) == len(approved_hosts),
            "host is outside the explicit scope")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", host) is not None,
            "invalid host alias")
    require(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", generation) is not None,
            "invalid generation")
    require(action in ("install", "restore"), "invalid action")
    require(backend in ("keyfile", "keyring"), "explicit existing backend required")
    home = Path(home)
    require(home == Path.home() and home.is_absolute(), "only the executing user's HOME is supported")
    folder = home / ".config" / "ov-mcp"
    # Existing binding only: never create a client directory, keyfile or keyring item.
    ancestors(folder / "key")
    check_overrides(folder)
    target = Keyfile(folder) if backend == "keyfile" else keyring_factory()
    current, identity = target.read()
    backups = folder / "credential-rollovers"
    require(not any((parent / ".git").exists() for parent in backups.parents),
            "backup must be outside repositories")
    backup = backups / (generation + ".json")
    if backups.exists():
        make_dir(backups, 0o700)
    if apply:
        make_dir(backups, 0o700)
    lock = open_lock(backups / ".lock") if apply else contextlib.nullcontext()
    with lock:
        current, identity = target.read()
        saved, _ = read_file(backup, missing=True) if backups.exists() else (None, None)
        record = json.loads(saved) if saved is not None else {}
        if saved is not None:
            require(set(record) == {"version", "host", "generation", "backend",
                                    "identity", "before", "after"}
                    and record["version"] == 2, "unsupported backup schema; inspect prior r1 state")
            require(record["host"] == host and record["generation"] == generation
                    and record["backend"] == backend and record["identity"] == identity,
                    "backup binding or generation mismatch")
            before, after = unpack(record["before"]), unpack(record["after"])
            require(before is not None and after is not None, "invalid backup")
            key_bytes(before)
            require(equal(key_bytes(after), after), "invalid selected backup credential")
            require(equal(current, before) or equal(current, after), "client changed since backup")
        else:
            require(action == "install", "rollback backup missing")
            before, after = current, key_bytes(key)
            record = dict(version=2, host=host, generation=generation, backend=backend,
                          identity=identity, before=pack(before), after=pack(after))
        if action == "install":
            require(equal(key_bytes(key), after), "generation already belongs to a different credential")
            wanted = after
        else:
            wanted = before
        changed = not equal(current, wanted)
        if apply and changed:
            if saved is None:
                fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(json.dumps(record, sort_keys=True).encode())
                    handle.flush()
                    os.fsync(handle.fileno())
                sync_dir(backups)
            check_overrides(folder)
            check, check_identity = target.read()
            require(equal(check, current) and check_identity == identity,
                    "client changed before commit")
            target.write(wanted)
            check, check_identity = target.read()
            require(equal(check, wanted) and check_identity == identity,
                    "client verification failed; retain backup and inspect")
        return dict(action=action, host=host, backend=backend, changed=changed,
                    dry_run=not apply, mcp_restart_required=False)


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
    parser.add_argument("--backend", choices=["keyfile", "keyring"])
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
                             args.action, args.backend, key, args.apply)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refusal as error:
        # Refusal messages are fixed strings, never library exceptions or secret bytes.
        print("credential update refused: " + str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print("credential update refused; inspect protected inputs and binding state", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
