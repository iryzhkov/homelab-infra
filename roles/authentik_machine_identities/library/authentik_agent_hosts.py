#!/usr/bin/python
"""Sole creation writer for M3 chaldea-HOST accounts; no old-account writes."""
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import stat
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agent_host_contract import (
    MATRIX, GROUPS, PATH, attributes, declaration, managed_role_ok,
    require, scope_expression,
)

ORIGIN = "https://auth.ryzhkov.dev"
API = ORIGIN + "/api/v3"
PATHS = {"user": "/core/users/", "token": "/core/tokens/",
         "group": "/core/groups/", "mapping": "/propertymappings/provider/scope/",
         "policy": "/policies/expression/"}

class Refused(Exception):
    pass

def matches(seen, body):
    return all(seen.get(k) == v for k, v in body.items())

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

    def require_superuser(self):
        # Both collection and used_by views can silently filter non-admin reads.
        actor = self.request("GET", "/core/users/me/")
        require(isinstance(actor, dict) and isinstance(actor.get("user"), dict)
                and actor["user"].get("is_superuser") is True)

    def mapping_uses(self, pk):
        require(isinstance(pk, str) and str(uuid.UUID(pk)) == pk)
        # Check authority here, independent of any previous application load.
        self.require_superuser()
        result = self.request("GET", "/propertymappings/all/" + pk + "/used_by/")
        require(isinstance(result, list) and len(result) <= 500)
        fields = {"app", "model_name", "pk", "name", "action"}
        require(all(isinstance(row, dict) and set(row) == fields
                    and all(isinstance(value, str) and 1 <= len(value) <= 1024
                            for value in row.values()) for row in result))
        return result

    def rows(self, kind, owned=None):
        # Complete public discovery; never list secret-bearing OAuth2 bodies.
        path = ("/providers/all/" if kind == "provider" else PATHS[kind]) + "?page_size=500"
        if kind == "provider":
            self.require_superuser()
        if kind == "application":
            # A matching pagination count is not completeness evidence when
            # Authentik silently filters applications for a non-superuser.
            self.require_superuser()
            path += "&superuser_full_list=true"
        result = self.request("GET", path)
        require(isinstance(result, dict) and isinstance(result.get("results"), list))
        rows = result["results"]
        require(len(rows) <= 500 and not result.get("pagination", {}).get("next"))
        require(result.get("pagination", {}).get("count") == len(rows))
        if kind == "provider":
            # Only the original receipt ID permits an OAuth2 detail read.
            # Check all public name/ID collisions before opening any such body.
            records = [r for r in (owned or {}).values() if r["kind"] == kind]
            for record in records:
                pk = record["pk"]
                require(type(pk) is int and pk > 0 and record["lookup"] == pk)
                found = [r for r in rows if r.get("pk") == pk]
                named = [r for r in rows if r.get("name") == record["body"]["name"]]
                require(len(found) == len(named) == 1 and found[0] == named[0])
            for record in records:
                detail = self.request("GET", PATHS[kind] + str(record["pk"]) + "/")
                require(isinstance(detail, dict) and detail.get("pk") == record["pk"]
                        and matches(detail, record["body"]))
                rows = [detail if r.get("pk") == record["pk"] else r for r in rows]
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




