#!/usr/bin/python
"""Closed controller-side Authentik enrollment. No runtime token issuance.

Uses API-returned IDs, an exclusive private receipt journal, and exact readbacks.
A pending mutation is never replayed or adopted by name. Root reconciles it.
"""
import copy
import datetime as dt
import fcntl
import json
import os
import re
import stat
import uuid
import urllib.error
import urllib.parse
import urllib.request

from ansible.module_utils.basic import AnsibleModule

ORIGIN = "https://auth.ryzhkov.dev"
API = ORIGIN + "/api/v3"
CLIENT = "jocasta-managed-interactive"
RESOURCE = "https://jocasta.mcp.ryzhkov.dev/mcp"
ISSUER = ORIGIN + "/application/o/" + CLIENT + "/"
SIGNING_KEY = "8bbd6516-af33-4e64-a73b-caa39e692c29"
HOSTS = {"laptop", "agent-a", "agent-b", "omarchy-pc"}
PATHS = {
    "user": "/core/users/", "mapping": "/propertymappings/provider/scope/",
    "provider": "/providers/oauth2/", "all_provider": "/providers/all/",
    "policy": "/policies/expression/",
    "application": "/core/applications/", "binding": "/policies/bindings/",
    "token": "/core/tokens/",
}


class Refused(Exception):
    pass


def require(ok):
    if not ok:
        raise Refused()


def stable(value):
    if isinstance(value, list):
        return sorted((stable(x) for x in value), key=lambda x: json.dumps(x, sort_keys=True))
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items()}
    return value


def matches(seen, body):
    for k, v in body.items():
        got = seen.get(k)
        if k == "expires":
            try:
                if timestamp(got) != timestamp(v):
                    return False
            except Exception:
                return False
        elif stable(got) != stable(v):
            return False
    return True


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"))
    return dt.datetime.fromisoformat(value[:-1] + "+00:00")


def declaration(hosts, owner, qualified, now):
    require(str(uuid.UUID(owner)) == owner)
    require(isinstance(hosts, list) and 1 <= len(hosts) <= 4)
    seen = set()
    for item in hosts:
        require(isinstance(item, dict) and set(item) == {"host", "generation", "expires", "state"})
        host, generation = item["host"], item["generation"]
        require(host in HOSTS and host not in seen)
        require(host == "laptop" or qualified)
        require(type(generation) is int and 1 <= generation <= 1000)
        require(item["state"] in ("present", "absent"))
        expiry = timestamp(item["expires"])
        if item["state"] == "present":
            require(now + dt.timedelta(days=1) <= expiry <= now + dt.timedelta(days=180))
        seen.add(host)
    return hosts


def expressions(users, owner):
    # Keys are exact API-returned integer PKs, values exact enrolled usernames.
    allowed = {str(u["pk"]): u["username"] for u in users}
    identity = (
        f"allowed = {allowed!r}\n"
        "u = user\n"
        "if u is None or not u.is_active or u.type != 'service_account':\n    return {}\n"
        "if allowed.get(str(u.pk)) != u.username:\n    return {}\n"
        f"if u.path != 'homelab-infra/webmcp-interactive' or u.attributes.get('webmcp-interactive-owner') != {owner!r}:\n    return {{}}\n"
        f"if provider.client_id != {CLIENT!r} or provider.application.slug != {CLIENT!r}:\n    return {{}}\n"
        f"return {{'aud': {RESOURCE!r}, 'jocasta_operator': True, 'webmcp_identity': u.username}}"
    )
    policy = (
        f"allowed = {allowed!r}\n"
        "u = request.user\n"
        "if u is None or not u.is_active or u.type != 'service_account':\n    return False\n"
        "if allowed.get(str(u.pk)) != u.username:\n    return False\n"
        f"if u.path != 'homelab-infra/webmcp-interactive' or u.attributes.get('webmcp-interactive-owner') != {owner!r}:\n    return False\n"
        "if u.is_superuser or u.groups.exists() or u.roles.exists():\n    return False\n"
        "if u.attributes.get('goauthentik.io/user/generated', False):\n    return False\n"
        "http = request.http_request\n"
        "if http is None or http.method != 'POST':\n    return False\n"
        "p = http.POST\n"
        f"return (p.get('client_id') == {CLIENT!r} and p.get('username') == u.username\n"
        "    and bool(p.get('password')) and not p.get('client_secret')\n"
        "    and not p.get('client_assertion') and not p.get('client_assertion_type')\n"
        "    and p.get('grant_type') == 'client_credentials'\n"
        f"    and p.get('resource') == {RESOURCE!r}\n"
        "    and len(p.getlist('resource')) == 1\n"
        "    and request.context.get('oauth_grant_type') == 'client_credentials'\n"
        "    and request.context.get('oauth_scopes') == {'jocasta.read', 'jocasta.write'}\n"
        "    and len(p.get('scope', '').split()) == 2\n"
        "    and set(p.get('scope', '').split()) == {'jocasta.read', 'jocasta.write'})"
    )
    return identity, policy


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Refused()


