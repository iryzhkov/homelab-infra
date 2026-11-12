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
    MATRIX, GROUPS, GROUP_IDS, PATH, ROTATION_HOSTS, attributes, declaration, managed_role_ok,
    require, scope_expression, writer_user_readback, writer_token_readback, rotation_provider_body,
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


class RotationReconciliationRefused(Refused):
    """Fixed public diagnostic; never includes an API body or credential."""


ROTATION_REFUSAL = (
    "Rotation provider reconciliation refused: retain the pending POST; "
    "require its exact returned pk and a live body matching the corrected contract "
    "(including grant_types). No provider POST was retried. "
    "The legacy pending POST has no pk; provider 113 requires operator reconciliation."
)


class HostEnrollment:
    def __init__(self, api, fd, owner, check=False, reenable_host=""):
        self.reenable_host = reenable_host
        self.api, self.fd, self.owner, self.check = api, fd, owner, check
        try:
            self.receipt = read_private(fd, "hosts.json")
            require(self.receipt["owner"] == owner)
            pending = self.receipt["pending"]
            if pending is not None:
                require(pending.get("method") == "POST" and pending.get("path") == PATHS["provider"])
            if pending is not None and not (
                pending.get("method") == "POST" and pending.get("path") == PATHS["provider"]
                and type(pending.get("pk")) is int and pending["pk"] > 0
                and ((pending.get("body", {}).get("name") == "chaldea-host-rotation"
                      and "provider" not in self.receipt.get("rotation", {})
                      and "group" in self.receipt.get("rotation", {}))
                     or (pending.get("body", {}).get("name") == "chaldea-akasha"
                         and "provider" not in self.receipt.get("akasha", {})
                         and {"mapping", "policy"} <= set(self.receipt.get("akasha", {}))))
            ):
                raise RotationReconciliationRefused()
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
                require(matches(actual, prior["body"]) and prior["body"] == body
                        and (kind != "provider" or (actual.get("pk") == prior["pk"]
                             and all(key in actual for key in body))))
                return prior["pk"]
            pending = self.receipt["pending"]
            if kind == "provider" and pending is not None:
                old_body = dict(body)
                old_body.pop("grant_types")
                old_body["allowed_grant_types"] = ["client_credentials"]
                if len(found) != 1 or found[0]["pk"] != pending["pk"]:
                    raise RotationReconciliationRefused()
                try:
                    actual = self.api.request("GET", PATHS[kind] + str(pending["pk"]) + "/")
                except Exception:
                    raise RotationReconciliationRefused() from None
                if not (pending["body"] in (body, old_body)
                        and actual.get("pk") == pending["pk"]
                        and len(found) == 1 and found[0]["pk"] == pending["pk"]
                        and all(key in actual for key in body) and matches(actual, body)):
                    raise RotationReconciliationRefused()
                objects[kind] = {"pk": pending["pk"], "body": body}
                self.changed = True
                self.commit()
                return actual["pk"]
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
        provider = owned("provider", "chaldea-host-rotation", rotation_provider_body(
            signing_key, flow("default-provider-authorization-implicit-consent"),
            flow("default-provider-invalidation-flow"), mappings[0]["pk"]))
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


    def akasha(self, users, signing_key):
        """Dedicated resource, receipt-owned and fail closed on foreign objects."""
        from ansible.module_utils.agent_host_contract import (
            AKASHA_HOSTS, AKASHA_REGISTRY_HOSTS, AKASHA_AUDIENCE, AKASHA_CLIENT,
            RUNTIME_GUARD, public_users, canonical)
        public_users(users)
        require(str(uuid.UUID(signing_key)) == signing_key)
        expected = canonical({str(u["pk"]): {
            "username": u["username"], "attributes": u["attributes"],
            "groups": sorted(str(g) for g in u["groups"])}
            for u in users if u["attributes"]["agent-host"] in AKASHA_HOSTS})
        guard = ("expected = " + repr(expected) + "\n" + RUNTIME_GUARD + """
u = request.user
e = expected.get(str(u.pk)) if u else None
if not e or e['username'] != u.username or not host_allowed(u, e):
    return False
http = request.http_request
if http is None or http.method != 'POST':
    return False
p = http.POST
return (p.get('client_id') == 'chaldea-akasha' and p.get('username') == u.username
    and bool(p.get('password')) and not p.get('client_secret')
    and not p.get('client_assertion') and not p.get('client_assertion_type')
    and p.get('grant_type') == 'client_credentials'
    and p.getlist('resource') == ['https://akasha.ryzhkov.dev']
    and p.get('scope', '').split() == ['akasha.ingest']
    and request.context.get('oauth_grant_type') == 'client_credentials'
    and request.context.get('oauth_scopes') == {'akasha.ingest'})
""")
        expression = ("registry_hosts = " + repr(AKASHA_REGISTRY_HOSTS) + "\n"
                      + "expected = " + repr(expected) + "\n" + RUNTIME_GUARD + """
u = user
e = expected.get(str(u.pk)) if u else None
if (not e or e['username'] != u.username or not host_allowed(u, e)
        or provider.client_id != 'chaldea-akasha'
        or provider.application.slug != 'chaldea-akasha'
        or token.scope != ['akasha.ingest']):
    return {}
host = registry_hosts[u.attributes['agent-host']]
return {'aud': 'https://akasha.ryzhkov.dev',
        'preferred_username': 'chaldea-' + host, 'host': host,
        'run_id': u.username}
""").rstrip()
        # Authentik's serializers trim surrounding whitespace on save; send the
        # stored form so the exact readback in mutate() and owned() matches.
        guard = guard.rstrip()
        objects = self.receipt.setdefault("akasha", {})
        def owned(kind, body):
            prior = objects.get(kind)
            field = "slug" if kind == "application" else "name"
            found = [r for r in self.rows[kind] if r.get(field) == body.get(field)]
            if kind == "binding":
                found = [r for r in self.rows[kind] if r.get("target") == body["target"]]
            if prior:
                require(len(found) == 1 and found[0]["pk"] == prior["pk"])
                actual = found[0]
                if kind == "provider":
                    actual = self.api.request("GET", PATHS[kind] + str(prior["pk"]) + "/")
                require(matches(actual, prior["body"]))
                if kind == "provider":
                    require(actual.get("pk") == prior["pk"]
                            and all(key in actual for key in body))
                if prior["body"] != body:
                    # Only the exact account cohort in our own expressions may evolve.
                    require(kind in ("mapping", "policy")
                            and {k: v for k, v in body.items() if k != "expression"}
                            == {k: v for k, v in prior["body"].items() if k != "expression"})
                    self.mutate("PATCH", PATHS[kind] + str(prior["pk"]) + "/", body)
                    prior["body"] = body
                    self.commit()
                return prior["pk"]
            pending = self.receipt["pending"]
            if kind == "provider" and pending is not None:
                require(pending["body"] == body and len(found) == 1
                        and found[0]["pk"] == pending["pk"])
                actual = self.api.request("GET", PATHS[kind] + str(pending["pk"]) + "/")
                require(actual.get("pk") == pending["pk"]
                        and all(key in actual for key in body) and matches(actual, body))
                objects[kind] = {"pk": pending["pk"], "body": body}
                self.changed = True
                self.commit()
                return actual["pk"]
            require(not found)
            if self.check:
                self.changed = True
                pk = "pending-akasha-" + kind
            else:
                pk = self.mutate("POST", PATHS[kind], body)["pk"]
            objects[kind] = {"pk": pk, "body": body}
            self.commit()
            self.rows[kind].append(dict(body, pk=pk))
            return pk
        flows = self.api.request("GET", "/flows/instances/?page_size=500")
        require(not flows["pagination"].get("next")
                and flows["pagination"]["count"] == len(flows["results"]))
        def flow(slug):
            found = [f["pk"] for f in flows["results"] if f["slug"] == slug]
            require(len(found) == 1)
            return found[0]
        mapping = owned("mapping", {"name": "Chaldea Akasha identity",
            "scope_name": "akasha.ingest", "expression": expression})
        policy = owned("policy", {"name": "Chaldea Akasha host admission",
            "expression": guard})
        provider = owned("provider", {"name": AKASHA_CLIENT, "client_id": AKASHA_CLIENT,
            "client_type": "confidential", "grant_types": ["client_credentials"],
            "authorization_flow": flow("default-provider-authorization-implicit-consent"),
            "invalidation_flow": flow("default-provider-invalidation-flow"),
            "access_token_validity": "seconds=300", "issuer_mode": "per_provider",
            "sub_mode": "hashed_user_id", "encryption_key": None,
            "include_claims_in_id_token": False, "refresh_token_validity": "seconds=0",
            "redirect_uris": [], "signing_key": signing_key, "property_mappings": [mapping],
            "jwt_federation_sources": [], "jwt_federation_providers": []})
        app = owned("application", {"name": "Chaldea Akasha", "slug": AKASHA_CLIENT,
            "provider": provider, "backchannel_providers": [], "policy_engine_mode": "all"})
        owned("binding", {"target": app, "policy": policy, "group": None, "user": None,
                          "order": 0, "enabled": True, "negate": False})
        require(not any(a.get("provider") == provider and a["pk"] != app
                        or provider in a.get("backchannel_providers", [])
                        for a in self.rows["application"]))

    def recover_provider(self, rotation_key, akasha_key):
        """Resolve only the exact journaled provider before allowing other writes."""
        pending = self.receipt["pending"]
        if pending is None:
            return
        if pending["body"].get("name") == "chaldea-akasha":
            require(akasha_key)
            # Reuse current receipt-owned users, without lifecycle mutation. Changed
            # cohort expressions refuse until this exact pending write is reconciled.
            users = [u for u in self.rows["user"]
                     if u["pk"] in self.receipt["users"].values()
                     and u["is_active"]
                     and u["attributes"]["agent-host"] not in self.receipt["revoked"]]
            self.akasha(users, akasha_key)
        else:
            if not rotation_key:
                raise RotationReconciliationRefused()
            self.rotation(rotation_key)

    def acquire_mapping(self, hosts, baselines, value, create=False):
        """One bounded resource effect, using the existing write-ahead journal."""
        record = self.receipt.get("acquire_mapping")
        if not record and not create:
            return baselines
        require(value)
        name = "M3 original writer acquisition READ"
        body = {"name": name, "scope_name": "holonet.acquire", "expression": "return {}"}
        selected = [m for m in self.rows["mapping"]
                    if m["scope_name"].startswith("holonet.acquire") or m["name"] == name]
        apps = [a for a in self.rows["application"] if a["slug"] == "chaldea-holonet"]
        require(len(apps) == 1)
        providers = [p for p in self.rows["provider"] if p["pk"] == apps[0]["provider"]]
        require(len(providers) == 1)
        provider = providers[0]
        require(not any(b["service"] == "holonet" and b["scope"] == "holonet.acquire"
                        for b in baselines))
        if record:
            require(record["provider"] == provider["pk"] and record["body"] == body)
            require(len(selected) == 1 and selected[0]["pk"] == record["pk"]
                    and selected[0]["name"] == name and selected[0]["scope_name"] == "holonet.acquire")
            expected = sorted(record["before"] + [record["pk"]])
            require(sorted(provider["property_mappings"]) in (record["before"], expected))
            require(not record["attached"] or sorted(provider["property_mappings"]) == expected)
            if not record["attached"]:
                require(selected[0]["expression"] == body["expression"])
            # Validate all existing resource ownership before a resumable attach.
            original_rows = copy.deepcopy(self.rows)
            provider["property_mappings"] = record["before"]
            try:
                self.resources(hosts, baselines)
            finally:
                self.rows = original_rows
        else:
            require(create and not selected)
            self.resources(hosts, baselines)
            record = {"provider": provider["pk"], "before": sorted(provider["property_mappings"]),
                      "body": body, "attached": False}
            require(len(record["before"]) == len(set(record["before"])))
            if self.check:
                self.changed = True
                record["pk"] = "pending-holonet-acquire"
            else:
                # mutate persists pending BEFORE POST. An unknown outcome cannot
                # be adopted by name or automatically retried on a later run.
                result = self.mutate("POST", PATHS["mapping"], body)
                require(isinstance(result["pk"], str) and str(uuid.UUID(result["pk"])) == result["pk"])
                record["pk"] = result["pk"]
            self.receipt["acquire_mapping"] = record
            self.commit()
            self.rows["mapping"].append(dict(body, pk=record["pk"]))
        if not record["attached"]:
            if self.check:
                self.changed = True
            else:
                fresh = self.api.rows("provider")
                found = [p for p in fresh if p["pk"] == record["provider"]]
                require(len(found) == 1 and sorted(found[0]["property_mappings"]) == record["before"])
                self.mutate("PATCH", PATHS["provider"] + str(record["provider"]) + "/",
                            {"property_mappings": record["before"] + [record["pk"]]})
            record["attached"] = True
            self.commit()
        # CHECK uses only an in-memory hypothetical PK, never a guessed grant.
        for row in self.rows["provider"]:
            if row["pk"] == record["provider"]:
                row["property_mappings"] = record["before"] + [record["pk"]]
        return baselines + [{"kind": "mapping", "pk": record["pk"], "service": "holonet",
                             "scope": "holonet.acquire",
                             "sha256": hashlib.sha256(body["expression"].encode()).hexdigest(),
                             "ownership": {"host_owner": self.owner}}]

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
        require(self.receipt["pending"] is None)
        self.changed = True
        if self.check:
            return body
        self.receipt["pending"] = {"method": method, "path": path, "body": body}
        self.save()
        result = self.api.request(method, path, body)
        # Preserve the returned identity even when a readback field is incompatible.
        if method == "POST" and path == PATHS["provider"]:
            require(type(result.get("pk")) is int and result["pk"] > 0)
            self.receipt["pending"]["pk"] = result["pk"]
            self.save()
        require(matches(result, body))
        if method == "POST" and path == PATHS["provider"]:
            require(all(key in result for key in body))
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
        if host in ROTATION_HOSTS and self.receipt.get("rotation", {}).get("group"):
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
        rotation = {"group": rotation_group, "client_id": "chaldea-host-rotation"} if rotation_group and host in ROTATION_HOSTS else None
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

    def purge_tokens(self, pk):
        # Deactivation must precede this fresh authoritative listing. A host may
        # have minted another token since the enrollment snapshot.
        for token in self.api.rows("token"):
            if token.get("user") == pk:
                self.changed = True
                if not self.check:
                    self.api.request("DELETE", "/core/tokens/" + token["identifier"] + "/")
        if not self.check:
            require(not any(t.get("user") == pk for t in self.api.rows("token")))

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
                    require(not user["is_active"])
                    self.purge_tokens(pk)
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
                if user:
                    self.purge_tokens(pk)
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
            rotation = {"group": rotation_group, "client_id": "chaldea-host-rotation"} if rotation_group and host in ROTATION_HOSTS else None
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
        if anchor == {"host_owner": self.owner}:
            record = self.receipt["acquire_mapping"]
            require(record["attached"] and baseline["kind"] == "mapping"
                    and baseline["service"] == "holonet" and baseline["scope"] == "holonet.acquire"
                    and current["pk"] == record["pk"] and current["name"] == record["body"]["name"]
                    and original == record["body"]["expression"])
            return
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


    def original_writer(self, value, previous_sha256=""):
        from ansible.module_utils.agent_host_contract import (
            original_writer_declaration, readback_sha256, ORIGINAL_WRITER,
        )
        value = original_writer_declaration(value)
        prior = self.receipt.get("original_writer")
        # An empty declaration uses the default closed mapping set. Existing
        # acquisition receipts still require explicit ownership reconciliation.
        if not value:
            return {}
        if prior and prior != value:
            require(previous_sha256 == readback_sha256(prior))
            require(all(prior[k] == value[k] for k in ("username", "pk", "subject", "generation")))
        if value.get("state") == "disabled":
            require(prior is not None)
            require(all(prior[k] == value[k] for k in prior if k != "state"))
            return value
        users = [u for u in self.rows["user"]
                 if u["username"] == ORIGINAL_WRITER or u["pk"] == value["pk"]
                 or u.get("uid") == value["subject"]]
        require(len(users) == 1)
        user = users[0]
        require(user["username"] == ORIGINAL_WRITER and user["pk"] == value["pk"]
                and user["uid"] == value["subject"]
                and user["path"] == "homelab-infra/machine-identities"
                and user["type"] == "service_account" and user["is_active"] is True
                and user["is_superuser"] is False
                and GROUP_IDS["holonet"] in user["groups"]
                and "agent-host" not in user["attributes"])
        require(readback_sha256(writer_user_readback(user)) == value["user_sha256"])
        identifier = ORIGINAL_WRITER + "-g1"
        tokens = [t for t in self.rows["token"] if t["identifier"] == identifier
                  or (t["user"] == user["pk"] and t["intent"] == "app_password")]
        require(len(tokens) == 1)
        token = tokens[0]
        require(token["identifier"] == identifier and token["user"] == user["pk"]
                and token["intent"] == "app_password" and token["expiring"] is True
                and matches(token, {"expires": value["expires"]}))
        require(readback_sha256(writer_token_readback(token)) == value["token_sha256"])
        # The protected subject is Authentik's hashed-user-id, never a host PK.
        apps = [a for a in self.rows["application"] if a["slug"] == "chaldea-holonet"]
        require(len(apps) == 1)
        providers = [p for p in self.rows["provider"] if p["pk"] == apps[0]["provider"]]
        # Public /providers/all/ has no sub_mode. Issuance binds str(u.uid)
        # to the protected subject, also pinned by the consumer; no detail GET.
        require(len(providers) == 1)
        return value

    def expressions(self, users, baselines, original_writer=None, previous_sha256=""):
        original_writer = self.original_writer(original_writer or {}, previous_sha256)
        planned = []
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
                desired = scope_expression(service, baseline["scope"], users, original, original_writer)
            else:
                from ansible.module_utils.agent_host_contract import jocasta_policy
                desired = jocasta_policy(users, original)
            planned.append((kind, pk, key, current["expression"], original, desired))
        # Every resource scope needs coexistence. Missing a row is not a partial rollout.
        expected = {("jocasta", "jocasta.read"),
                    ("shizune", "shizune.use"), ("ha", "ha.read"), ("homelab", "homelab.read")}
        from ansible.module_utils.agent_host_contract import MATRIX as matrix
        expected |= {("ov", "ov." + a) for a in matrix["laptop"]["ov"]["ov_actions"]}
        expected |= {("ov", "ov.personal." + a) for a in matrix["laptop"]["ov"]["ov_personal"]["actions"]}
        expected |= {("holonet", s) for p in matrix["laptop"]["holonet"]["purposes"].values() for s in p["scopes"]}
        if original_writer:
            expected.add(("holonet", "holonet.acquire"))
        require({(b["service"], b["scope"]) for b in baselines if b["kind"] == "mapping"} == expected)
        require(sum(b["kind"] == "policy" for b in baselines) == 1)
        require(len(baselines) == len(expected) + 1)
        require(set(self.receipt["expressions"]) <= keys)
        for kind, pk, key, current, original, desired in planned:
            if desired != current:
                self.mutate("PATCH", PATHS[kind] + pk + "/", {"expression": desired})
            self.receipt["expressions"][key] = {"original": original, "rendered": desired}
            self.commit()
        self.receipt["original_writer"] = original_writer
        self.save()

