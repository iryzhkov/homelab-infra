#!/usr/bin/python
"""Read only the configured provider's bounded public signing evidence."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
import json
import re
import signal
import ssl
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
module: authentik_machine_jwks
short_description: Refuse unsupported public Authentik provider signing evidence
description: Reads the validated provider JWKS without credentials or redirects.
options:
  jwks_url:
    description: Exact HTTPS JWKS URL from the role's validated service profile.
    required: true
    type: str
author: homelab-infra
"""
EXAMPLES = ""
RETURN = ""

MAX_BODY = 65536
DEADLINE_SECONDS = 8


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def signing_evidence(value):
    # 2026.8.4 JWKSView.get_keys yields the configured signing key and,
    # optionally, the encryption key. Select by use, never position, kid/UUID
    # guesses, x5c certificate type, or merely any RSA key in the set.
    if not isinstance(value, dict) or not isinstance(value.get("keys"), list):
        return False
    keys = value["keys"]
    if not 1 <= len(keys) <= 2 or any(not isinstance(k, dict) for k in keys):
        return False
    if any(k.get("use") not in ("sig", "enc") for k in keys):
        return False
    signers = [k for k in keys if k["use"] == "sig"]
    if len(signers) != 1:
        return False
    if any(any(field in k for field in ("d", "p", "q", "dp", "dq", "qi", "oth", "k")) for k in keys):
        return False
    key = signers[0]
    if key.get("kty") != "RSA" or key.get("alg") != "RS256":
        return False
    for field in ("n", "e"):
        text = key.get(field)
        if not isinstance(text, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
            return False
        try:
            raw = urlsafe_b64decode(text + "=" * (-len(text) % 4))
        except ValueError:
            return False
        if urlsafe_b64encode(raw).decode().rstrip("=") != text or int.from_bytes(raw, "big") == 0:
            return False
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_evidence(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
            or parts.query or parts.fragment or not parts.path.endswith("/jwks/")):
        raise ValueError()
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
    request = Request(url, headers={"Accept": "application/json", "Accept-Encoding": "identity"})
    with opener.open(request, timeout=DEADLINE_SECONDS) as response:
        if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
            raise ValueError()
        size = response.headers.get("Content-Length")
        if size is not None and (not size.isdecimal() or int(size) > MAX_BODY):
            raise ValueError()
        raw = response.read(MAX_BODY + 1)
    if len(raw) > MAX_BODY:
        raise ValueError()
    return signing_evidence(json.loads(raw.decode("utf-8"), object_pairs_hook=_unique))


def expired(*_):
    raise TimeoutError()


def main():
    module = AnsibleModule(argument_spec={"jwks_url": {"type": "str", "required": True}}, supports_check_mode=True)
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    try:
        accepted = fetch_evidence(module.params["jwks_url"])
    except Exception:
        # No URL, response body, remote error or cryptographic material escapes.
        accepted = False
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    if not accepted:
        module.fail_json(msg="Public provider signing evidence refused")
    module.exit_json(changed=False)


if __name__ == "__main__":
    main()
