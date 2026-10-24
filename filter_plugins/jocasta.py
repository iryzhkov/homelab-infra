"""Opt-in HTTP MCP settings for the existing Jocasta Compose stack.

The four limited MCP settings remain all-or-none. A separate optional operator
trio requires that existing verifier/resource and an operator-capable source
image. Jocasta validates again at startup; this filter refuses wrong inventory
before host writes. Source allowlists are not image-publication or deployment
receipts. See docs/jocasta.md.
"""
import re

from ansible.errors import AnsibleFilterError

MCP_KEYS = (
    "JOCASTA_OIDC_ISSUER",
    "JOCASTA_OIDC_AUDIENCE",
    "JOCASTA_OIDC_JWKS_URL",
    "JOCASTA_MCP_RESOURCE_URL",
)
OPERATOR_KEYS = (
    "JOCASTA_OPERATOR_OIDC_ISSUER",
    "JOCASTA_OPERATOR_OIDC_JWKS_URL",
    "JOCASTA_OPERATOR_OIDC_CLIENT_ID",
)
KNOWN_KEYS = ("JOCASTA_IMAGE",) + MCP_KEYS + OPERATOR_KEYS

# Full Jocasta source commits whose images are accepted to serve HTTP MCP:
# the reviewed, published a3e83c5, whose application tree equals the
# qualified 232c5e3. This image supports only limited access, never operator.
# 37ce657 has no MCP support and is still refused.
MCP_IMAGE_REVISIONS = frozenset({"a3e83c55e73661fc50d92a9ca328236a86f291fd"})

# Intended operator source pin. Unit/race/cross checks passed at this source;
# final reviews, hosted CI, checked image publication and deployment are PENDING.
# Root must reconcile the final reviewed source and image artifact before applying.
# Inventory remains on the existing limited-only image with no operator settings.
OPERATOR_IMAGE_REVISIONS = frozenset({"acd4ba57001559ef0e795ccf47f34413c9420c59"})
OPERATOR_IMAGE_REPOSITORY = "git.ryzhkov.dev/igor/jocasta"
OPERATOR_ISSUER = "https://auth.ryzhkov.dev/application/o/jocasta-laptop-interactive/"
OPERATOR_JWKS = OPERATOR_ISSUER + "jwks/"
OPERATOR_CLIENT_ID = "jocasta-laptop-interactive"

# The canonical MCP resource. Its HTTPS route is the separately managed
# web_mcp_route service "jocasta" (filter_plugins/web_mcp.py JOCASTA_ROUTE),
# not this stack's browser route.
MCP_RESOURCE = "https://jocasta.mcp.ryzhkov.dev/mcp"

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = _LABEL + r"(?:\." + _LABEL + r")*"
_SEGMENT = r"/[A-Za-z0-9._~-]+"
ISSUER = re.compile(r"https://(" + _HOST + r")((?:" + _SEGMENT + r")*/)")
JWKS = re.compile(r"https://(" + _HOST + r")((?:" + _SEGMENT + r")+/?)")
# Plain token characters only: the value lands unquoted in a Compose .env,
# where '$', '#', quotes and whitespace would be reinterpreted.
AUDIENCE = re.compile(r"[A-Za-z0-9._~:/-]{1,256}")
IMAGE = re.compile(r"[a-z0-9][a-z0-9./_-]*/jocasta:([0-9a-f]{40})(?:@sha256:[0-9a-f]{64})?")


def _require(ok, message):
    if not ok:
        raise AnsibleFilterError("jocasta-mcp: " + message)


def _url(pattern, value, what):
    _require(isinstance(value, str) and len(value) <= 2048, what + " must be a string")
    found = pattern.fullmatch(value)
    if found is None or len(found.group(1)) > 253:
        raise AnsibleFilterError("jocasta-mcp: " + what + " must be a canonical HTTPS URL"
                                 " without port, credentials, query or fragment")
    if (found.lastindex or 0) > 1:
        segments = found.group(2).strip("/").split("/")
        _require(not {".", ".."} & set(segments), what + " must not contain dot segments")
    return found.group(1)


def jocasta_mcp_env(env_extra):
    """Return validated public MCP settings; absent/all-empty operator trio is off."""
    _require(isinstance(env_extra, dict), "env_extra must be a mapping")
    unknown = sorted(set(env_extra) - set(KNOWN_KEYS))
    _require(not unknown, "unknown env_extra keys " + ", ".join(unknown))
    operator_present = [key for key in OPERATOR_KEYS if key in env_extra]
    _require(not operator_present or len(operator_present) == len(OPERATOR_KEYS),
             "set all of " + ", ".join(OPERATOR_KEYS) + " or none of them")
    operator_on = bool(operator_present) and not all(env_extra[key] == "" for key in OPERATOR_KEYS)
    present = [key for key in MCP_KEYS if key in env_extra]
    _require(not operator_on or present, "operator needs the existing limited MCP verifier and resource")
    if not present:
        return {}
    _require(len(present) == len(MCP_KEYS),
             "set all of " + ", ".join(MCP_KEYS) + " or none of them")

    _require(env_extra["JOCASTA_MCP_RESOURCE_URL"] == MCP_RESOURCE,
             "MCP resource must be exactly " + MCP_RESOURCE)
    issuer_host = _url(ISSUER, env_extra["JOCASTA_OIDC_ISSUER"], "OIDC issuer (with trailing slash)")
    jwks_host = _url(JWKS, env_extra["JOCASTA_OIDC_JWKS_URL"], "OIDC JWKS URL")
    _require(jwks_host == issuer_host, "OIDC JWKS URL must be on the issuer's host")
    audience = env_extra["JOCASTA_OIDC_AUDIENCE"]
    _require(isinstance(audience, str) and AUDIENCE.fullmatch(audience) is not None,
             "OIDC audience must be 1..256 plain token characters")

    image = env_extra.get("JOCASTA_IMAGE")
    found = IMAGE.fullmatch(image) if isinstance(image, str) else None
    _require(found is not None and found.group(1) in (MCP_IMAGE_REVISIONS | OPERATOR_IMAGE_REVISIONS),
             "MCP needs a jocasta:<commit> image from an accepted MCP-capable source commit")
    settings = {key: env_extra[key] for key in MCP_KEYS}
    if operator_on:
        _require(env_extra["JOCASTA_OPERATOR_OIDC_ISSUER"] == OPERATOR_ISSUER,
                 "operator issuer must be exactly " + OPERATOR_ISSUER)
        _require(env_extra["JOCASTA_OPERATOR_OIDC_JWKS_URL"] == OPERATOR_JWKS,
                 "operator JWKS must be exactly " + OPERATOR_JWKS)
        _require(env_extra["JOCASTA_OPERATOR_OIDC_CLIENT_ID"] == OPERATOR_CLIENT_ID,
                 "operator client ID must be exactly " + OPERATOR_CLIENT_ID)
        _require(issuer_host == "auth.ryzhkov.dev",
                 "operator and limited issuers must use the fixed Authentik origin")
        _require(env_extra["JOCASTA_OIDC_ISSUER"] != OPERATOR_ISSUER,
                 "operator issuer must be distinct from the limited issuer")
        _require(found.group(1) in OPERATOR_IMAGE_REVISIONS
                 and image.split("@", 1)[0] == OPERATOR_IMAGE_REPOSITORY + ":" + found.group(1),
                 "operator needs the exact accepted operator-capable image source and repository")
        settings.update({key: env_extra[key] for key in OPERATOR_KEYS})
    return settings


class FilterModule:
    def filters(self):
        return {"jocasta_mcp_env": jocasta_mcp_env}
