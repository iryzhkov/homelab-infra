"""Opt-in HTTP MCP settings for the existing Jocasta Compose stack.

The jocasta stack's env_extra names either none of the four MCP keys, which
leaves the rendered .env exactly as before, or all four with canonical values.
Jocasta validates them again at startup; this filter refuses a wrong inventory
before anything on the host is written. See docs/jocasta.md.
"""
import re

from ansible.errors import AnsibleFilterError

MCP_KEYS = (
    "JOCASTA_OIDC_ISSUER",
    "JOCASTA_OIDC_AUDIENCE",
    "JOCASTA_OIDC_JWKS_URL",
    "JOCASTA_MCP_RESOURCE_URL",
)
KNOWN_KEYS = ("JOCASTA_IMAGE",) + MCP_KEYS

# Full Jocasta source commits whose images are accepted to serve HTTP MCP:
# the reviewed, published a3e83c5, whose application tree equals the
# qualified 232c5e3. Every other image is refused, including the deployed
# 37ce657 pin, which has no MCP support.
MCP_IMAGE_REVISIONS = frozenset({"a3e83c55e73661fc50d92a9ca328236a86f291fd"})

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
    """Return the four MCP variables for the .env, or {} when MCP is off."""
    _require(isinstance(env_extra, dict), "env_extra must be a mapping")
    unknown = sorted(set(env_extra) - set(KNOWN_KEYS))
    _require(not unknown, "unknown env_extra keys " + ", ".join(unknown))
    present = [key for key in MCP_KEYS if key in env_extra]
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
    _require(found is not None and found.group(1) in MCP_IMAGE_REVISIONS,
             "MCP needs a jocasta:<commit> image from an accepted MCP-capable source commit")
    return {key: env_extra[key] for key in MCP_KEYS}


class FilterModule:
    def filters(self):
        return {"jocasta_mcp_env": jocasta_mcp_env}
