"""Closed operator application contract and blueprint ownership checks.

Only the opt-in operator role uses these filters. Human and machine accounts
are never created or edited here.
"""
import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid5

import yaml
from ansible.errors import AnsibleFilterError

# Reuse the maintained asynchronous-apply receipt guard without changing its
# machine declarations, grant mappings, credential paths or entrypoint.
_shared = Path(__file__).resolve().parents[2] / "authentik_machine_identities/filter_plugins/machine_identities.py"
_loader = importlib.util.spec_from_file_location("_operator_apply_receipts", _shared)
assert _loader is not None and _loader.loader is not None
_receipts = importlib.util.module_from_spec(_loader)
_loader.loader.exec_module(_receipts)

SLUG = "jocasta-laptop-interactive"
NAME = "Jocasta laptop interactive"
GROUP = "jocasta-operators"
BLUEPRINT = "homelab-infra Jocasta laptop interactive operator"
ORIGIN = "https://auth.ryzhkov.dev"
ISSUER = ORIGIN + "/application/o/" + SLUG + "/"
RESOURCE = "https://jocasta.mcp.ryzhkov.dev/mcp"
KEY = "8bbd6516-af33-4e64-a73b-caa39e692c29"
OFFLINE = "goauthentik.io/providers/oauth2/scope-offline_access"
OFFLINE_EXPRESSION = (
    "# This scope grants the application a refresh token that can be used to refresh user data\n"
    "# and let the application access authentik without the users interaction\n"
    "return {}"
)
MARKER = "# homelab-infra-jocasta-operator-v1: "
MARKER_V2 = "# homelab-infra-jocasta-operator-v2: "
# The one root-reconciled generation. This is a validation boundary, never
# a default opt-in or evidence generated from names or current observations.
ORIGINAL_GENERATION = {
    "blueprint_pk": "0f3072ce-5328-45d2-875f-86008db8b5c0",
    "content_sha512": "194e5a329845d7200615f95d1515b5644cf97928312e814aa9e9836ec317fdcc37e9bcec6aaa0dc5cf49f3d317d70804d851c042b64bb803ebbd11288ac07427",
    "status": "successful",
    "last_applied": "2026-10-24T19:45:43.219495Z",
    "last_applied_hash": "194e5a329845d7200615f95d1515b5644cf97928312e814aa9e9836ec317fdcc37e9bcec6aaa0dc5cf49f3d317d70804d851c042b64bb803ebbd11288ac07427",
    "user_pk": 4,
    "identities": {
        "read": "7da2a648-fbd8-43bc-8517-3101b7b9b0d9",
        "write": "fb142251-4a90-4d20-96a1-2a618e63a8f2",
        "group": "f86de0ad-4c9b-59cb-ab14-f691449f4353",
        "policy": "dc6e5831-77bb-4592-b57e-f160580ce466",
        "provider": 105,
        "application": "a3586abb-8f76-40b3-a6ca-dea7c8df0a02",
        "binding-group": "603ae539-4343-551e-80de-3a175a092b6d",
        "binding-policy": "e4d95b6f-5fe7-508a-a923-94ea1d67b09a"
    }
}
REDIRECTS = [
    "http://127.0.0.1:43181/callback/bbdouGp56E53",
    "http://localhost:43182/callback",
    "http://127.0.0.1:43183/callback",
]
ENDPOINTS = {
    "provider": "/providers/oauth2/?page_size=500",
    "application": "/core/applications/?superuser_full_list=true&page_size=500",
    "group": "/core/groups/?include_users=true&page_size=500",
    "mapping": "/propertymappings/provider/scope/?page_size=500",
    "policy": "/policies/expression/?page_size=500",
    "binding": "/policies/bindings/?page_size=500",
    "flow": "/flows/instances/?page_size=500",
}
MODELS = {
    "provider": "authentik_providers_oauth2.oauth2provider",
    "application": "authentik_core.application",
    "group": "authentik_core.group",
    "mapping": "authentik_providers_oauth2.scopemapping",
    "policy": "authentik_policies_expression.expressionpolicy",
    "binding": "authentik_policies.policybinding",
}


def refuse(message):
    raise AnsibleFilterError("Jocasta operator: " + message)


