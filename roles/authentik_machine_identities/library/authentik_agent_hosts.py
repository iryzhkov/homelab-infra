#!/usr/bin/python
"""Sole creation writer for M3 chaldea-HOST accounts; no old-account writes."""
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
import stat
import uuid
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.agent_host_contract import (
    MATRIX, GROUPS, GROUP_IDS, PATH, attributes, declaration, managed_role_ok,
    require, scope_expression,
)

ORIGIN = "https://auth.ryzhkov.dev"
API = ORIGIN + "/api/v3"
PATHS = {"user": "/core/users/", "token": "/core/tokens/",
         "group": "/core/groups/", "mapping": "/propertymappings/provider/scope/",
         "policy": "/policies/expression/", "provider": "/providers/oauth2/",
         "application": "/core/applications/", "binding": "/policies/bindings/"}

from ansible.module_utils.authentik_enrollment import (
    APIClient, Refused, matches, secure_dir, read_private, write_private,
)


class HostEnrollment:
    def __init__(self, api, fd, owner, check=False, reenable_host=""):
        self.reenable_host = reenable_host
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


    def rotation(self, signing_key):
        """Own a separate group/provider/application; never touch resource scopes."""
        require(str(uuid.UUID(signing_key)) == signing_key)
        mappings = [m for m in self.rows["mapping"] if m.get("scope_name") == "goauthentik.io/api"]
        require(len(mappings) == 1)
        flows = self.api.request("GET", "/flows/instances/?page_size=500")
        require(not flows["pagination"].get("next") and flows["pagination"]["count"] == len(flows["results"]))
        def flow(slug):
            found = [f["pk"] for f in flows["results"] if f["slug"] == slug]
            require(len(found) == 1)
            return found[0]
        objects = self.receipt.setdefault("rotation", {})
        def owned(kind, name, body):
            prior = objects.get(kind)
            found = [r for r in self.rows[kind] if r.get("slug" if kind == "application" else "name") == name]
            if kind == "binding":
                found = [r for r in self.rows[kind] if r.get("target") == body["target"]]
            if prior:
                require(len(found) == 1 and found[0]["pk"] == prior["pk"])
                actual = found[0]
                if kind == "provider":
                    actual = self.api.request("GET", PATHS[kind] + str(prior["pk"]) + "/")
                require(matches(actual, prior["body"]) and prior["body"] == body)
                return prior["pk"]
            require(not found)
            if self.check:
                self.changed = True
                return None
            result = self.mutate("POST", PATHS[kind], body)
            objects[kind] = {"pk": result["pk"], "body": body}
            self.commit()
            return result["pk"]
        group = owned("group", "chaldea-host-accounts", {
            "name": "chaldea-host-accounts", "is_superuser": False, "roles": [], "parents": []})
        provider = owned("provider", "chaldea-host-rotation", {
            "name": "chaldea-host-rotation", "client_id": "chaldea-host-rotation",
            "client_type": "confidential", "allowed_grant_types": ["client_credentials"],
            "authorization_flow": flow("default-provider-authorization-implicit-consent"),
            "invalidation_flow": flow("default-provider-invalidation-flow"),
            "access_token_validity": "seconds=300", "issuer_mode": "per_provider",
            "redirect_uris": [], "signing_key": signing_key,
            "property_mappings": [mappings[0]["pk"]]})
        if group is None or provider is None:
            return
        app = owned("application", "chaldea-host-rotation", {
            "name": "Chaldea host rotation", "slug": "chaldea-host-rotation",
            "provider": provider, "backchannel_providers": [], "policy_engine_mode": "all"})
        if app is None:
            return
        owned("binding", "", {"target": app, "group": group, "order": 0,
                              "enabled": True, "negate": False, "policy": None, "user": None})
        require(not any(a.get("provider") == provider and a["pk"] != app
                        or provider in a.get("backchannel_providers", []) for a in self.rows["application"]))

    def resources(self, hosts, baselines):
        # Inspect public provider bodies only. Never open an OAuth2 secret.
        for service in set().union(*(set(MATRIX[h["host"]]) for h in hosts)):
            selected = [b for b in baselines if b["service"] == service]
            mappings = [b for b in selected if b["kind"] == "mapping"]
            require(mappings)
            ids = {b["pk"] for b in mappings}
            anchor = mappings[0]["ownership"]
            if service == "jocasta":
                fd = secure_dir(anchor["receipt_dir"])
                try:
                    receipt = read_private(fd, "receipt.json")
                finally:
                    os.close(fd)
                require(receipt["pending"] is None)
                owned = receipt["objects"]
                ids.add(owned["write"]["pk"])
                # The host writer leaves jocasta.write alone, so its current body
                # must still equal the owned receipt; a modified expression could
                # otherwise inject foreign claims into admitted Jocasta tokens.
                writes = [m for m in self.rows["mapping"] if m["pk"] == owned["write"]["pk"]]
                require(len(writes) == 1 and matches(writes[0], owned["write"]["body"]))
                provider_pk = owned["provider"]["pk"]
                app_pk = owned["application"]["pk"]
                providers = [p for p in self.rows["provider"] if p["pk"] == provider_pk]
                apps = [a for a in self.rows["application"] if a["pk"] == app_pk]
                bindings = [b for b in self.rows["binding"] if b.get("target") == app_pk]
                require(len(bindings) == 1 and matches(bindings[0], owned["binding"]["body"]))
                require(len(apps) == 1 and matches(apps[0], owned["application"]["body"]))
            else:
                blueprint = self.api.request("GET", "/managed/blueprints/" + anchor["blueprint_pk"] + "/")
                content = blueprint["content"]
                require(blueprint["status"] == "successful"
                        and hashlib.sha256(content.encode()).hexdigest() == anchor["content_sha256"])
                prefix = "# homelab-infra-owned: "
                markers = [json.loads(line[len(prefix):]) for line in content.splitlines() if line.startswith(prefix)]
                require(len(markers) == 1 and markers[0]["version"] == 1)
                objects = markers[0]["objects"]
                providers = [p for p in self.rows["provider"] if set(p["property_mappings"]) == ids
                             and ["provider", p["name"], "present"] in objects]
                require(len(providers) == 1)
                provider_pk = providers[0]["pk"]
                apps = [a for a in self.rows["application"] if a.get("provider") == provider_pk]
                require(len(apps) == 1 and ["application", apps[0]["slug"], "present"] in objects)
                app_pk = apps[0]["pk"]
                require(apps[0]["policy_engine_mode"] == "any")
                bindings = [b for b in self.rows["binding"] if b.get("target") == app_pk]
                require(len(bindings) == 1 and bindings[0].get("group") == GROUP_IDS[service]
                        and bindings[0].get("policy") is None and bindings[0].get("user") is None)
            require(len(providers) == len(apps) == 1)
            provider = providers[0]
            attached = provider["property_mappings"]
            require(provider["meta_model_name"] == "authentik_providers_oauth2.oauth2provider"
                    and len(attached) == len(ids) and set(attached) == ids)
            rows = [m for m in self.rows["mapping"] if m["pk"] in ids]
            require(len(rows) == len(ids) and all(m["scope_name"] != "goauthentik.io/api" for m in rows))
            require(apps[0]["provider"] == provider["pk"] and apps[0].get("backchannel_providers") == [])
            require([a["pk"] for a in self.rows["application"] if a.get("provider") == provider["pk"]] == [app_pk])
            require(not any(provider["pk"] in a.get("backchannel_providers", []) for a in self.rows["application"]))
            require(bindings[0]["enabled"] is True and bindings[0]["negate"] is False
                    and bindings[0]["order"] == 0)

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
            require(g["pk"] == GROUP_IDS[service])
            require(g.get("is_superuser") is False and g.get("roles") == [] and g.get("parents") == [])
            ids.append(g["pk"])
        if self.receipt.get("rotation", {}).get("group"):
            record = self.receipt["rotation"]["group"]
            found = [g for g in self.rows["group"] if g["pk"] == record["pk"]]
            # The newly created group is not in the initial snapshot yet.
            if found:
                require(len(found) == 1 and matches(found[0], record["body"]))
            require(not any(record["pk"] in u.get("groups", []) and
                            u["pk"] not in self.receipt["users"].values() for u in self.rows["user"]))
            ids.append(record["pk"])
        return sorted(ids)

    def validate_user(self, user, host, groups):
        require(user["username"] == "chaldea-" + host and user["path"] == PATH
                and user["type"] == "service_account" and user.get("is_superuser") is False)
        expected = attributes(host, self.owner)
        actual = copy.deepcopy(user["attributes"])
        actual.pop("revoked", None)
        rotation_group = self.receipt.get("rotation", {}).get("group", {}).get("pk")
        legacy_groups = [g for g in groups if g != rotation_group]
        rotation = {"group": rotation_group, "client_id": "chaldea-host-rotation"} if rotation_group else None
        require(actual in (expected, attributes(host, self.owner, rotation))
                and sorted(user["groups"]) in (groups, legacy_groups))
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
            if host == self.reenable_host:
                require(not item["revoked"] and user is not None)
                prior = self.receipt["tokens"].get(host)
                if not user["is_active"] or host in self.receipt["revoked"]:
                    require(prior is None or item["generation"] > prior["generation"])
                    now = dt.datetime.now(dt.timezone.utc)
                    expiry = dt.datetime.fromisoformat(item["expires"].replace("Z", "+00:00"))
                    require(now + dt.timedelta(days=1) <= expiry <= now + dt.timedelta(days=60))
                    user = dict(user, is_active=True, attributes=attributes(host, self.owner))
                    self.mutate("PATCH", "/core/users/" + str(pk) + "/",
                                {"is_active": True, "attributes": user["attributes"]})
                    if host in self.receipt["revoked"]:
                        self.receipt["revoked"].remove(host)
                    self.receipt["tokens"].pop(host, None)
                    self.commit()
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
                for token_row in self.rows["token"]:
                    if token_row.get("user") == pk:
                        self.changed = True
                        if not self.check:
                            self.api.request("DELETE", "/core/tokens/" + token_row["identifier"] + "/")
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
            rotation_group = self.receipt.get("rotation", {}).get("group", {}).get("pk")
            rotation = {"group": rotation_group, "client_id": "chaldea-host-rotation"} if rotation_group else None
            desired = {"groups": groups, "attributes": attributes(host, self.owner, rotation)}
            if sorted(user["groups"]) != groups or user["attributes"] != desired["attributes"]:
                updated = self.mutate("PATCH", "/core/users/" + str(user["pk"]) + "/", desired)
                user = dict(user, **desired) if self.check else updated
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
        expected = {("jocasta", "jocasta.read"),
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
        "reenable_host": {"type": "str", "default": ""},
        "rotation_signing_key": {"type": "str", "default": ""},
        "api_token": {"type": "str", "no_log": True, "required": True},
    }, supports_check_mode=True)
    fd = lock = None
    try:
        p = module.params
        declaration(p["hosts"], p["owner"], dt.datetime.now(dt.timezone.utc), MATRIX)
        fd = secure_dir(p["receipt_dir"])
        if not module.check_mode:
            lock = os.open("host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            st = os.fstat(lock)
            require(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid()
                    and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        e = HostEnrollment(APIClient(p["api_token"], PATHS), fd, p["owner"], module.check_mode, p["reenable_host"])
        hosts = declaration(p["hosts"], p["owner"], dt.datetime.now(dt.timezone.utc), e.receipt["tokens"])
        e.resources(hosts, p["baselines"])
        rotation_key = (p["rotation_signing_key"] or e.receipt.get("rotation", {}).get("provider", {}).get("body", {}).get("signing_key"))
        if rotation_key:
            e.rotation(rotation_key)
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
