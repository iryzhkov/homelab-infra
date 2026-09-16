#!/usr/bin/env python3
"""Generate SOPS host tokens and provision UpKeeper secret sources without printing secrets."""
import json
import os
from pathlib import Path
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ENCRYPTED = ROOT / "secrets/homelab-jocasta.sops.yaml"
HOSTS = {"laptop": "laptop", "homelab": "homelab", "normandy": "normandy", "omarchy-pc": "gaming-pc"}

def main():
    os.umask(0o077)
    if ENCRYPTED.exists():
        data = json.loads(subprocess.check_output(
            ["sops", "--decrypt", "--output-type", "json", str(ENCRYPTED)]))
    else:
        data = {"JOCASTA_TOKENS": {host: secrets.token_urlsafe(48) for host in HOSTS}}
        encrypted = subprocess.check_output(
            ["sops", "--encrypt", "--input-type", "json", "--output-type", "yaml",
             "--filename-override", str(ENCRYPTED), "/dev/stdin"],
            input=json.dumps(data).encode(), cwd=ROOT,
        )
        with ENCRYPTED.open("xb") as handle:
            handle.write(encrypted)
    for host, target in HOSTS.items():
        value = data["JOCASTA_TOKENS"][host].encode()
        relative = ".config/upkeeper/secrets/jocasta/" + host
        if target is None:
            destination = Path.home() / relative
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(value)
            destination.chmod(0o600)
        else:
            command = ("umask 077; mkdir -p ~/.config/upkeeper/secrets/jocasta; "
                       "cat > ~/" + relative + "; chmod 600 ~/" + relative)
            ssh = ["ssh", "-o", "BatchMode=yes", target, command]
            if host == "omarchy-pc":
                # Normandy holds the authorized fleet key for this host.
                import shlex
                ssh = ["ssh", "-o", "BatchMode=yes", "normandy", shlex.join(ssh)]
            subprocess.run(ssh, input=value, check=True)
        print(host + ": provisioned ~/" + relative + " mode0600")

if __name__ == "__main__":
    main()