class Ref:
    def __init__(self, tag, value):
        self.tag, self.value = tag, value


class Dumper(yaml.SafeDumper):
    pass


def _reference(dumper, value):
    if isinstance(value.value, list):
        return dumper.represent_sequence(value.tag, value.value, flow_style=True)
    return dumper.represent_scalar(value.tag, value.value)


Dumper.add_representer(Ref, _reference)


def human_check(pk, name):
    return (
        f"{name} is not None and {name}.is_active and {name}.type == 'internal' "
        f"and {name}.pk == {pk} and "
        f"{name}.groups.filter(name={GROUP!r}, is_superuser=False).exists()"
    )


def operator_spec(pk, state="present", ownership=None):
    if not isinstance(pk, int) or isinstance(pk, bool) or not 0 < pk < 2**31:
        refuse("operator_user_pk must be an explicit positive integer from operator custody")
    pk = int(pk)  # Strip Ansible 2.19 scalar tags after validating the integer.
    if state not in ("present", "absent"):
        refuse("state must be present or absent")
    check = human_check(pk, "user")
    read = (
        f"if not ({check}):\n    return {{}}\n"
        f"if provider.name != {NAME!r} or provider.client_id != {SLUG!r}:\n    return {{}}\n"
        f"if provider.application.slug != {SLUG!r}:\n    return {{}}\n"
        f"return {{'aud': {RESOURCE!r}, 'jocasta_operator': True}}"
    )
    policy = (
        "import re\n"
        f"if not ({human_check(pk, 'request.user')}):\n    return False\n"
        "ctx = request.context\n"
        "challenge = ctx.get('oauth_code_challenge')\n"
        "scopes = ctx.get('oauth_scopes', set())\n"
        "return (ctx.get('oauth_grant_type') == 'authorization_code'\n"
        "    and ctx.get('oauth_response_type') == 'code'\n"
        "    and ctx.get('oauth_code_challenge_method') == 'S256'\n"
        "    and isinstance(challenge, str)\n"
        "    and re.fullmatch(r'[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]', challenge) is not None\n"
        "    and isinstance(scopes, (set, list, tuple))\n"
        "    and 'jocasta.read' in scopes\n"
        "    and set(scopes) <= {'jocasta.read', 'jocasta.write', 'offline_access'}\n"
        f"    and ctx.get('oauth_redirect_uri') in {REDIRECTS!r})"
    )
    key = lambda item: str(uuid5(UUID("07d31d49-70c9-4ee4-bf1e-8bc68e49a338"), item))
    ref = lambda item: {"_tag": "!KeyOf", "_value": item}
    find = lambda model, field, value: {"_tag": "!Find", "_value": [model, [field, value]]}
    objects = [
        {"id": "read", "kind": "mapping", "pk": key("read"),
         "attrs": {"name": NAME + " read", "scope_name": "jocasta.read",
                   "description": "Read the full Jocasta store", "expression": read}},
        {"id": "write", "kind": "mapping", "pk": key("write"),
         "attrs": {"name": NAME + " write", "scope_name": "jocasta.write",
                   "description": "Edit the full Jocasta store", "expression": "return {}"}},
        {"id": "group", "kind": "group", "pk": key("group"),
         "attrs": {"name": GROUP, "is_superuser": False, "roles": [], "parents": [],
                   "users": [find("authentik_core.user", "pk", pk)]}},
        {"id": "policy", "kind": "policy", "pk": key("policy"),
         "attrs": {"name": NAME + " authorization", "expression": policy, "execution_logging": False}},
        {"id": "provider", "kind": "provider", "pk": None,
         "attrs": {"name": NAME, "client_id": SLUG, "client_type": "public",
                   "grant_types": ["authorization_code", "refresh_token"],
                   "redirect_uris": [{"matching_mode": "strict", "url": url,
                                      "redirect_uri_type": "authorization"} for url in REDIRECTS],
                   "authorization_flow": find("authentik_flows.flow", "slug",
                                               "default-provider-authorization-explicit-consent"),
                   "invalidation_flow": find("authentik_flows.flow", "slug", "default-provider-invalidation-flow"),
                   "access_code_validity": "seconds=60", "access_token_validity": "seconds=300",
                   "refresh_token_validity": "days=30", "refresh_token_threshold": "seconds=0",
                   "include_claims_in_id_token": True, "issuer_mode": "per_provider",
                   "signing_key": find("authentik_crypto.certificatekeypair", "kp_uuid", KEY),
                   "encryption_key": None,
                   "property_mappings": [ref("read"), ref("write"),
                       find("authentik_providers_oauth2.scopemapping", "managed", OFFLINE)]}},
        {"id": "application", "kind": "application", "pk": key("application"),
         "attrs": {"slug": SLUG, "name": NAME, "provider": ref("provider"),
                   "policy_engine_mode": "all", "meta_launch_url": "blank://blank"}},
    ]
    for index, target in enumerate(("group", "policy")):
        objects.append({"id": "binding-" + target, "kind": "binding", "pk": key("binding-" + target),
                        "attrs": {"target": ref("application"), target: ref(target),
                                  "enabled": True, "negate": False, "order": index,
                                  "failure_result": False, "timeout": 30, "expiring": False}})
    spec = {"user_pk": pk, "state": state, "objects": objects}
    if ownership is not None:
        ownership = operator_recovery(ownership)
        if pk != ownership["user_pk"]:
            refuse("recovery human PK differs")
        for obj in objects:
            obj["pk"] = ownership["identities"][obj["id"]]
        spec["ownership"] = ownership
    return spec


