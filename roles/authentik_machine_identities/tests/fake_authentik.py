"""A small in-memory Authentik API for the machine identity role's tests.

It models only what the role touches, following the 2026.8.4 sources: stored
blueprints applied entry by entry (with !Find and !KeyOf), users, groups,
app-password tokens, one OAuth2 provider kind, scope mappings, applications,
policy bindings, keypairs, check_access and view_key. Every request is
recorded, so a test can prove what a run did not do.
"""
from base64 import b64encode, urlsafe_b64encode
import hashlib
import http.server
import importlib.util
from pathlib import Path
import json
import secrets
import ssl
import threading
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

# Reuse the managed-inventory seam's pinned public/secret serializer behavior.
_seam_path = Path(__file__).resolve().parents[2] / "authentik_webmcp_interactive/tests/provider_inventory_seam.py"
_seam_spec = importlib.util.spec_from_file_location("machine_provider_inventory_seam", _seam_path)
assert _seam_spec is not None and _seam_spec.loader is not None
provider_seam = importlib.util.module_from_spec(_seam_spec)
_seam_spec.loader.exec_module(provider_seam)

ADMIN = "FAKE-ADMIN-TOKEN-NEVER-PRINT"
KEY_PREFIX = "FAKEAPPPASSWORD"
KEYPAIR = "0b6e3f4a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


class Loader(yaml.SafeLoader):
    pass


Loader.add_constructor("!Find", lambda loader, node: ("Find", loader.construct_sequence(node, deep=True)))
Loader.add_constructor("!KeyOf", lambda loader, node: ("KeyOf", loader.construct_scalar(node)))


def page(rows):
    return {"pagination": {"count": len(rows), "current": 1, "total_pages": 1}, "results": rows, "autocomplete": {}}