class APIClient:
    def __init__(self, token):
        require(isinstance(token, str) and 16 <= len(token) <= 4096 and "\n" not in token and "\r" not in token)
        self.token = token
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None, public=False):
        require(isinstance(path, str) and path.startswith("/") and not path.startswith("//"))
        endpoint = ORIGIN + path if public else API + path
        data = None if body is None else json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if not public:
            headers["Authorization"] = "Bearer " + self.token
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=8) as response:
                require(response.status in (200, 201, 204))
                raw = response.read(2 * 1024 * 1024 + 1)
                require(len(raw) <= 2 * 1024 * 1024)
                return json.loads(raw) if raw else {}
        except Exception:
            raise Refused() from None

    def rows(self, kind):
        # Complete bounded lists are required; no silent pagination or omissions.
        path = PATHS[kind] + "?page_size=500"
        if kind == "application":
            path += "&superuser_full_list=true"
        result = self.request("GET", path)
        require(isinstance(result, dict) and isinstance(result.get("results"), list))
        rows = result["results"]
        require(len(rows) <= 500 and not result.get("pagination", {}).get("next"))
        require(result.get("pagination", {}).get("count") == len(rows))
        return rows


def secure_dir(path):
    require(isinstance(path, str) and path.startswith("/") and os.path.normpath(path) == path)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path[1:].split("/"):
            st = os.fstat(fd)
            require(st.st_uid in (0, os.getuid()) and not st.st_mode & 0o022)
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        st = os.fstat(fd)
        require(st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) == 0o700)
        return fd
    except Exception:
        os.close(fd)
        raise Refused() from None


def read_private(fd, name):
    leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    with os.fdopen(leaf) as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size <= 1048576)
        raw = stream.read(1048577)
        require(len(raw) <= 1048576)
        return json.loads(raw)