def operator_blueprint(spec):
    # Facts must contain only Ansible-supported plain data. Reconstruct native
    # scalars and materialise YAML tag objects only inside this renderer.
    spec = operator_spec(spec["user_pk"], str(spec["state"]), spec.get("ownership"))
    def tagged(value):
        if isinstance(value, dict):
            if set(value) == {"_tag", "_value"}:
                return Ref(value["_tag"], value["_value"])
            return {k: tagged(v) for k, v in value.items()}
        if isinstance(value, list):
            return [tagged(v) for v in value]
        return value
    entries = []
    objects = spec["objects"] if spec["state"] == "present" else list(reversed(spec["objects"]))
    for obj in objects:
        ident = {"pk": obj["pk"]} if obj["pk"] else {"name": NAME}
        entry = {"model": MODELS[obj["kind"]], "id": obj["id"], "identifiers": ident}
        if spec["state"] == "present":
            entry["attrs"] = obj["attrs"]
        else:
            entry["state"] = "absent"
        entries.append(entry)
    doc = {"version": 1, "metadata": {"name": BLUEPRINT, "labels": {
        "blueprints.goauthentik.io/description": "Opt-in human operator OAuth owned by homelab-infra"}},
        "entries": entries}
    marker = {"user_pk": spec["user_pk"], "state": spec["state"]}
    prefix = MARKER
    if "ownership" in spec:
        marker["ownership"] = spec["ownership"]
        prefix = MARKER_V2
    return prefix + json.dumps(marker, sort_keys=True) + "\n" + yaml.dump(
        tagged(doc), Dumper=Dumper, sort_keys=False).strip()


def operator_recovery(value):
    """Only root's explicit receipt for the reconciled original operation."""
    if not isinstance(value, dict) or set(value) != set(ORIGINAL_GENERATION):
        refuse("recovery must have the closed original-generation schema")
    ids = value.get("identities")
    if not isinstance(ids, dict) or set(ids) != set(ORIGINAL_GENERATION["identities"]):
        refuse("recovery must pin all eight identities")
    for name, pk in {**ids, "blueprint_pk": value.get("blueprint_pk")}.items():
        if name == "provider":
            if not isinstance(pk, int) or isinstance(pk, bool) or not 0 < pk < 2**31:
                refuse("recovery provider PK must be a positive integer")
        else:
            try:
                if not isinstance(pk, str) or str(UUID(pk)) != pk:
                    raise ValueError()
            except (ValueError, AttributeError, TypeError):
                refuse("recovery UUID must be canonical: " + name)
    if (not isinstance(value.get("user_pk"), int) or isinstance(value["user_pk"], bool)
            or value != ORIGINAL_GENERATION):
        refuse("recovery differs from the exact root-reconciled original generation")
    return json.loads(json.dumps(value))


