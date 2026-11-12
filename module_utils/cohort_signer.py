"""Approved cohort generation for root-wrapped enrollment only.

The wrapper owns cohort.lock for the entire Ansible invocation. Comparisons
retain every existing ownership/body check; only declared signer UUIDs from
the journal's history resolve to the approved current signer.
"""
import copy
import json
import os
import stat

PATH = "/etc/authentik-signer-generation.json"


def generation():
    if not os.path.exists(PATH):
        return None
    info = os.stat(PATH, follow_symlinks=False)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o644
            or os.environ.get("AUTHENTIK_SIGNER_ENROLLMENT") != "locked"):
        raise RuntimeError("use authentik-signer enrollment for active cohort enrollment")
    with open(PATH) as stream:
        value = json.load(stream)
    if value["phase"] != "idle":
        raise RuntimeError("finish signer recovery before enrollment")
    return value


def signer(slug, declared):
    g = generation()
    if not g:
        return declared
    if slug not in {p["slug"] for p in g["contracts"]}:
        raise RuntimeError("provider not in approved signer cohort")
    if declared not in g.get("history", []) + [g["current"]["pk"]]:
        raise RuntimeError("foreign signer refused")
    return g["current"]["pk"]


def body(pk, declared):
    g = generation()
    if not g:
        return declared
    providers = [p for p in g["contracts"] if p["pk"] == pk]
    if len(providers) != 1 or "signing_key" not in declared:
        return declared
    result = copy.deepcopy(declared)
    result["signing_key"] = signer(providers[0]["slug"], result["signing_key"])
    return result


def receipt(value):
    result = copy.deepcopy(value)
    def visit(node):
        if isinstance(node, dict):
            if "body" in node and "pk" in node and "signing_key" in node["body"]:
                node["body"] = body(node["pk"], node["body"])
            for item in node.values():
                visit(item)
        elif isinstance(node, list):
            for item in node:
                visit(item)
    visit(result)
    return result