def write_private(fd, name, value):
    # Only a named receipt or a fixed host file in a held private directory.
    try:
        read_private(fd, name)
    except FileNotFoundError:
        pass
    temp = ".new-" + str(uuid.uuid4())
    leaf = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
    with os.fdopen(leaf, "w") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.rename(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


class Enrollment:
    def __init__(self, api, fd, owner):
        self.api, self.fd, self.owner = api, fd, owner
        try:
            self.receipt = read_private(fd, "receipt.json")
            require(set(self.receipt) == {"version", "owner", "objects", "pending", "generations"})
            require(self.receipt["version"] == 1 and self.receipt["owner"] == owner)
            require(self.receipt["pending"] is None)
        except FileNotFoundError:
            # A nonempty directory without its original receipt cannot bootstrap.
            require(set(os.listdir(fd)) <= {"lock"})
            self.receipt = {"version": 1, "owner": owner, "objects": {}, "pending": None, "generations": {}}
            self.save()
        self.changed = False
        self.rows = {k: api.rows(k) for k in PATHS}
        self.verify_owned()

    def save(self):
        write_private(self.fd, "receipt.json", self.receipt)

    def detail(self, record):
        return PATHS[record["kind"]] + urllib.parse.quote(str(record["lookup"]), safe="") + "/"

    def verify_owned(self):
        for key, record in self.receipt["objects"].items():
            found = [x for x in self.rows[record["kind"]] if x.get("pk") == record["pk"]]
            require(len(found) == 1 and matches(found[0], record["body"]))
            if record["kind"] == "user":
                require(found[0].get("is_superuser") is False)

    def ensure(self, key, kind, body, field):
        record = self.receipt["objects"].get(key)
        found = [x for x in self.rows[kind] if x.get(field) == body[field]]
        if record is None:
            # No name-only adoption, including a prior uncertain POST.
            require(not found)
            method, path = "POST", PATHS[kind]
        else:
            require(record["kind"] == kind and len(found) == 1 and found[0].get("pk") == record["pk"])
            require(matches(found[0], record["body"]))
            if matches(found[0], body):
                return found[0]
            method, path = "PATCH", self.detail(record)
        self.receipt["pending"] = {"key": key, "method": method, "path": path, "body": body}
        self.save()
        result = self.api.request(method, path, body)
        require(isinstance(result, dict) and result.get("pk") is not None and matches(result, body))
        if record is not None:
            require(result["pk"] == record["pk"])
        # Persist only public declared fields and actual IDs, never API extras.
        lookup = body[field] if kind in ("token", "application") else result["pk"]
        new = {"kind": kind, "pk": result["pk"], "lookup": lookup, "body": copy.deepcopy(body)}
        self.receipt["objects"][key] = new
        if kind == "token":
            _, host, generation = key.split(":")
            self.receipt["generations"][host] = max(int(generation), self.receipt["generations"].get(host, 0))
        self.receipt["pending"] = None
        self.save()
        self.rows[kind] = [x for x in self.rows[kind] if x.get("pk") != result["pk"]] + [result]
        self.changed = True
        return result

    def remove(self, key):
        record = self.receipt["objects"].get(key)
        if record is None:
            return
        # Presence was bound to original actual PK/body before any writes.
        self.receipt["pending"] = {"key": key, "method": "DELETE", "path": self.detail(record)}
        self.save()
        self.api.request("DELETE", self.detail(record))
        self.rows[record["kind"]] = [x for x in self.rows[record["kind"]] if x.get("pk") != record["pk"]]
        del self.receipt["objects"][key]
        self.receipt["pending"] = None
        self.save()
        self.changed = True

    def no_foreign_references(self):
        owned = self.receipt["objects"]
        apps, bindings = self.rows["application"], self.rows["binding"]
        if "application" in owned:
            app = owned["application"]["pk"]
            allowed = {v["pk"] for v in owned.values() if v["kind"] == "binding"}
            require(all(b.get("target") != app or b["pk"] in allowed for b in bindings))
        if "provider" in owned:
            provider = owned["provider"]["pk"]
            app_ids = {v["pk"] for v in owned.values() if v["kind"] == "application"}
            require(all(a.get("provider") != provider or a["pk"] in app_ids for a in apps))
        mapping_ids = {v["pk"] for v in owned.values() if v["kind"] == "mapping"}
        provider_ids = {v["pk"] for v in owned.values() if v["kind"] == "provider"}
        # Scope mappings belong to the common Provider model, including proxy
        # providers. Keep mutations OAuth2-only, but require complete cross-type
        # reference evidence before every convergence and credential retrieval.
        providers = self.rows["all_provider"]
        require(all(isinstance(p, dict) and type(p.get("pk")) is int
                    and isinstance(p.get("property_mappings"), list)
                    and all(isinstance(value, str) for value in p["property_mappings"])
                    for p in providers))
        require(all(not (set(p["property_mappings"]) & mapping_ids)
                    or p["pk"] in provider_ids for p in providers))
        policy_ids = {v["pk"] for v in owned.values() if v["kind"] == "policy"}
        binding_ids = {v["pk"] for v in owned.values() if v["kind"] == "binding"}
        require(all(b.get("policy") not in policy_ids or b["pk"] in binding_ids for b in bindings))
        user_ids = {v["pk"] for v in owned.values() if v["kind"] == "user"}
        # These new service accounts must have no direct application bindings,
        # extra tokens, groups or roles. Future services need explicit source work.
        token_ids = {v["pk"] for v in owned.values() if v["kind"] == "token"}
        require(all(t.get("user") not in user_ids or t["pk"] in token_ids for t in self.rows["token"]))
        require(all(b.get("user") not in user_ids for b in bindings))

    def converge(self, hosts, state):
        self.no_foreign_references()
        declared = {i["host"]: i for i in hosts}
        prior_hosts = {k.split(":", 1)[1] for k in self.receipt["objects"] if k.startswith("user:")}
        require(prior_hosts <= set(declared))  # Never silently drop existing hosts.
        for item in hosts:
            high = self.receipt["generations"].get(item["host"], 0)
            key = "token:" + item["host"] + ":" + str(item["generation"])
            require(item["generation"] >= high)
            if state == "present" and item["state"] == "present":
                require(item["generation"] > high or key in self.receipt["objects"])
        for item in hosts:
            host = item["host"]
            username = "webmcp-interactive-" + host
            if "user:" + host not in self.receipt["objects"] and (item["state"] == "absent" or state == "absent"):
                continue
            self.ensure("user:" + host, "user", {
                "username": username, "name": "Web MCP interactive " + host,
                "type": "service_account", "path": "homelab-infra/webmcp-interactive",
                "is_active": item["state"] == "present" and state == "present",
                "groups": [], "roles": [],
                "attributes": {"webmcp-interactive-owner": self.owner, "goauthentik.io/user/token-maximum-lifetime": "days=180"},
            }, "username")
        by_pk = {user["pk"]: user for user in self.rows["user"]}
        users = [by_pk[self.receipt["objects"]["user:" + item["host"]]["pk"]]
                 for item in hosts if item["state"] == "present" and state == "present"]
        identity, policy = expressions(users, self.owner)
        # Bootstrap without resource mappings. No valid Jocasta token can exist
        # before the deny policy is attached. Passwords are retrieved only last.
        if state == "present" and "policy" not in self.receipt["objects"]:
            self.ensure("policy", "policy", {"name": CLIENT + " access", "expression": "return False", "execution_logging": False}, "name")
        if state == "present":
            flows = self.api.request("GET", "/flows/instances/?page_size=500")
            require(isinstance(flows.get("results"), list) and not flows.get("pagination", {}).get("next"))
            flow = lambda slug: [f["pk"] for f in flows["results"] if f.get("slug") == slug]
            authorization = flow("default-provider-authorization-implicit-consent")
            invalidation = flow("default-provider-invalidation-flow")
            require(len(authorization) == len(invalidation) == 1)
            provider_body = {
                "name": CLIENT, "client_id": CLIENT, "client_type": "confidential",
                "grant_types": ["client_credentials"], "redirect_uris": [],
                "authorization_flow": authorization[0], "invalidation_flow": invalidation[0],
                "authentication_flow": None, "access_token_validity": "seconds=300",
                # Authentik 2026.8.4 builds access JWTs from IDToken.to_dict;
                # this flag must be true for resource/operator mappings to appear.
                "include_claims_in_id_token": True, "issuer_mode": "per_provider",
                "signing_key": SIGNING_KEY, "encryption_key": None,
                "jwt_federation_sources": [], "jwt_federation_providers": [],
                "property_mappings": [],
            }
            if "provider" not in self.receipt["objects"]:
                self.ensure("provider", "provider", provider_body, "name")
            provider = self.receipt["objects"]["provider"]["pk"]
            self.ensure("application", "application", {
                "name": CLIENT, "slug": CLIENT, "provider": provider,
                "backchannel_providers": [], "policy_engine_mode": "all",
                "meta_launch_url": "blank://blank", "meta_hide": True,
            }, "slug")
            app = self.receipt["objects"]["application"]["pk"]
            policy_pk = self.receipt["objects"]["policy"]["pk"]
            self.ensure("binding", "binding", {
                "target": app, "policy": policy_pk, "group": None, "user": None,
                "order": 0, "enabled": True, "negate": False,
                "timeout": 5, "failure_result": False,
            }, "target")
            read = self.ensure("read", "mapping", {"name": CLIENT + " read", "scope_name": "jocasta.read", "description": "", "expression": identity}, "name")
            write = self.ensure("write", "mapping", {"name": CLIENT + " write", "scope_name": "jocasta.write", "description": "", "expression": "return {}"}, "name")
            provider_body["property_mappings"] = [read["pk"], write["pk"]]
            self.ensure("provider", "provider", provider_body, "name")
            self.ensure("policy", "policy", {"name": CLIENT + " access", "expression": policy, "execution_logging": False}, "name")
            jwks = self.api.request("GET", "/application/o/" + CLIENT + "/jwks/", public=True)
            keys = jwks.get("keys")
            require(isinstance(keys, list) and 1 <= len(keys) <= 16)
            signers = [k for k in keys if k.get("use") == "sig"]
            require(signers and all(k.get("kty") == "RSA" and k.get("alg") == "RS256" and k.get("n") and k.get("e") for k in signers))
        for item in hosts:
            host, generation = item["host"], item["generation"]
            present = item["state"] == "present" and state == "present"
            key = "token:" + host + ":" + str(generation)
            prior = [k for k in self.receipt["objects"] if k.startswith("token:" + host + ":")]
            generations = [int(k.rsplit(":", 1)[1]) for k in prior]
            require(not generations or generation >= max(generations))
            if present:
                self.ensure(key, "token", {
                    "identifier": "webmcp-interactive-" + host + "-g" + str(generation),
                    "intent": "app_password", "user": self.receipt["objects"]["user:" + host]["pk"],
                    "description": CLIENT + " " + self.owner + " " + host + " generation " + str(generation),
                    "expiring": True, "expires": item["expires"],
                }, "identifier")
            for old in prior:
                if old != key or not present:
                    self.remove(old)
        if state == "absent":
            for key in ("binding", "application", "provider", "read", "write", "policy"):
                self.remove(key)
        # Re-read all owned objects and all references before opening any key.
        self.rows = {k: self.api.rows(k) for k in PATHS}
        self.verify_owned()
        self.no_foreign_references()
        for item in hosts:
            host = item["host"]
            name = host + ".json"
            if item["state"] != "present" or state != "present":
                try:
                    old = read_private(self.fd, name)
                    require(old.get("owner") == self.owner and old.get("host") == host)
                    os.unlink(name, dir_fd=self.fd)
                    self.changed = True
                except FileNotFoundError:
                    pass
                continue
            token = "webmcp-interactive-" + host + "-g" + str(item["generation"])
            result = self.api.request("GET", "/core/tokens/" + token + "/view_key/")
            password = result.get("key")
            require(isinstance(password, str) and re.fullmatch(r"[\x21-\x7e]{16,256}", password) is not None)
            value = {"version": 1, "owner": self.owner, "host": host,
                     "username": "webmcp-interactive-" + host, "password": password, "generation": item["generation"]}
            try:
                old = read_private(self.fd, name)
                require(old.get("owner") == self.owner and old.get("host") == host)
            except FileNotFoundError:
                old = None
            if old != value:
                write_private(self.fd, name, value)
                self.changed = True
        return self.changed


def main():
    module = AnsibleModule(argument_spec={
        "enabled": {"type": "bool", "default": False},
        "hosts": {"type": "list", "elements": "dict", "required": True},
        "owner": {"type": "str", "required": True},
        "receipt_dir": {"type": "path", "required": True},
        "laptop_qualified": {"type": "bool", "default": False},
        "state": {"type": "str", "choices": ["present", "absent"], "default": "present"},
        "api_token": {"type": "str", "no_log": True, "default": ""},
    }, supports_check_mode=True)
    fd = lock = None
    try:
        p = module.params
        require(p["enabled"])
        hosts = declaration(p["hosts"], p["owner"], p["laptop_qualified"], dt.datetime.now(dt.timezone.utc))
        if module.check_mode:
            module.exit_json(changed=False, plan={"hosts": [i["host"] for i in hosts], "state": p["state"], "issuer": ISSUER, "offline": True})
        fd = secure_dir(p["receipt_dir"])
        lock = os.open("lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=fd)
        st = os.fstat(lock)
        require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        enrollment = Enrollment(APIClient(p["api_token"]), fd, p["owner"])
        changed = enrollment.converge(hosts, p["state"])
        module.exit_json(changed=changed, issuer=ISSUER, credential_paths=[p["receipt_dir"] + "/" + i["host"] + ".json" for i in hosts if i["state"] == "present" and p["state"] == "present"])
    except Exception:
        module.fail_json(msg="Managed enrollment refused or unconfirmed. Reconcile the original protected receipt and pending operation before retry; no mutation was replayed.")
    finally:
        if lock is not None:
            os.close(lock)
        if fd is not None:
            os.close(fd)


if __name__ == "__main__":
    main()