class HostEnrollment:
    def __init__(self, api, fd, owner, check=False):
        self.api, self.fd, self.owner, self.check = api, fd, owner, check
        try:
            self.receipt = read_private(fd, "hosts.json")
            require(self.receipt["owner"] == owner and self.receipt["pending"] is None)
            require(self.receipt["version"] == 1)
        except FileNotFoundError:
            require(set(os.listdir(fd)) <= {"host.lock"})
            self.receipt = {"version": 1, "owner": owner, "pending": None,
                            "users": {}, "tokens": {}, "revoked": [], "expressions": {}}
        self.changed = False
        api.require_superuser()
        self.rows = {k: api.rows(k) for k in PATHS}

    def save(self):
        if not self.check:
            write_private(self.fd, "hosts.json", self.receipt)

    def mutate(self, method, path, body):
        self.changed = True
        if self.check:
            return body
        self.receipt["pending"] = {"method": method, "path": path, "body": body}
        self.save()
        result = self.api.request(method, path, body)
        require(matches(result, body))
        # Caller records actual IDs before clearing the pending journal.
        return result

    def commit(self):
        self.receipt["pending"] = None
        self.save()

    def group_ids(self, host):
        ids = []
        for service in MATRIX[host]:
            if service == "jocasta":
                continue
            found = [g for g in self.rows["group"] if g["name"] == GROUPS[service]]
            require(len(found) == 1)
            g = found[0]
            require(g.get("is_superuser") is False and g.get("roles") == [] and g.get("parents") == [])
            ids.append(g["pk"])
        return sorted(ids)

    def validate_user(self, user, host, groups):
        require(user["username"] == "chaldea-" + host and user["path"] == PATH
                and user["type"] == "service_account" and user.get("is_superuser") is False)
        expected = attributes(host, self.owner)
        actual = copy.deepcopy(user["attributes"])
        actual.pop("revoked", None)
        require(actual == expected and sorted(user["groups"]) == groups)
        roles, permissions = {}, {}
        for rid in user["roles"]:
            roles[rid] = self.api.request("GET", "/rbac/roles/" + rid + "/")
            require([u["pk"] for u in self.rows["user"] if rid in u.get("roles", [])] == [user["pk"]])
            require(not any(rid in g.get("roles", []) for g in self.rows["group"]))
            # Query also includes global permissions (object_pk=null); reject them.
            result = self.api.request("GET", "/rbac/permissions/roles/?uuid=" + rid + "&page_size=500")
            require(not result.get("pagination", {}).get("next")
                    and result["pagination"]["count"] == len(result["results"]))
            permissions[rid] = result["results"]
        require(managed_role_ok(user, roles, permissions, self.rows["token"]))

    def users(self, hosts):
        require(set(self.receipt["users"]) <= {i["host"] for i in hosts})
        result, delivery = [], []
        for item in hosts:
            host = item["host"]
            username = "chaldea-" + host
            found = [u for u in self.rows["user"] if u["username"] == username]
            require(len(found) <= 1)
            pk = self.receipt["users"].get(host)
            require((pk is None and not found) or (len(found) == 1 and found[0]["pk"] == pk))
            groups = self.group_ids(host)
            user = found[0] if found else None
            if user:
                self.validate_user(user, host, groups)
            revoked = (item["revoked"] or host in self.receipt["revoked"]
                       or bool(user and (not user["is_active"] or user["attributes"].get("revoked"))))
            if revoked:
                if host not in self.receipt["revoked"]:
                    self.receipt["revoked"].append(host)
                    self.changed = True
                    self.save()
                if user and user["is_active"]:
                    self.mutate("PATCH", "/core/users/" + str(pk) + "/", {"is_active": False})
                    self.commit()
                delivery.append(dict(item, revoked=True))
                continue
            if not user:
                body = {"username": username, "name": "Chaldea " + host,
                        "path": PATH, "type": "service_account", "is_active": True,
                        "groups": groups, "attributes": attributes(host, self.owner)}
                user = self.mutate("POST", "/core/users/", body)
                if self.check:
                    # Account PKs cannot be predicted in check mode.
                    delivery.append(dict(item, revoked=False))
                    continue
                self.receipt["users"][host] = user["pk"]
                self.commit()
            result.append(user)
            token = username + "-g" + str(item["generation"])
            prior = self.receipt["tokens"].get(host)
            if prior is None:
                require(not any(t["identifier"] == token for t in self.rows["token"]))
                body = {"identifier": token, "user": user["pk"], "intent": "app_password",
                        "expiring": True, "expires": item["expires"],
                        "description": "M3 host bootstrap " + self.owner}
                self.mutate("POST", "/core/tokens/", body)
                self.receipt["tokens"][host] = {"identifier": token, "generation": item["generation"]}
                self.commit()
            else:
                # Bootstrap is one-shot. Self-rotation owns later generations.
                require(item["generation"] == prior["generation"])
            delivery.append(dict(item, revoked=False, token=token))
        return result, delivery


    def baseline_owner(self, baseline, current, original):
        anchor = baseline["ownership"]
        if baseline["service"] == "jocasta":
            require(set(anchor) == {"receipt_dir"})
            fd = secure_dir(anchor["receipt_dir"])
            try:
                prior = read_private(fd, "receipt.json")
            finally:
                os.close(fd)
            require(prior["version"] == 1 and prior["pending"] is None)
            matches_ = [r for r in prior["objects"].values()
                        if r["kind"] == baseline["kind"] and r["pk"] == baseline["pk"]]
            require(len(matches_) == 1)
            recorded = matches_[0]["body"]
            require(recorded.get("expression") in (original, current["expression"]))
            for field in ("name", "scope_name"):
                if field in recorded:
                    require(recorded[field] == current.get(field))
        else:
            require(set(anchor) == {"blueprint_pk", "content_sha256"})
            require(str(uuid.UUID(anchor["blueprint_pk"])) == anchor["blueprint_pk"])
            blueprint = self.api.request("GET", "/managed/blueprints/" + anchor["blueprint_pk"] + "/")
            content = blueprint["content"]
            require(blueprint["status"] == "successful"
                    and hashlib.sha256(content.encode()).hexdigest() == anchor["content_sha256"])
            prefix = "# homelab-infra-owned: "
            markers = [line[len(prefix):] for line in content.splitlines() if line.startswith(prefix)]
            require(len(markers) == 1)
            record = json.loads(markers[0])
            require(record["version"] == 1)
            require(["scopemapping", current["name"], "present"] in record["objects"]
                    and ["group", GROUPS[baseline["service"]], "present"] in record["objects"])

    def expressions(self, users, baselines):
        require(isinstance(baselines, list) and baselines)
        keys = set()
        for baseline in baselines:
            require(set(baseline) == {"kind", "pk", "sha256", "service", "scope", "ownership"})
            kind, pk, service = baseline["kind"], baseline["pk"], baseline["service"]
            require(kind in ("mapping", "policy") and service in set().union(*(set(x) for x in MATRIX.values())))
            require(kind != "policy" or service == "jocasta")
            key = kind + ":" + pk
            require(key not in keys)
            keys.add(key)
            found = [r for r in self.rows[kind] if r["pk"] == pk]
            require(len(found) == 1)
            current = found[0]
            prior = self.receipt["expressions"].get(key)
            if prior:
                require(current["expression"] == prior["rendered"])
                require(hashlib.sha256(prior["original"].encode()).hexdigest() == baseline["sha256"])
                original = prior["original"]
            else:
                original = current["expression"]
                require(hashlib.sha256(original.encode()).hexdigest() == baseline["sha256"])
            self.baseline_owner(baseline, current, original)
            if kind == "mapping":
                require(current["scope_name"] == baseline["scope"])
                desired = scope_expression(service, baseline["scope"], users, original)
            else:
                from ansible.module_utils.agent_host_contract import jocasta_policy
                desired = jocasta_policy(users, original)
            if desired != current["expression"]:
                self.mutate("PATCH", PATHS[kind] + pk + "/", {"expression": desired})
            self.receipt["expressions"][key] = {"original": original, "rendered": desired}
            self.commit()
        # Every resource scope needs coexistence. Missing a row is not a partial rollout.
        expected = {("jocasta", "jocasta.read"), ("jocasta", "jocasta.write"),
                    ("shizune", "shizune.use"), ("ha", "ha.read"), ("homelab", "homelab.read")}
        from ansible.module_utils.agent_host_contract import MATRIX as matrix
        expected |= {("ov", "ov." + a) for a in matrix["laptop"]["ov"]["ov_actions"]}
        expected |= {("ov", "ov.personal." + a) for a in matrix["laptop"]["ov"]["ov_personal"]["actions"]}
        expected |= {("holonet", s) for p in matrix["laptop"]["holonet"]["purposes"].values() for s in p["scopes"]}
        require({(b["service"], b["scope"]) for b in baselines if b["kind"] == "mapping"} == expected)
        require(sum(b["kind"] == "policy" for b in baselines) == 1)

