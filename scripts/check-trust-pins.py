#!/usr/bin/python3
"""Compare independent public pins before any host action. Never reads a private key."""
import base64
import hashlib
from pathlib import Path
import re
import sys

import yaml


def check(fleet, infra):
    f = yaml.safe_load((Path(fleet) / "inventory/group_vars/all.yml").read_text())
    h = yaml.safe_load((Path(infra) / "vars/chaldea-runtime.yml").read_text())
    for key in ("chaldea_host_version", "chaldea_host_sha256", "chaldea_node_version", "chaldea_access_paths",
                "chaldea_access_trust_repository", "chaldea_access_trust_bundle_path",
                "chaldea_access_trust_anchors", "chaldea_deployment_anchor_kid",
                "chaldea_deployment_anchor_public_jwk", "chaldea_deployment_anchor_spki_sha256"):
        if f[key] != h[key]:
            raise ValueError("fleet/infra pin mismatch: " + key)
    if (f["chaldea_access_trust_repository"] != "ssh://git@github.com/iryzhkov/chaldea-trust.git"
            or f["chaldea_access_trust_bundle_path"] != "bundle.jws"):
        raise ValueError("trust must use data-only chaldea-trust main:bundle.jws")
    expected = [{"kid": "{{ chaldea_deployment_anchor_kid }}",
                 "public_key": "{{ chaldea_deployment_anchor_public_jwk }}"}]
    if f["chaldea_access_trust_anchors"] != expected:
        raise ValueError("anchor mapping must resolve the independently compared public pin")
    if f["chaldea_access_trust_enabled"] is not False:
        raise ValueError("fleet enrollment must remain disabled")
    key = f["chaldea_deployment_anchor_public_jwk"]
    if not re.fullmatch(r"deployment-[0-9]{4}-(?:0[1-9]|1[0-2])[a-z]{1,8}", f["chaldea_deployment_anchor_kid"]):
        raise ValueError("unexpected deployment kid")
    x = key["x"]
    if key["kty"] != "OKP" or key["crv"] != "Ed25519" or not re.fullmatch(r"[A-Za-z0-9_-]{43}", x):
        raise ValueError("replace the new public JWK placeholder before deployment")
    raw = base64.urlsafe_b64decode(x + "=")
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != x:
        raise ValueError("noncanonical public JWK")
    digest = hashlib.sha256(bytes.fromhex("302a300506032b6570032100") + raw).hexdigest()
    if digest != f["chaldea_deployment_anchor_spki_sha256"]:
        raise ValueError("public JWK/SPKI SHA-256 mismatch")
    print("fleet/infra runtime, paths and public anchor match: " + digest)


if __name__ == "__main__":
    check(*sys.argv[1:])