def main():
    module = AnsibleModule(argument_spec={
        "hosts": {"type": "list", "elements": "dict", "required": True},
        "owner": {"type": "str", "required": True},
        "receipt_dir": {"type": "path", "required": True},
        "baselines": {"type": "list", "elements": "dict", "required": True},
        "original_writer_read": {"type": "dict", "default": {}},
        "create_original_writer_read_mapping": {"type": "bool", "default": False},
        "original_writer_previous_sha256": {"type": "str", "default": ""},
        "reenable_host": {"type": "str", "default": ""},
        "akasha_signing_key": {"type": "str", "default": ""},
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
        if p["original_writer_read"]:
            require(any("holonet" in MATRIX[h["host"]] for h in hosts))
        rotation_key = (p["rotation_signing_key"] or e.receipt.get("rotation", {}).get("provider", {}).get("body", {}).get("signing_key"))
        akasha_key = (p.get("akasha_signing_key") or e.receipt.get("akasha", {}).get("provider", {}).get("body", {}).get("signing_key"))
        # Use the same protected signer pin as the existing resource providers.
        if akasha_key:
            require(akasha_key == rotation_key)
        # Preflight before any provider, account or token mutation.
        original_receipt = copy.deepcopy(e.receipt)
        from ansible.module_utils.agent_host_contract import original_writer_declaration
        projection_changed = original_writer_declaration(p["original_writer_read"]) != e.receipt.get("original_writer", {})
        e.check = True
        saved_rows = copy.deepcopy(e.rows)
        e.recover_provider(rotation_key, akasha_key)
        # Adopting a pending provider is not a resource change; keep it out of
        # resource_changed so the isolation guard below only sees resources.
        e.changed = False
        baselines = e.acquire_mapping(hosts, p["baselines"], p["original_writer_read"],
                                      p.get("create_original_writer_read_mapping", False))
        e.resources(hosts, baselines)
        resource_changed = e.changed
        e.changed = False
        if rotation_key:
            e.rotation(rotation_key)
        users, _ = e.users(hosts)
        if projection_changed or resource_changed:
            require(not e.changed and not p["reenable_host"])
        e.expressions(users, baselines, p["original_writer_read"], p.get("original_writer_previous_sha256", ""))
        if akasha_key:
            e.akasha(users, akasha_key)
        e.receipt = original_receipt
        e.changed = False
        e.check = module.check_mode
        e.rows = saved_rows
        e.recover_provider(rotation_key, akasha_key)
        baselines = e.acquire_mapping(hosts, p["baselines"], p["original_writer_read"],
                                      p.get("create_original_writer_read_mapping", False))
        e.resources(hosts, baselines)
        if rotation_key:
            e.rotation(rotation_key)
        users, delivery = e.users(hosts)
        # Missing accounts in CHECK need real API-returned PKs at apply time.
        # Validate all baselines even then, but do not render guessed admission.
        e.expressions(users, baselines, p["original_writer_read"], p.get("original_writer_previous_sha256", ""))
        if akasha_key:
            e.akasha(users, akasha_key)
        if not module.check_mode:
            # Recheck authoritative account state after the last policy write.
            e.rows = {k: e.api.rows(k) for k in PATHS}
            users, delivery = e.users(hosts)
            e.original_writer(p["original_writer_read"])
        # Projection-only enrollment never reads/delivers any host credential.
        if projection_changed or resource_changed:
            delivery = []
        module.exit_json(changed=e.changed, delivery=delivery,
                         public_host_users=users, predicted_pks=False)
    except RotationReconciliationRefused:
        module.fail_json(msg=ROTATION_REFUSAL)
    except Exception:
        module.fail_json(msg="Host enrollment refused or unconfirmed. Reconcile hosts.json and its pending operation; no automatic replay.")
    finally:
        if lock is not None:
            os.close(lock)
        if fd is not None:
            os.close(fd)

if __name__ == "__main__":
    main()