def main():
    module = AnsibleModule(argument_spec={
        "hosts": {"type": "list", "elements": "dict", "required": True},
        "owner": {"type": "str", "required": True},
        "receipt_dir": {"type": "path", "required": True},
        "baselines": {"type": "list", "elements": "dict", "required": True},
        "api_token": {"type": "str", "no_log": True, "required": True},
    }, supports_check_mode=True)
    fd = lock = None
    try:
        p = module.params
        hosts = declaration(p["hosts"], p["owner"], dt.datetime.now(dt.timezone.utc))
        fd = secure_dir(p["receipt_dir"])
        if not module.check_mode:
            lock = os.open("host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            st = os.fstat(lock)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                    and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        e = HostEnrollment(APIClient(p["api_token"]), fd, p["owner"], module.check_mode)
        # Preflight the entire operation before the first account/token write.
        original_receipt = copy.deepcopy(e.receipt)
        e.check = True
        users, _ = e.users(hosts)
        e.expressions(users, p["baselines"])
        e.receipt = original_receipt
        e.changed = False
        e.check = module.check_mode
        users, delivery = e.users(hosts)
        # Missing accounts in CHECK need real API-returned PKs at apply time.
        # Validate all baselines even then, but do not render guessed admission.
        e.expressions(users, p["baselines"])
        if not module.check_mode:
            # Recheck authoritative account state after the last policy write.
            e.rows = {k: e.api.rows(k) for k in PATHS}
            users, delivery = e.users(hosts)
        module.exit_json(changed=e.changed, delivery=delivery,
                         public_host_users=users, predicted_pks=False)
    except Exception:
        module.fail_json(msg="Host enrollment refused or unconfirmed. Reconcile hosts.json and its pending operation; no automatic replay.")
    finally:
        if lock is not None:
            os.close(lock)
        if fd is not None:
            os.close(fd)

if __name__ == "__main__":
    main()
