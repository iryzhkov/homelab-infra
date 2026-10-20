#!/usr/bin/python
"""Read-only controller artifact preflight. Never returns file contents."""
import hashlib
import os
import re
import stat
import struct

from ansible.module_utils.basic import AnsibleModule

MAX_BYTES = 256 * 1024 * 1024


def need(condition):
    if not condition:
        raise ValueError("protected local artifact refused")


def protected_open(path, owner_uid):
    """Walk from / using held directory descriptors and no-follow opens."""
    need(isinstance(path, str) and len(path.encode("utf-8")) <= 4096
         and re.fullmatch(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*", path))
    parts = path.split("/")[1:]
    need(all(part not in (".", "..") for part in parts))
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            info = os.fstat(directory)
            need(info.st_uid in {0, owner_uid} and not info.st_mode & 0o022)
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        info = os.fstat(directory)
        need(info.st_uid in {0, owner_uid} and not info.st_mode & 0o022)
        # NONBLOCK prevents a replaced FIFO from hanging before fstat refuses it.
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
    finally:
        os.close(directory)
    try:
        info = os.fstat(source)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and info.st_uid == owner_uid and stat.S_IMODE(info.st_mode) == 0o600
             and 64 <= info.st_size <= MAX_BYTES)
        return os.fdopen(source, "rb")
    except BaseException:
        os.close(source)
        raise


def validate(path, owner_uid, sha256):
    need(type(owner_uid) is int and 0 <= owner_uid <= 2147483647
         and isinstance(sha256, str) and re.fullmatch(r"[a-f0-9]{64}", sha256))
    with protected_open(path, owner_uid) as stream:
        before = os.fstat(stream.fileno())
        need(before.st_uid == owner_uid and before.st_nlink == 1
             and stat.S_IMODE(before.st_mode) == 0o600
             and 64 <= before.st_size <= MAX_BYTES)
        digest, transfer = hashlib.sha256(), hashlib.sha1()
        header, programs = b"", b""
        offset = size = count = 0
        # Hash and parse the same byte stream. SHA1 pins Ansible's copy module;
        # SHA256 is the authoritative provenance gate.
        total = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            need(total <= MAX_BYTES)
            digest.update(chunk)
            transfer.update(chunk)
            if not header:
                header = chunk[:64]
                need(header[:7] == b"\x7fELF\x02\x01\x01" and header[7] in (0, 3))
                kind, machine, version = struct.unpack_from("<HHI", header, 16)
                need(kind == 2 and machine == 62 and version == 1)
                offset = struct.unpack_from("<Q", header, 32)[0]
                size, count = struct.unpack_from("<HH", header, 54)
                need(size == 56 and 0 < count <= 128 and offset >= 64
                     and offset + size * count <= before.st_size)
            # Only retain the bounded program table from the bytes being hashed.
            start, end = max(offset, total - len(chunk)), min(offset + size * count, total)
            if end > start:
                programs += chunk[start - (total - len(chunk)):end - (total - len(chunk))]
        need(total == before.st_size and digest.hexdigest() == sha256)
        need(len(programs) == size * count)
        # PT_DYNAMIC and PT_INTERP both require a dynamic runtime.
        need(all(struct.unpack_from("<I", programs, i * size)[0] not in (2, 3)
                 for i in range(count)))
        after = os.fstat(stream.fileno())
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
              before.st_ctime_ns, before.st_uid, before.st_mode, before.st_nlink)
             == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns, after.st_uid, after.st_mode, after.st_nlink))
        return transfer.hexdigest()


def main():
    module = AnsibleModule(argument_spec={
        "path": {"type": "str", "required": True, "no_log": True},
        "owner_uid": {"type": "int", "required": True},
        "sha256": {"type": "str", "required": True},
    }, supports_check_mode=True)
    try:
        checksum = validate(**module.params)
    except (OSError, ValueError, TypeError, struct.error):
        module.fail_json(msg="web-mcp protected local artifact preflight refused")
        return
    module.exit_json(changed=False, sha1=checksum)


if __name__ == "__main__":
    main()
