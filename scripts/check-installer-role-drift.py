#!/usr/bin/env python3
"""Check copied installer roles against a reviewed fleet commit, never floating main."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/iryzhkov/chaldea-fleet-config.git"
PIN = "886b48dbee492455d6998ea7357fc7884695fc16"
ROLES = ("roles/chaldea_access_trust", "roles/chaldea_host_artifact")
HANDLER = "roles/chaldea_access_trust/handlers/main.yml"
# Exact upstream bytes, with exactly one approved infra-only line appended.
FLEET_HANDLER = (b"---\n- name: Reload access trust units\n  become: true\n"
                 b"  ansible.builtin.systemd_service:\n    daemon_reload: true\n")
INFRA_HANDLER = FLEET_HANDLER + b"  when: not ansible_check_mode\n"


def fetch_reference(pin=PIN, repository=REPOSITORY):
    if not re.fullmatch(r"[0-9a-f]{40}", pin) or pin == "0" * 40:
        raise ValueError("reference pin must be a full nonzero lowercase commit SHA")
    with tempfile.TemporaryDirectory(dir=Path.home(), prefix="installer-role-reference-") as directory:
        def git(*args):
            return subprocess.run(
                ["git", "-C", directory, *args], check=True, capture_output=True,
                timeout=90, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}).stdout
        git("init", "--bare", "--quiet")
        git("fetch", "--depth=1", "--no-tags", repository, pin)
        if git("rev-parse", "FETCH_HEAD^{commit}").decode().strip() != pin:
            raise ValueError("fetched commit does not match reference pin")
        files = {}
        for entry in git("ls-tree", "-rz", pin, "--", *ROLES).split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            mode, kind, blob = metadata.split()
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise ValueError("reference contains a non-regular file: " + path.decode())
            files[path.decode()] = git("cat-file", "blob", blob.decode())
        if any(not any(p.startswith(role + "/") for p in files) for role in ROLES):
            raise ValueError("reference pin must contain both installer roles")
        return files


def compare(root, reference):
    local = {}
    for role in ROLES:
        directory = root / role
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("missing or non-directory role: " + role)
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ValueError("non-regular local path: " + str(path))
            if path.is_file():
                local[path.relative_to(root).as_posix()] = path.read_bytes()
            elif not path.is_dir():
                raise ValueError("non-regular local path: " + str(path))
    missing, extra = reference.keys() - local.keys(), local.keys() - reference.keys()
    if missing or extra:
        raise ValueError(f"installer role file set differs: missing={sorted(missing)}, extra={sorted(extra)}")
    if reference.get(HANDLER) != FLEET_HANDLER:
        raise ValueError("reference handler changed; review the explicit exception")
    for path, data in reference.items():
        expected = INFRA_HANDLER if path == HANDLER else data
        if local[path] != expected:
            raise ValueError("unexpected installer role content: " + path)


def main():
    try:
        reference = fetch_reference()
        compare(ROOT, reference)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("installer role drift check failed: " + str(error), file=sys.stderr)
        return 1
    print(f"{len(reference)} installer role files match fleet {PIN}; exact check-mode handler exception applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
