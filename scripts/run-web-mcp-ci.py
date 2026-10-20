#!/usr/bin/env python3
"""Run packaging fixtures in a fresh root-owned disposable CI container directory."""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess

FILES = (
    "ansible.cfg filter_plugins/web_mcp.py playbooks/library/web_mcp_guard.py "
    "playbooks/web-mcp-endpoint.yml playbooks/web-mcp-route.yml "
    "roles/web_mcp_endpoint/defaults/main.yml roles/web_mcp_endpoint/tasks/main.yml "
    "roles/web_mcp_endpoint/templates/endpoint.service.j2 "
    "roles/web_mcp_route/defaults/main.yml roles/web_mcp_route/tasks/main.yml "
    "roles/web_mcp_route/templates/route.yml.j2 roles/web_mcp_route/vars/main.yml "
    "scripts/test-web-mcp-packaging.py scripts/test-ov-http-packaging.py "
    "playbooks/ov-http.yml roles/ov_http_compose/defaults/main.yml "
    "roles/ov_http_compose/tasks/main.yml roles/ov_http_compose/templates/runtime.toml.j2 "
    "stacks/ov-http/compose.yml.j2"
).split()


def main():
    source = Path(__file__).resolve().parents[1]
    run, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    assert all(re.fullmatch(r"[1-9][0-9]{0,19}", value) for value in (run, attempt))
    cache = Path(os.environ.get("WEB_MCP_CI_FIXTURE_PARENT", "/var/cache"))
    assert cache.is_absolute() and ".." not in cache.parts and not cache.is_relative_to(source)
    root = cache / f"web-mcp-ci-{run}-{attempt}"
    os.environ["HOME"] = str(root / ".cache/web-mcp-tests/home")
    os.environ["TMPDIR"] = os.environ["HOME"] + "/tmp"
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    assert os.environ["HOME"] == f"{cache}/web-mcp-ci-{run}-{attempt}/.cache/web-mcp-tests/home"
    assert os.environ["TMPDIR"] == os.environ["HOME"] + "/tmp"
    assert os.geteuid() == 0 and os.environ.get("GITHUB_ACTIONS") == "true"
    for parent in list(reversed(cache.parents)) + [cache]:
        info = parent.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
    os.umask(0o077)
    root.mkdir(mode=0o700)  # Exclusive: existing directories, files and links all refuse.
    for relative in FILES:
        src, dst = source / relative, root / relative
        assert src.is_file() and not src.is_symlink() and src.resolve().is_relative_to(source)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst, follow_symlinks=False)
    Path(os.environ["TMPDIR"]).mkdir(parents=True)
    for script in ("test-web-mcp-packaging.py", "test-ov-http-packaging.py"):
        subprocess.run(["python3", "scripts/" + script], cwd=root, check=True)


if __name__ == "__main__":
    main()
