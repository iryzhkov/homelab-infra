#!/usr/bin/env python3
"""Fetch public nginx test artifacts into scratch when no nginx is installed."""
import hashlib
import lzma
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import urllib.request

home = Path(os.environ["HOME"])
assert str(home).startswith("/work/.cache/")
if not shutil.which("nginx"):
    destination = home / "nginx"
    destination.mkdir(parents=True, exist_ok=True)
    origin = "https://deb.debian.org/debian/"
    index = lzma.decompress(urllib.request.urlopen(
        origin + "dists/bookworm/main/binary-amd64/Packages.xz", timeout=60).read()).decode()
    packages = {}
    for record in index.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in record.splitlines() if ": " in line and not line.startswith(" "))
        if fields.get("Package") in ("nginx", "libcrypt1", "libpcre2-8-0", "libssl3", "zlib1g", "libnss-wrapper"):
            packages[fields["Package"]] = fields
    assert len(packages) == 6
    for name, fields in packages.items():
        raw = urllib.request.urlopen(origin + fields["Filename"], timeout=60).read()
        assert hashlib.sha256(raw).hexdigest() == fields["SHA256"]
        package = destination / (name + ".deb")
        package.write_bytes(raw)
        subprocess.run(["dpkg-deb", "-x", str(package), str(destination)], check=True)
    binary = home / "bin/nginx"
    binary.parent.mkdir(parents=True, exist_ok=True)
    libraries = destination / "usr/lib/x86_64-linux-gnu"
    # Debian bookworm also uses /lib for some library packages.
    library_path = str(libraries) + ":" + str(destination / "lib/x86_64-linux-gnu")
    (home / "passwd").write_text("root:x:0:0:fixture:" + str(home) + ":/bin/sh\n")
    (home / "group").write_text("root:x:0:\n")
    binary.write_text("#!/bin/sh\nNSS_WRAPPER_PASSWD=" + shlex.quote(str(home / "passwd")) +
                      " NSS_WRAPPER_GROUP=" + shlex.quote(str(home / "group")) +
                      " LD_PRELOAD=" + shlex.quote(str(libraries / "libnss_wrapper.so")) +
                      " LD_LIBRARY_PATH=" + shlex.quote(library_path) + " exec " +
                      shlex.quote(str(destination / "usr/sbin/nginx")) + ' "$@"\n')
    binary.chmod(0o755)