def content_spec(content):
    try:
        line = content.splitlines()[0]
        if line.startswith(MARKER_V2):
            marker = json.loads(line[len(MARKER_V2):])
            if set(marker) != {"user_pk", "state", "ownership"}:
                raise ValueError("unexpected v2 marker fields")
            old = operator_spec(marker["user_pk"], marker["state"], marker["ownership"])
        else:
            if not line.startswith(MARKER):
                raise ValueError("unknown ownership marker")
            marker = json.loads(line[len(MARKER):])
            if set(marker) != {"user_pk", "state"}:
                raise ValueError("unexpected v1 marker fields")
            old = operator_spec(marker["user_pk"], marker["state"])
        if content != operator_blueprint(old):
            raise ValueError("noncanonical ownership content")
        return old
    except (ValueError, KeyError, IndexError, AssertionError, AttributeError, TypeError):
        refuse("foreign or changed blueprint ownership, PK binding or content; inspect without replay")


def operator_owned_spec(spec, instance, recovery=None):
    if not instance:
        refuse("fresh bootstrap is unqualified; an existing receipt-bound generation is required")
    old = content_spec(instance["content"])
    if "ownership" in old:
        if recovery is not None:
            refuse("recovery transition cannot repeat on a changed generation")
        ownership = old["ownership"]
        if old["state"] == "absent" and spec["state"] == "present":
            refuse("retired generation cannot be recreated without qualified allocation enrollment")
    else:
        ownership = operator_recovery(recovery)
        if spec["state"] != "present":
            refuse("original recovery only migrates ownership of the present generation")
        if (instance["content"] != operator_blueprint(operator_spec(4))
                or _receipts.sha512(instance["content"].encode()).hexdigest() != ownership["content_sha512"]
                or any(instance.get(field) != ownership[expected] for field, expected in (
                    ("pk", "blueprint_pk"), ("status", "status"),
                    ("last_applied", "last_applied"),
                    ("last_applied_hash", "last_applied_hash")))):
            refuse("original recovery receipt changed; no transition is authorized")
    return operator_spec(spec["user_pk"], spec["state"], ownership)


def operator_receipt_dir(value):
    if (not isinstance(value, str) or not value.startswith("/")
            or str(Path(value)) != value or ".." in Path(value).parts
            or Path(value).is_relative_to("/tmp")):
        refuse("supply a fresh absolute private receipt directory on disk")
    return value


def rows(body):
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        refuse("incomplete API collection")
    p = body.get("pagination", {})
    found = body["results"]
    if (p.get("count") != len(found) or p.get("current") != 1
            or p.get("total_pages") not in (0, 1)
            or any(not isinstance(row, dict) or "pk" not in row for row in found)
            or len(found) > 500
            or len({str(row["pk"]) for row in found}) != len(found)):
        refuse("incomplete or ambiguous API collection; do not assume absence")
    return found


def operator_instance(body, spec, recovery=None):
    instances = [x for x in rows(body) if x.get("name") == BLUEPRINT]
    if len(instances) > 1:
        refuse("duplicate blueprint")
    if not instances:
        return {}
    instance = instances[0]
    content = instance.get("content", "")
    old = content_spec(content)
    if (old["user_pk"] != spec["user_pk"] or instance.get("path") != ""
            or instance.get("context") != {} or instance.get("enabled") is not True):
        refuse("foreign or changed blueprint ownership, PK binding or content; inspect without replay")
    applied = _receipts._time(instance.get("last_applied"), "operator previous terminal receipt")
    if "ownership" in old and (
            instance.get("pk") != old["ownership"]["blueprint_pk"]
            or applied <= _receipts._time(old["ownership"]["last_applied"], "operator origin receipt")):
        refuse("changed ownership record or nonfresh migrated receipt")
    if recovery is not None:
        operator_owned_spec(spec, instance, recovery)
    # Unknown/pending and failed imports cannot be safely replayed by a new run.
    if (instance.get("status") != "successful"
            or instance.get("last_applied_hash") != _receipts.sha512(content.encode()).hexdigest()):
        refuse("previous apply is uncertain or failed; recover the original operation without replay")
    return instance


def collections(results):
    if not isinstance(results, list) or len(results) != len(ENDPOINTS):
        refuse("missing or duplicate API collection")
    out = {r["item"]["kind"]: rows(r["json"]) for r in results}
    if set(out) != set(ENDPOINTS):
        refuse("missing API collection")
    return out


