#!/usr/bin/python
"""Private host-bound credential delivery with monotonic generation and revoke latch."""
import fcntl
import hashlib
import json
import os
import pwd
import socket
import stat
from ansible.module_utils.basic import AnsibleModule

def require(ok):
    if not ok:
        raise ValueError("Host credential custody refused")

from ansible.module_utils.authentik_enrollment import read_file, atomic

def credential(fd, host, uid, generation, revoked, password=None, check=False):
    raw = read_file(fd, "host.generation.json")
    secret = read_file(fd, "host.app-password")
    marker = json.loads(raw) if raw is not None else None
    if marker:
        require(marker["host"] == host and marker["uid"] == uid
                and type(marker["generation"]) is int and marker["generation"] > 0)
        require(marker["status"] in ("committed", "revoked"))
        revoked = revoked or marker["status"] == "revoked"
    elif secret is not None:
        # An orphaned key must never be adopted or overwritten.
        require(False)
    if revoked:
        changed = secret is not None or marker is None or marker["status"] != "revoked"
        if not check and changed:
            tombstone = {"host": host, "uid": uid, "generation": max(generation, marker["generation"] if marker else 0),
                         "status": "revoked"}
            atomic(fd, "host.generation.json", json.dumps(tombstone).encode())
            if secret is not None:
                os.unlink("host.app-password", dir_fd=fd)
                os.fsync(fd)
        return changed, False
    if marker:
        require(secret is not None and hashlib.sha256(secret).hexdigest() == marker["sha256"])
        # The host's self-rotated generation always wins over bootstrap inventory.
        if marker["generation"] >= generation:
            return False, False
        # Bootstrap cannot rotate a credential. P3.2 owns this transition.
        require(False)
    if password is None or check:
        return True, True
    require(isinstance(password, str) and 16 <= len(password) <= 256
            and all(33 <= ord(c) <= 126 for c in password))
    body = password.encode()
    marker = {"host": host, "uid": uid, "generation": generation, "status": "pending",
              "sha256": hashlib.sha256(body).hexdigest()}
    # A crash at any subsequent point leaves an explicit unconfirmed operation.
    # Refuse replay until the operator reconciles that marker.
    atomic(fd, "host.generation.json", json.dumps(marker).encode())
    atomic(fd, "host.app-password", body)
    marker["status"] = "committed"
    atomic(fd, "host.generation.json", json.dumps(marker).encode())
    return True, False

def directory(home, create):
    require(os.path.normpath(home) == home and home.startswith("/home/"))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    changed = False
    try:
        parts = (home + "/.config/chaldea/authentik").strip("/").split("/")
        for index, part in enumerate(parts):
            st = os.fstat(fd)
            require(st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022)
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                require(index >= len(home.strip("/").split("/")))
                if not create:
                    os.close(fd)
                    return None, True
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                changed = True
            os.close(fd)
            fd = next_fd
            st = os.fstat(fd)
            require(st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022)
            if index >= len(parts) - 2:
                require(st.st_uid == os.getuid())
                if stat.S_IMODE(st.st_mode) != 0o700:
                    changed = True
                    if create:
                        os.fchmod(fd, 0o700)
        return fd, changed
    except Exception:
        os.close(fd)
        raise

def main():
    m = AnsibleModule(argument_spec={
        "host": {"type": "str", "required": True},
        "home": {"type": "path", "required": True},
        "hostname": {"type": "str", "required": True},
        "uid": {"type": "int", "required": True},
        "generation": {"type": "int", "required": True},
        "revoked": {"type": "bool", "required": True},
        "password": {"type": "str", "no_log": True},
        "inspect": {"type": "bool", "default": False},
    }, supports_check_mode=True)
    fd = lock = None
    try:
        p = m.params
        require(os.getuid() == p["uid"] and socket.gethostname() == p["hostname"])
        require(pwd.getpwuid(os.getuid()).pw_dir == p["home"])
        check = m.check_mode or p["inspect"]
        fd, changed = directory(p["home"], not check)
        if fd is None:
            m.exit_json(changed=True, needs_key=not p["revoked"])
        if not check:
            lock = os.open("host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            st = os.fstat(lock)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1
                    and stat.S_IMODE(st.st_mode) == 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        updated, needs_key = credential(fd, p["host"], p["uid"], p["generation"],
                                        p["revoked"], p["password"], check)
        m.exit_json(changed=changed or updated, needs_key=needs_key)
    except Exception:
        m.fail_json(msg="Host delivery refused. Inspect host binding, private custody and generation marker; do not replay a pending operation.")
    finally:
        if lock is not None:
            os.close(lock)
        if fd is not None:
            os.close(fd)

if __name__ == "__main__":
    main()
