#!/usr/bin/python
"""Read-only preflight for fixed web MCP packages and owned route files."""
import grp
import hashlib
import os
from pathlib import Path
import pwd
import stat
import struct
import subprocess

from ansible.module_utils.basic import AnsibleModule

SERVICES = ("ha-mcp", "homelab-observer", "holonet")
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
    need(group is None or (group.gr_gid == gid and set(group.gr_mem) <= {service}))
    need(by_gid is None or by_gid.gr_name == service)
    # getgrouplist includes the requested primary GID as well as NSS memberships.
    need(set(os.getgrouplist(service, gid)) == {gid})
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
        need(name == "backend-token" if service == "ha-mcp" else
             name == "backend.json" if service == "holonet" else name.startswith("gatus-") and name.endswith(".json") and "/" not in name)
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



OV_ROOT = Path("/opt/web-mcp/ov-http")
OV_OWNER = "homelab-infra ov-http compose v1\n"
OV_LABEL = "homelab-infra.ov-http"


def ov_package(release, files_required=False):
    import re
    need(isinstance(release, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", release))
    # No host account is created or adopted for the container's numeric identity.
    need(optional_account(pwd.getpwuid, 8098) is None)
    need(optional_account(grp.getgrgid, 8098) is None)
    inspect(OV_ROOT, {0}, directory=True)
    marker = OV_ROOT / ".homelab-infra-owned"
    if inspect(marker, {0}, exact_mode=0o644):
        need(marker.read_text() == OV_OWNER)
    else:
        need(not OV_ROOT.exists())
    if OV_ROOT.exists():
        need(set(p.name for p in OV_ROOT.iterdir()) <= {".homelab-infra-owned", "releases"})
    releases = OV_ROOT / "releases"
    inspect(releases, {0}, directory=True)
    if releases.exists():
        for path in releases.iterdir():
            need(re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", path.name))
            inspect(path, {0}, directory=True)
    directory = releases / release
    inspect(directory, {0}, directory=True, required=files_required)
    if directory.exists():
        need(set(p.name for p in directory.iterdir()) <=
             {".homelab-infra-owned", "compose.yml", "runtime.toml", "backend-key"})
        marker = directory / ".homelab-infra-owned"
        need(inspect(marker, {0}, exact_mode=0o644, required=True))
        need(marker.read_text() == OV_OWNER)
    for name in ("runtime.toml", "backend-key"):
        info = inspect(directory / name, {0, 8098}, exact_mode=0o600,
                       required=files_required)
        if info:
            need(info.st_uid == 8098 and info.st_gid == 8098)
    inspect(directory / "compose.yml", {0}, exact_mode=0o644, required=files_required)


def ov_runtime():
    """Inspect only fixed Compose names. Never take over unrelated Docker objects."""
    import json
    import subprocess
    def docker(*args):
        return subprocess.run(["docker", "--host=unix:///var/run/docker.sock", *args], check=True, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    ids = docker("container", "ls", "-aq", "--filter",
                 "label=com.docker.compose.project=ov-http").split()
    # Also catch an unrelated object occupying either fixed container name.
    for name in ("ov-http", "ov-http-check"):
        ids.extend(docker("container", "ls", "-aq", "--filter", "name=^/" + name + "$").split())
    for ident in set(ids):
        obj = json.loads(docker("container", "inspect", ident))[0]
        labels = obj.get("Config", {}).get("Labels", {}) or {}
        need(labels.get(OV_LABEL) == "v1")
        need(labels.get("com.docker.compose.project") == "ov-http")
        need(labels.get("com.docker.compose.service") == "ov-http")
        # Never remove or reuse a left-over check container.
        need(obj["Name"] == "/ov-http")
    networks = docker("network", "ls", "-q", "--filter",
                      "label=com.docker.compose.project=ov-http").split()
    networks.extend(docker("network", "ls", "-q", "--filter", "name=^ov-http_default$").split())
    for ident in set(networks):
        obj = json.loads(docker("network", "inspect", ident))[0]
        labels = obj.get("Labels", {}) or {}
        need(obj["Name"] == "ov-http_default" and labels.get(OV_LABEL) == "v1")
        need(labels.get("com.docker.compose.project") == "ov-http")


def main():
    module = AnsibleModule(argument_spec={
        "kind": {"type": "str", "choices": ["endpoint", "credential", "route", "binary", "ov-package", "ov-files", "ov-runtime"],
                 "required": True},
        "service": {"type": "str", "choices": [*SERVICES, "ov-http"]},
        "uid": {"type": "int"},
        "gid": {"type": "int"},
        "sha256": {"type": "str"},
        "credential_names": {"type": "list", "elements": "str", "default": []},
        "source": {"type": "path", "no_log": True},
        "release": {"type": "str"},
    }, supports_check_mode=True)
    p = module.params
    try:
        if p["kind"] == "credential":
            need(p["uid"] is not None and p["source"])
            result = inspect(p["source"], {0, p["uid"]}, exact_mode=0o600, required=True)
            if result is None:
                raise ValueError("missing source")
            need(result.st_uid == p["uid"])
        elif p["kind"] in ("ov-package", "ov-files"):
            ov_package(p["release"], files_required=p["kind"] == "ov-files")
        elif p["kind"] == "ov-runtime":
            ov_runtime()
        else:
            need(p["service"] in (*SERVICES, "ov-http") if p["kind"] == "route"
                 else p["service"] in SERVICES)
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
    except (OSError, ValueError, TypeError, subprocess.SubprocessError, KeyError, IndexError):
        module.fail_json(msg="web-mcp protected preflight refused; inspect fixed paths and identity")
    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