def resolve(value, found, data, spec):
    if isinstance(value, dict) and set(value) == {"_tag", "_value"}:
        if value["_tag"] == "!KeyOf":
            return found.get(value["_value"], {}).get("pk")
        model, (field, expected) = value["_value"]
        if model == "authentik_core.user":
            return spec["user_pk"]
        if model == "authentik_crypto.certificatekeypair":
            return KEY
        if model == MODELS["mapping"]:
            return next((x["pk"] for x in data["mapping"] if x.get(field) == expected), None)
        if model == "authentik_flows.flow":
            matches = [x["pk"] for x in data["flow"] if x.get(field) == expected]
            if len(matches) != 1:
                refuse("required default flow is missing or ambiguous")
            return matches[0]
        refuse("unrecognised blueprint reference")
    if isinstance(value, list):
        return [resolve(v, found, data, spec) for v in value]
    return value


def operator_guard(spec, instance, results, user, key, verify=False):
    data = collections(results)
    for row in data["provider"]:
        if not isinstance(row.get("property_mappings"), list):
            refuse("provider scope references are uncertain")
    for row in data["application"]:
        if ("provider" not in row or not isinstance(row.get("backchannel_providers"), list)
                or any(not isinstance(pk, int) or isinstance(pk, bool) for pk in row["backchannel_providers"])):
            refuse("application provider references are uncertain")
    for row in data["binding"]:
        if any(field not in row for field in ("target", "group", "policy", "user")):
            refuse("policy binding references are uncertain")
    if "ownership" in spec:
        # Direct filter callers receive the same receipt checks as the role.
        operator_instance({"results": [instance], "pagination": {
            "count": 1, "current": 1, "total_pages": 1}}, spec)
        previous = content_spec(instance["content"])
        if previous.get("ownership") != spec["ownership"]:
            operator_owned_spec(spec, instance, spec["ownership"])
    old_present = bool(instance and content_spec(instance["content"])["state"] == "present")
    found = {}
    for obj in spec["objects"]:
        candidates = [row for row in data[obj["kind"]]
                      if (obj["pk"] is not None and str(row["pk"]) == str(obj["pk"]))
                      or any(row.get(field) == obj["attrs"][field]
                             for field in ("name", "slug", "client_id") if field in obj["attrs"])]
        if len(candidates) > 1:
            refuse("colliding " + obj["id"])
        current = candidates[0] if candidates else {}
        if current:
            if not old_present and not (verify and spec["state"] == "present"):
                refuse("refusing foreign " + obj["id"])
            if obj["id"] != "provider" and obj["pk"] and str(current["pk"]) != str(obj["pk"]):
                refuse("foreign PK for " + obj["id"])
            for field in ("name", "slug", "client_id"):
                if field in obj["attrs"] and current.get(field) != obj["attrs"][field]:
                    refuse("changed identity for " + obj["id"])
        if old_present and not current and not (verify and spec["state"] == "absent"):
            if obj["id"] == "application" and found.get("provider"):
                refuse("operator provider lacks the owned application\'s exact reference; reconcile before writes")
            refuse("missing owned object: " + obj["id"])
        found[obj["id"]] = current

    provider, app, group, policy = [found[x] for x in ("provider", "application", "group", "policy")]
    if group and (group.get("is_superuser") is not False or group.get("roles") != []
                  or group.get("parents") != [] or not isinstance(group.get("users_obj"), list)
                  or any(u.get("pk") != spec["user_pk"] for u in group["users_obj"])):
        refuse("group privilege or membership drift")
    # Names and a successful old blueprint receipt cannot establish ownership
    # of a replacement provider. Require the owned application's live exact
    # reference before any mutation, including name-selected retirement.
    if provider and (not app or app.get("provider") != provider["pk"]):
        refuse("operator provider lacks the owned application's exact reference; reconcile before writes")
    expected_provider = next(x["pk"] for x in spec["objects"] if x["id"] == "provider")
    if provider and expected_provider is not None and (
            not isinstance(provider["pk"], int) or isinstance(provider["pk"], bool)
            or provider["pk"] != expected_provider):
        refuse("foreign PK for provider")
    if app and app.get("provider") not in (None, provider.get("pk")):
        refuse("operator application is bound to a foreign provider")
    owned_bindings = {x["pk"] for x in spec["objects"] if x["kind"] == "binding"}
    for target in ("group", "policy"):
        binding = found["binding-" + target]
        if binding and (not app or not found[target] or binding.get("target") != app["pk"]
                        or binding.get(target) != found[target]["pk"]
                        or any(binding.get(field) is not None
                               for field in ("user", "group", "policy") if field != target)):
            refuse("changed owned binding references")
    for binding in data["binding"]:
        if (binding.get("target") == app.get("pk") and app
                or binding.get("group") == group.get("pk") and group
                or binding.get("policy") == policy.get("pk") and policy):
            if str(binding["pk"]) not in owned_bindings or binding.get("target") != app.get("pk") or not app:
                refuse("foreign policy binding")
    if provider:
        for other in data["application"]:
            if (other.get("provider") == provider["pk"] or provider["pk"] in other.get("backchannel_providers", [])):
                if other["pk"] != app.get("pk"):
                    refuse("foreign application uses operator provider")
    mappings = {found[x].get("pk") for x in ("read", "write")} - {None}
    for other in data["provider"]:
        if other["pk"] != provider.get("pk") and mappings.intersection(other.get("property_mappings", [])):
            refuse("foreign provider uses operator mappings")
    if spec["state"] == "present":
        if (user.get("pk") != spec["user_pk"] or user.get("type") != "internal" or user.get("is_active") is not True):
            refuse("bound user must currently be the exact active internal human")
        if key.get("private_key_available") is not True or key.get("key_type") != "rsa" or key.get("pk") != KEY:
            refuse("fixed signing key lacks RSA/private-key metadata")
        offline = [x for x in data["mapping"] if x.get("managed") == OFFLINE and x.get("scope_name") == "offline_access"]
        if len(offline) != 1 or offline[0].get("expression", "").strip() != OFFLINE_EXPRESSION:
            refuse("built-in offline_access mapping is missing or changed")
    if verify or old_present:
        for obj in spec["objects"]:
            current = found[obj["id"]]
            if verify and spec["state"] == "absent":
                if current:
                    refuse("owned object remains after removal: " + obj["id"])
                continue
            if not current:
                refuse("missing applied object: " + obj["id"])
            for field, value in obj["attrs"].items():
                if field == "users":
                    actual = sorted(u["pk"] for u in current["users_obj"])
                    expected = [spec["user_pk"]]
                else:
                    actual, expected = current.get(field), resolve(value, found, data, spec)
                    if field == "property_mappings":
                        if not isinstance(actual, list) or not isinstance(expected, list) or None in expected:
                            refuse("missing or malformed provider scope references")
                        actual, expected = sorted(actual), sorted(expected)
                if actual != expected:
                    refuse("applied field differs: " + obj["id"] + "." + field)
            if obj["kind"] == "binding":
                for field in ("user", "group", "policy"):
                    if field not in obj["attrs"] and current.get(field) is not None:
                        refuse("extra binding authority")
    return True


def operator_terminal(instance, content, expected=None):
    if instance.get("content") != content:
        refuse("blueprint content changed during verification")
    old = content_spec(content)
    operator_instance({"results": [instance], "pagination": {
        "count": 1, "current": 1, "total_pages": 1}}, old)
    if expected is not None and any(instance.get(field) != expected.get(field) for field in (
            "pk", "name", "content", "path", "context", "enabled",
            "status", "last_applied", "last_applied_hash")):
        refuse("terminal ownership generation changed during verification")
    return True


class FilterModule:
    def filters(self):
        return {
            "jocasta_operator_spec": operator_spec,
            "jocasta_operator_blueprint": operator_blueprint,
            "jocasta_operator_instance": operator_instance,
            "jocasta_operator_owned_spec": operator_owned_spec,
            "jocasta_operator_receipt_dir": operator_receipt_dir,
            "jocasta_operator_guard": operator_guard,
            "jocasta_operator_apply_state": _receipts.machine_identity_apply_state,
            "jocasta_operator_api": _receipts.machine_identity_api_url,
            "jocasta_operator_terminal": operator_terminal,
        }