def signing_jwk(private_key, certificate=None):
    """2026.8.4 JWKSView: key/alg from PRIVATE KEY, optional x5c from cert."""
    public = private_key.public_key()
    def uint(value, minimum=0):
        return urlsafe_b64encode(value.to_bytes(max(minimum, (value.bit_length() + 7) // 8), "big")).decode().rstrip("=")
    key = {"kid": "synthetic-signing-key", "use": "sig"}
    if isinstance(private_key, rsa.RSAPrivateKey):
        numbers = public.public_numbers()
        key |= {"kty": "RSA", "alg": "RS256", "n": uint(numbers.n), "e": uint(numbers.e)}
    elif isinstance(private_key, ec.EllipticCurvePrivateKey):
        numbers = public.public_numbers()
        key |= {"kty": "EC", "alg": "ES256", "crv": "P-256", "x": uint(numbers.x, 32), "y": uint(numbers.y, 32)}
    elif isinstance(private_key, ed25519.Ed25519PrivateKey):
        key |= {"kty": "OKP", "alg": "EdDSA", "crv": "Ed25519",
                "x": urlsafe_b64encode(public.public_bytes_raw()).decode().rstrip("=")}
    else:
        raise ValueError("unsupported synthetic private key")
    if certificate is not None:
        key["x5c"] = [b64encode(certificate.public_bytes(serialization.Encoding.DER)).decode()]
    return key


class ImportError_(Exception):
    pass


class Authentik:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests = []
        self.blueprints = {}
        self.users = {"igor": {"pk": 1, "username": "igor", "type": "internal", "is_active": True,
                               "attributes": {}, "path": "users", "roles": []}}
        self.groups = {"family": {"pk": str(uuid.uuid4()), "name": "family", "is_superuser": False,
                                  "parents": [], "roles": [], "users": ["igor"]}}
        self.tokens = {}
        self.mappings = {}
        self.providers = {}
        self.apps = {"budget": {"pk": str(uuid.uuid4()), "slug": "budget", "name": "Budget", "provider": None,
                                "policy_engine_mode": "any", "backchannel_providers": [], "bindings": []}}
        self.apps["budget"]["bindings"].append(self._binding(self.apps["budget"], self.groups["family"]["pk"]))
        self.keypairs = {KEYPAIR: {"pk": KEYPAIR, "name": "authentik Self-signed Certificate",
                                   "private_key_available": True, "key_type": "rsa"}}
        self.jwks = {"keys": [signing_jwk(rsa.generate_private_key(public_exponent=65537, key_size=2048))]}
        self.public_authorization = []
        self.public_without_length = False
        self.next_pk = 100
        self.responses = {}
        self.redirects = {}
        self.apply_reads = 0
        self.pending = None
        self.apply_failure = False
        self.on_pending = lambda: None
        self.detail_reads = 0

    # -- model ---------------------------------------------------------------
    def _pk(self):
        self.next_pk += 1
        return self.next_pk

    @staticmethod
    def _binding(app, group):
        return {"pk": str(uuid.uuid4()), "target": app["pk"], "group": group, "user": None,
                "policy": None, "enabled": True, "negate": False, "order": 0}

    def add_user(self, username, **fields):
        self.users[username] = {"pk": self._pk(), "username": username, "type": "internal", "is_active": True,
                                "attributes": {}, "path": "users", "roles": []} | fields

    def _user_view(self, user):
        groups = [g["pk"] for g in self.groups.values() if user["username"] in g["users"]]
        superuser = any(g["is_superuser"] for g in self.groups.values() if user["username"] in g["users"])
        return {k: v for k, v in user.items()} | {"groups": groups, "is_superuser": superuser}

    def _group_view(self, group):
        return {k: v for k, v in group.items() if k != "users"} | {
            "users_obj": [{"username": u, "pk": self.users[u]["pk"]} for u in group["users"] if u in self.users]}

    def _token_view(self, token):
        return {k: v for k, v in token.items() if k not in ("key", "user")} | {
            "user": self.users[token["user"]]["pk"], "user_obj": {"username": token["user"]}}

    def _app_view(self, app):
        provider = next((p for p in self.providers.values() if p["pk"] == app["provider"]), None)
        return {k: v for k, v in app.items() if k != "bindings"} | {
            "provider_obj": {"name": provider["name"]} if provider else None}

    def passing(self, app, user):
        if not app["bindings"]:
            return True
        for b in app["bindings"]:
            if not b["enabled"]:
                continue
            group = next((g for g in self.groups.values() if g["pk"] == b["group"]), None)
            hit = (group and user["username"] in group["users"]) or b["user"] == user["pk"]
            if hit != b["negate"]:
                return True
        return False

    # -- blueprints ----------------------------------------------------------
    def parse(self, content):
        try:
            doc = yaml.load(content, Loader=Loader)
        except yaml.YAMLError as exc:
            raise ImportError_(str(exc)) from None
        if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("entries"), list):
            raise ImportError_("not a blueprint")
        return doc

    def apply(self, content):
        ids = {}

        def resolve(value):
            if isinstance(value, tuple) and value[0] == "KeyOf":
                if value[1] not in ids:
                    raise ImportError_("unknown KeyOf " + value[1])
                return ids[value[1]]
            if isinstance(value, tuple) and value[0] == "Find":
                model, (field, wanted) = value[1]
                if model == "authentik_crypto.certificatekeypair":
                    return wanted if wanted in self.keypairs else None
                return "flow:" + wanted
            if isinstance(value, list):
                return [resolve(v) for v in value]
            return value

        for entry in self.parse(content)["entries"]:
            model, state = entry["model"], entry.get("state", "present")
            ident = {k: resolve(v) for k, v in entry.get("identifiers", {}).items()}
            attrs = {k: resolve(v) for k, v in (entry.get("attrs") or {}).items()}
            key = self._apply_entry(model, state, ident, attrs)
            if "id" in entry and key is not None:
                ids[entry["id"]] = key

    def _apply_entry(self, model, state, ident, attrs):
        if model == "authentik_providers_oauth2.scopemapping":
            name = ident["name"]
            if state == "absent":
                self.mappings.pop(name, None)
                return None
            m = self.mappings.setdefault(name, {"pk": str(uuid.uuid4()), "name": name})
            m.update(attrs)
            return m["pk"]
        if model == "authentik_providers_oauth2.oauth2provider":
            name = ident["name"]
            if state == "absent":
                self.providers.pop(name, None)
                return None
            p = self.providers.setdefault(name, {"pk": self._pk(), "name": name, "client_secret": secrets.token_hex(16)})
            p["meta_model_name"] = "authentik_providers_oauth2.oauth2provider"
            p.update({k: v for k, v in attrs.items() if k not in ("authorization_flow", "invalidation_flow")})
            return p["pk"]
        if model == "authentik_core.application":
            slug = ident["slug"]
            if state == "absent":
                self.apps.pop(slug, None)
                return None
            a = self.apps.setdefault(slug, {"pk": str(uuid.uuid4()), "slug": slug,
                                             "backchannel_providers": [], "bindings": []})
            a.update({k: v for k, v in attrs.items() if k != "meta_launch_url"})
            return a["pk"]
        if model == "authentik_core.user":
            username = ident["username"]
            if state == "absent":
                if username in self.users:
                    del self.users[username]
                    self.tokens = {k: t for k, t in self.tokens.items() if t["user"] != username}
                    for g in self.groups.values():
                        g["users"] = [u for u in g["users"] if u != username]
                return None
            if username not in self.users:
                self.add_user(username)
            self.users[username].update(attrs)
            return username
        if model == "authentik_core.token":
            identifier = ident["identifier"]
            if state == "absent":
                self.tokens.pop(identifier, None)
                return None
            if "user" not in attrs or "intent" not in attrs:
                raise ImportError_("Missing user or intent")
            user = self.users[attrs["user"]]
            existing = self.tokens.get(identifier)
            if existing and existing["user"] != attrs["user"]:
                raise ImportError_("User cannot be changed")
            limit = user["attributes"].get("goauthentik.io/user/token-maximum-lifetime", "minutes=30")
            unit, amount = limit.split("=")
            ceiling = datetime.now(timezone.utc) + timedelta(**{unit: int(amount)})
            if datetime.fromisoformat(attrs["expires"].replace("Z", "+00:00")) > ceiling:
                raise ImportError_("Token expires exceeds maximum lifetime")
            token = existing or {"identifier": identifier, "key": KEY_PREFIX + secrets.token_hex(16)}
            token.update(attrs)
            self.tokens[identifier] = token
            return identifier
        if model == "authentik_core.group":
            name = ident["name"]
            if state == "absent":
                self.groups.pop(name, None)
                return None
            g = self.groups.setdefault(name, {"pk": str(uuid.uuid4()), "name": name, "users": []})
            g.update(attrs)
            return g["pk"]
        if model == "authentik_policies.policybinding":
            app = next(a for a in self.apps.values() if a["pk"] == ident["target"])
            if not any(b["group"] == ident["group"] for b in app["bindings"]):
                app["bindings"].append(self._binding(app, ident["group"]))
            for b in app["bindings"]:
                if b["group"] == ident["group"]:
                    b.update({k: v for k, v in attrs.items() if k in ("order", "enabled", "negate")})
            return None
        raise ImportError_("unsupported model " + model)

    def complete_apply(self):
        instance = self.pending
        self.pending = None
        try:
            if self.apply_failure:
                raise ImportError_("synthetic worker failure")
            self.apply(instance["content"])
            instance["status"] = "successful"
            instance["last_applied_hash"] = hashlib.sha512(instance["content"].encode()).hexdigest()
        except (ImportError_, KeyError, StopIteration) as exc:
            instance["status"] = "error"
            instance["error"] = str(exc)
        instance["last_applied"] = datetime.now(timezone.utc).isoformat()

    # -- HTTP ----------------------------------------------------------------
    def handle(self, method, raw_path, body):
        parts = urlsplit(raw_path)
        path = unquote(parts.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.requests.append((method, path, query))
        if path.startswith("/application/o/"):
            response = self.responses.get((method, path))
            if response is not None:
                return response() if callable(response) else response
            slug = path.split("/")[3]
            app = self.apps.get(slug)
            if (method != "GET" or path != f"/application/o/{slug}/jwks/" or not app
                    or not any(p["pk"] == app["provider"] for p in self.providers.values())):
                return 404, {"detail": "Not found."}
            return 200, self.jwks
        assert path.startswith("/api/v3/")
        path = path[len("/api/v3"):]
        bp = "/managed/blueprints/"
        if (method, path) in self.responses:
            response = self.responses[(method, path)]
            response = response() if callable(response) else response
            if response is not None:
                return response
        if path == bp and method == "GET":
            return 200, page([b for b in self.blueprints.values() if query.get("name", "") in b["name"]])
        if path == bp and method == "POST":
            try:
                self.parse(body["content"])
            except ImportError_ as exc:
                return 400, {"content": [str(exc)]}
            pk = str(uuid.uuid4())
            self.blueprints[pk] = {"pk": pk, "name": body["name"], "content": body["content"].strip(),
                                   "status": "unknown", "enabled": True, "path": "", "context": {},
                                   "last_applied": datetime.now(timezone.utc).isoformat(),
                                   "last_applied_hash": ""}
            return 201, self.blueprints[pk]
        if path.startswith(bp) and path.count("/") == 4:
            pk = path.split("/")[3]
            if method == "PATCH":
                try:
                    self.parse(body["content"])
                except ImportError_ as exc:
                    return 400, {"content": [str(exc)]}
                self.blueprints[pk]["content"] = body["content"].strip()
                # auto_now updates on ordinary saves, without clearing old success/hash.
                self.blueprints[pk]["last_applied"] = datetime.now(timezone.utc).isoformat()
                return 200, self.blueprints[pk]
            self.detail_reads += 1
            if self.pending is not None:
                self.on_pending()
                if self.apply_reads == 0:
                    self.complete_apply()
                else:
                    self.apply_reads -= 1
            return 200, self.blueprints[pk]
        if path.startswith(bp) and path.endswith("/apply/") and method == "POST":
            instance = self.blueprints[path.split("/")[3]]
            self.pending = instance
            if self.apply_reads == 0:
                self.complete_apply()
            # The real endpoint returns the current instance, no task ID/reset.
            return 200, dict(instance)
        if method != "GET":
            return 405, {}
        if path == "/propertymappings/provider/scope/":
            return 200, page([m for m in self.mappings.values() if m["name"] == query.get("name")])
        if path == "/core/users/me/":
            return 200, {"user": {"is_superuser": True}}
        if path == "/providers/all/":
            return 200, page([provider_seam.public_provider(p) for p in self.providers.values()])
        if path.startswith("/providers/oauth2/") and path.count("/") == 4:
            pk = path.split("/")[3]
            rows = [p for p in self.providers.values() if str(p["pk"]) == pk]
            return (200, provider_seam.oauth_provider(rows[0])) if len(rows) == 1 else (404, {})
        if path == "/providers/oauth2/":
            rows = [provider_seam.oauth_provider(p) for p in self.providers.values()]
            for field in ("name", "client_id"):
                if field in query:
                    rows = [r for r in rows if r.get(field) == query[field]]
            return 200, page(rows)
        if path == "/core/applications/":
            rows = [self._app_view(a) for a in self.apps.values() if query.get("slug", a["slug"]) == a["slug"]]
            return 200, page(rows)
        if path.startswith("/core/applications/") and path.endswith("/check_access/"):
            app = self.apps[path.split("/")[3]]
            user = next(u for u in self.users.values() if str(u["pk"]) == query["for_user"])
            return 200, {"passing": self.passing(app, user), "messages": []}
        if path == "/core/groups/":
            return 200, page([self._group_view(g) for g in self.groups.values() if g["name"] == query.get("name")])
        if path == "/core/users/":
            return 200, page([self._user_view(u) for u in self.users.values() if u["username"] == query.get("username")])
        if path == "/core/tokens/":
            return 200, page([self._token_view(t) for t in self.tokens.values() if t["identifier"] == query.get("identifier")])
        if path.startswith("/core/tokens/") and path.endswith("/view_key/"):
            token = self.tokens.get(path.split("/")[3])
            return (200, {"key": token["key"]}) if token else (404, {"detail": "Not found."})
        if path.startswith("/crypto/certificatekeypairs/"):
            key = self.keypairs.get(path.split("/")[3])
            return (200, key) if key else (404, {"detail": "Not found."})
        if path == "/policies/bindings/":
            rows = [b for a in self.apps.values() for b in a["bindings"] if a["pk"] == query.get("target")]
            return 200, page(rows)
        return 404, {"detail": "Not found."}


def serve(state, cert, key):
    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self, method):
            public = urlsplit(self.path).path.startswith("/application/o/")
            if public:
                state.public_authorization.append(self.headers.get("Authorization"))
            redirect = state.redirects.get((method, urlsplit(self.path).path))
            if redirect:
                state.requests.append((method, urlsplit(self.path).path, {}))
                self.send_response(302)
                self.send_header("Location", redirect)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if not public and self.headers.get("Authorization") != "Bearer " + ADMIN:
                status, body = 403, {"detail": "denied"}
            else:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length)) if length else None
                with state.lock:
                    status, body = state.handle(method, self.path, payload)
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if not (public and state.public_without_length):
                self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLEOFError):
                pass

        def do_GET(self):
            self._respond("GET")

        def do_POST(self):
            self._respond("POST")

        def do_PATCH(self):
            self._respond("PATCH")

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
