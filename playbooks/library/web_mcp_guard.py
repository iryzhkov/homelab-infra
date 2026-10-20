#!/usr/bin/python
"""Read-only preflight for the two fixed web MCP packages and route files."""
import grp
import hashlib
import os
from pathlib import Path
import pwd
import stat
import struct

from ansible.module_utils.basic import AnsibleModule

SERVICES = ("ha-mcp", "homelab-observer")
EDGE = Path("/home/igor/apps/traefik/file_provider")
PACKAGE_ROOT = Path("/opt/web-mcp")


def need(condition):
    if not condition:
        raise ValueError("protected path, ownership or dedicated identity precondition failed")


def inspect(path, owners, directory=False, exact_mode=None, required=False):
    """lstat every ancestor; never traverse a writable or symbolic-link parent."""
    path = Path(path)
    need(path.is_absolute() and ".." not in path.parts)
    parts = list(reversed(path.parents)) + [path]
    result = None
    for part in parts:
        try:
            result = part.lstat()
        except FileNotFoundError:
            need(not required)
            return None
        need(not stat.S_ISLNK(result.st_mode) and result.st_uid in owners)
        if part != path or directory:
            need(stat.S_ISDIR(result.st_mode) and result.st_mode & 0o022 == 0)
        else:
            need(stat.S_ISREG(result.st_mode) and result.st_nlink == 1)
            if exact_mode is not None:
                need(stat.S_IMODE(result.st_mode) == exact_mode)
    return result


def optional_account(lookup, key):
    try:
        return lookup(key)
    except KeyError:
        return None


def dedicated(service, uid, gid):
    user = optional_account(pwd.getpwnam, service)
    by_uid = optional_account(pwd.getpwuid, uid)
    group = optional_account(grp.getgrnam, service)
    by_gid = optional_account(grp.getgrgid, gid)
    need(user is None or (user.pw_uid == uid and user.pw_gid == gid
                         and user.pw_dir == "/nonexistent"
                         and user.pw_shell == "/usr/sbin/nologin"))
    need(by_uid is None or by_uid.pw_name == service)
    need(group is None or (group.gr_gid == gid and not group.gr_mem))
    need(by_gid is None or by_gid.gr_name == service)
    need(all(entry.pw_name == service for entry in pwd.getpwall()
             if entry.pw_uid == uid or entry.pw_gid == gid))


def endpoint(service, uid, gid, sha256, credential_names):
    dedicated(service, uid, gid)
    inspect("/etc/ssl/certs/ca-certificates.crt", {0}, exact_mode=0o644, required=True)
    root = PACKAGE_ROOT / service
    config = Path("/etc") / service
    unit = Path("/etc/systemd/system") / (service + ".service")
    marker = root / ".homelab-infra-owned"
    found = inspect(marker, {0}, exact_mode=0o644)
    if found:
        need(marker.read_text() == "homelab-infra web-mcp " + service + "\n")
    else:
        # Never adopt an existing user's directory, unit or endpoint implicitly.
        need(not root.exists() and not config.exists() and not unit.exists())
    for path in (root, root / "releases", root / "releases" / sha256, config):
        inspect(path, {0}, directory=True)
    inspect(unit, {0}, exact_mode=0o644)
    inspect(config / "config.json", {0, uid}, exact_mode=0o600)
    for name in credential_names:
        need(name == "backend-token" if service == "ha-mcp"
             else name.startswith("gatus-") and name.endswith(".json") and "/" not in name)
        inspect(config / name, {0, uid}, exact_mode=0o600)
    inspect(root / "releases" / sha256 / service, {0}, exact_mode=0o555)


def route(service, uid):
    inspect(EDGE, {0, uid}, directory=True, required=True)
    path = EDGE / ("web-mcp-" + service + ".yml")
    if inspect(path, {0, uid}, exact_mode=0o644):
        need(path.lstat().st_uid == 0)
        with path.open() as stream:
            need(stream.readline() == "# Managed by homelab-infra web-mcp " + service + "\n")


def binary(service, sha256):
    path = PACKAGE_ROOT / service / "releases" / sha256 / service
    inspect(path, {0}, exact_mode=0o555, required=True)
    with path.open("rb") as stream:
        header = stream.read(64)
        need(len(header) == 64 and header[:6] == b"\x7fELF\x02\x01")
        need(struct.unpack_from("<H", header, 18)[0] == 62)
        offset = struct.unpack_from("<Q", header, 32)[0]
        size, count = struct.unpack_from("<HH", header, 54)
        need(size == 56 and 0 < count <= 128)
        stream.seek(offset)
        for _ in range(count):
            program = stream.read(size)
            need(len(program) == size and struct.unpack_from("<I", program)[0] != 3)
        stream.seek(0)
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        need(digest.hexdigest() == sha256)


def main():
    module = AnsibleModule(argument_spec={
        "kind": {"type": "str", "choices": ["endpoint", "credential", "route", "binary"],
                 "required": True},
        "service": {"type": "str", "choices": list(SERVICES)},
        "uid": {"type": "int"},
        "gid": {"type": "int"},
        "sha256": {"type": "str"},
        "credential_names": {"type": "list", "elements": "str", "default": []},
        "source": {"type": "path", "no_log": True},
    }, supports_check_mode=True)
    p = module.params
    try:
        if p["kind"] == "credential":
            need(p["uid"] is not None and p["source"])
            result = inspect(p["source"], {0, p["uid"]}, exact_mode=0o600, required=True)
            if result is None:
                raise ValueError("missing source")
            need(result.st_uid == p["uid"])
        else:
            need(p["service"] in SERVICES)
            if p["kind"] in ("endpoint", "binary"):
                import re
                need(isinstance(p["sha256"], str)
                     and re.fullmatch(r"[a-f0-9]{64}", p["sha256"]) is not None)
            if p["kind"] == "endpoint":
                need(p["uid"] is not None and 100 <= p["uid"] <= 999
                     and p["gid"] is not None and 100 <= p["gid"] <= 999)
                endpoint(p["service"], p["uid"], p["gid"], p["sha256"], p["credential_names"])
            elif p["kind"] == "binary":
                binary(p["service"], p["sha256"])
            else:
                need(p["uid"] is not None)
                route(p["service"], p["uid"])
    except (OSError, ValueError, TypeError):
        module.fail_json(msg="web-mcp protected preflight refused; inspect fixed paths and identity")
    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
