"""Plan, ownership and verification rules for roles/authentik_machine_identities.

Pure functions over plain data, so every refusal can be tested without Ansible
or Authentik. Messages name objects and fields, never a credential.

The blueprint's stored content is the ownership record: its "owned" comment
line lists every object and its desired state. Only a stored present entry
confers ownership; absent entries are deletion requests. Other colliding names
are refused, never adopted or deleted.
"""
from hashlib import sha256, sha512
import json
import posixpath
import re
from datetime import datetime, timedelta
from typing import NoReturn
from urllib.parse import quote, urlsplit

try:
    from ansible.errors import AnsibleFilterError
except ImportError:  # the unit tests import this file without Ansible
    class AnsibleFilterError(Exception):
        pass

OWNED_PREFIX = "# homelab-infra-owned: "
MAX_LIFETIME = "goauthentik.io/user/token-maximum-lifetime"
# A present identity whose app password expires sooner than this is refused:
# renew it (a later expiry) or rotate it (a higher generation) first.
MIN_REMAINING = timedelta(days=1)

HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,49}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}\Z")
CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
LOGIN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
NODENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}\Z")
HOME = re.compile(r"/[A-Za-z0-9._/-]{1,255}\Z")

# Fields every service application and every account declaration has. The
# service profile below adds its own.
APPLICATION_KEYS = {"state", "slug", "name", "client_id", "signing_key", "group"}
ACCOUNT_KEYS = {"host", "profile", "generation", "expires", "state"}
STATES = ("present", "absent")

# How each kind of object is looked up, and the field that names it.
LOOKUPS = {
    "scopemapping": ("/propertymappings/provider/scope/?name={}", "name"),
    "provider": ("/providers/oauth2/?name={}", "name"),
    "client_id": ("/providers/oauth2/?client_id={}", "client_id"),
    "application": ("/core/applications/?slug={}&superuser_full_list=true", "slug"),
    "group": ("/core/groups/?name={}&include_users=true", "name"),
    "user": ("/core/users/?username={}", "username"),
    "token": ("/core/tokens/?identifier={}", "identifier"),
}
MISSING = object()


def _refuse(message) -> NoReturn:
    raise AnsibleFilterError("machine identities: " + message)


def _keys(value, expected, name):
    if not isinstance(value, dict):
        _refuse(f"{name} must be a mapping")
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        _refuse(f"{name} needs exactly {sorted(expected)} (missing {missing}, unknown {extra})")
    return value


def _match(pattern, value, name):
    if not isinstance(value, str) or not pattern.match(value):
        _refuse(f"{name} is not valid")
    return value


def _time(value, name):
    if not isinstance(value, str):
        _refuse(f"{name} must be an ISO 8601 string with a time zone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _refuse(f"{name} must be an ISO 8601 string with a time zone")
    if parsed.tzinfo is None:
        _refuse(f"{name} must name a time zone")
    return parsed


def machine_identity_api_url(value):
    """Credential transport: one canonical HTTPS origin and the fixed API path."""
    if not isinstance(value, str) or not re.fullmatch(
            r"https://(?:[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?|\[[0-9a-f:]+\])"
            r"(?::[1-9][0-9]{0,4})?/api/v3", value):
        _refuse("authentik_url must be a canonical HTTPS origin followed by /api/v3")
    try:
        parts = urlsplit(value)
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
    except ValueError:
        _refuse("authentik_url has an invalid origin")
    return value


def _complete_rows(body, label, field):
    """Require a complete, well-formed collection before trusting absence."""
    rows = body.get("results") if isinstance(body, dict) else None
    pagination = body.get("pagination") if isinstance(body, dict) else None
    if (not isinstance(rows, list) or not isinstance(pagination, dict)
            or type(pagination.get("count")) is not int or pagination["count"] < 0
            or any(not isinstance(row, dict) or not isinstance(row.get(field), str)
                   or not row[field] for row in rows)):
        _refuse(f"{label}: lookup answered in an unexpected shape")
    if (pagination["count"] != len(rows) or body.get("next")
            or pagination.get("next") or pagination.get("current", 1) != 1
            or pagination.get("total_pages", 1) not in (0, 1)):
        _refuse(f"{label}: lookup did not return every candidate")
    return rows


def machine_identity_blueprint(result, name):
    """Validate the entire lookup before accepting zero or one exact match."""
    if not isinstance(result, dict) or result.get("status") != 200:
        _refuse("blueprint: lookup answered in an unexpected shape")
    rows = _complete_rows(result.get("json"), "blueprint", "name")
    if any(not isinstance(row.get("pk"), str) or not UUID.fullmatch(row["pk"])
           or not isinstance(row.get("content"), str) or not row["content"] for row in rows):
        _refuse("blueprint: lookup answered in an unexpected shape")
    exact = [row for row in rows if row["name"] == name]
    if len(exact) > 1:
        _refuse("blueprint: more than one object has this name")
    return exact[0] if exact else {}


def _resource(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{1,512}", value):
        _refuse("access resource must be a printable URL")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or "*" in value or "\\" in value):
        _refuse("access resource must be an https URL without credentials, query or fragment")
    return value


# --- Closed service profiles ----------------------------------------------
# No caller-selected scope, attribute, claim map or mapping code.

SCOPE = "jocasta.read"
TOKEN_SECONDS = 300
# The attribute the scope mapping reads. Only an administrator can change a
# service account's attributes.
ATTRIBUTE = "chaldea_jocasta"
PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


def expression(access):
    """The scope mapping's Python. The audience is fixed here, the project comes
    from the account's own attributes, and anything unexpected yields no claims
    (the token then keeps Authentik's default audience, which Jocasta refuses)."""
    return "\n".join([
        "# Rendered by homelab-infra roles/authentik_machine_identities.",
        f"grant = user.attributes.get({json.dumps(ATTRIBUTE)}) if user else None",
        "if (not isinstance(grant, dict) or str(user.type) != \"service_account\"",
        f"        or not ak_is_group_member(user, name={json.dumps(access['group'])})):",
        "    return {}",
        "project = grant.get(\"project\")",
        "if not isinstance(project, str) or not project:",
        "    return {}",
        f"return {{\"aud\": {json.dumps(access['resource'])}, \"jocasta_projects\": [project]}}",
    ])


def _jocasta_application(access):
    """Validate Jocasta's own application fields; add its mapping name and code."""
    if access["scope"] != SCOPE:
        _refuse(f"access scope must be {SCOPE}")
    if access["token_seconds"] != TOKEN_SECONDS or isinstance(access["token_seconds"], bool):
        _refuse(f"access token_seconds must be {TOKEN_SECONDS}")
    _resource(access["resource"])
    return {"mapping": f"{access['name']}: {SCOPE}", "expression": expression(access)}


def _jocasta_grant(item, name):
    """An account's Jocasta grant: its one project, stored as an attribute."""
    project = _match(PROJECT, item["project"], name + " project")
    return {"project": project}, {ATTRIBUTE: {"project": project}}


SERVICE = {
    "name": "jocasta",
    "application_keys": {"resource", "scope", "token_seconds"},
    "account_keys": {"project"},
    "application": _jocasta_application,
    "grant": _jocasta_grant,
}


OV_ROOT = re.compile(r"viking://resources/(?:[A-Za-z0-9_@+.,=-]+/)*[A-Za-z0-9_@+.,=-]+\Z")
OBSERVER_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
HA_ENTITY = re.compile(r"(light|switch|sensor|binary_sensor)\.[a-z0-9_]{1,96}\Z")
HA_ACTION = re.compile(r"(state\.read|light\.control|switch\.control)\Z")


def _bounded_grants(value, name, pattern, maximum, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _refuse(f"{name} must be an explicit list of {minimum}..{maximum} grants")
    for grant in value:
        _match(pattern, grant, name)
    if len(set(value)) != len(value):
        _refuse(f"{name} has duplicate grants")
    return list(value)


def _ov_grant(item, name):
    roots = _bounded_grants(item["ov_namespaces"], name + " ov_namespaces", OV_ROOT, 16, 1)
    if any(len(r) > 1024 or any(p in (".", "..") for p in r.split("/")) for r in roots):
        _refuse(name + " ov_namespaces must be canonical roots")
    if any(a == b or a.startswith(b + "/") or b.startswith(a + "/")
           for n, a in enumerate(roots) for b in roots[n + 1:]):
        _refuse(name + " ov_namespaces must not overlap")
    grant = {"ov_namespaces": roots}
    return grant, {"chaldea_ov": grant}


def _homelab_grant(item, name):
    grant = {key: _bounded_grants(item[key], name + " " + key, OBSERVER_ID, 128)
             for key in ("homelab_services", "homelab_hosts")}
    return grant, {"chaldea_homelab": grant}


def _ha_grant(item, name):
    grant = {"ha_entities": _bounded_grants(item["ha_entities"], name + " ha_entities", HA_ENTITY, 64, 1),
             "ha_actions": _bounded_grants(item["ha_actions"], name + " ha_actions", HA_ACTION, 3, 1)}
    # Also preserve validID's explicit length bound, independent of its regex.
    if any(len(entity) > 112 for entity in grant["ha_entities"]):
        _refuse(name + " ha_entities exceed the verifier ID bound")
    return grant, {"chaldea_ha": grant}


HOLONET_FIELDS = {"holonet_server", "holonet_libraries", "holonet_catalog_users",
                  "holonet_history_users", "holonet_target_users", "holonet_actions", "holonet_namespace"}


def _holonet_grant(item, name):
    # Initial catalog-only profile deliberately cannot enroll broader roadmap actions.
    grant = {key: item[key] for key in sorted(HOLONET_FIELDS)}
    _match(re.compile(r"[0-9a-f]{32}\Z"), grant["holonet_server"], name + " server")
    _match(re.compile(r"[a-z][a-z0-9-]{0,31}\Z"), grant["holonet_namespace"], name + " namespace")
    grant["holonet_libraries"] = _bounded_grants(grant["holonet_libraries"], name + " libraries",
                                                re.compile(r"[1-9][0-9]{0,18}\Z"), 1, 1)
    if int(grant["holonet_libraries"][0]) > 9223372036854775807:
        _refuse(name + " library ID exceeds int64")
    grant["holonet_catalog_users"] = _bounded_grants(grant["holonet_catalog_users"], name + " users",
                                                    re.compile(r"[0-9a-f]{32}\Z"), 1, 1)
    if (grant["holonet_history_users"] != [] or grant["holonet_target_users"] != []
            or grant["holonet_actions"] != ["catalog.read"]):
        _refuse(name + " requires catalog.read only and empty history/target grants")
    return grant, {"chaldea_holonet": grant}


def _bounded_expression(access, service, scope=None):
    # The allowlist is embedded from the validated operator declaration, never
    # from request parameters. Require an exact account grant and group membership.
    expected = access["account_grants"]
    lines = [
        "# Rendered by homelab-infra roles/authentik_machine_identities.",
        f"expected = {expected!r}",
        f"grant = user.attributes.get('chaldea_{service}') if user else None",
        "if (not user or str(user.type) != 'service_account'",
        f"        or not ak_is_group_member(user, name={access['group']!r})",
        "        or user.username not in expected or grant != expected[user.username]):",
        "    return {}",
    ]
    if service == "holonet":
        lines += [
            "if token.scope != ['holonet.read']:",
            "    return {}",
            f"return dict(grant, aud={access['resource']!r})",
        ]
    elif service == "ha":
        # Authentik 2026.8.4 get_claims evaluates requested mappings with token
        # and deepmerge appends arrays. Elect one selected mapping to emit both
        # arrays exactly once, even when both scopes are requested. token.scope
        # is BaseGrantModel's list, not an untrusted HTTP request parameter.
        lines += [
            "requested = token.scope",
            f"allowed = {list(access['scope_mappings'])!r}",
            "if (not isinstance(requested, list) or not requested",
            "        or any(s not in allowed for s in requested)",
            "        or len(set(requested)) != len(requested)):",
            "    return {}",
            f"if {scope!r} != sorted(requested)[0]:",
            "    return {}",
            "actions = [a for a in grant['ha_actions']",
            "           if ('ha.read' if a == 'state.read' else 'ha.control') in requested]",
            "if not actions:",
            "    return {}",
            f"return {{'aud': {access['resource']!r}, 'ha_entities': grant['ha_entities'], 'ha_actions': actions}}",
        ]
    else:
        lines.append(f"return dict(grant, aud={access['resource']!r})")
    return "\n".join(lines)


SERVICES = {
    "holonet": {"name": "holonet", "account_keys": HOLONET_FIELDS, "scope": "holonet.read",
                "resource": "https://media.mcp.ryzhkov.dev/mcp", "grant": _holonet_grant},
    "jocasta": SERVICE,
    "ov": {"name": "ov", "account_keys": {"ov_namespaces"}, "scope": "ov.read",
           "resource": "https://ov.mcp.ryzhkov.dev/mcp", "grant": _ov_grant},
    "ha": {"name": "ha", "account_keys": {"ha_entities", "ha_actions"}, "scope": "ha.read",
           "resource": "https://ha.mcp.ryzhkov.dev/mcp", "grant": _ha_grant},
    "homelab": {"name": "homelab", "account_keys": {"homelab_services", "homelab_hosts"},
                "scope": "homelab.read", "resource": "https://homelab.mcp.ryzhkov.dev/mcp",
                "grant": _homelab_grant},
}


def _application(raw, service):
    profile = SERVICES[service]
    extra = {"issuer", "jwks_url"} if service != "jocasta" else set()
    access = dict(_keys(raw, APPLICATION_KEYS | SERVICE["application_keys"] | extra,
                        f"{service} access"))
    if access["state"] not in STATES:
        _refuse("access state must be present or absent")
    for field, pattern in (("slug", SLUG), ("name", NAME), ("group", NAME),
                           ("client_id", CLIENT_ID), ("signing_key", UUID)):
        _match(pattern, access[field], "access " + field + (" (an existing keypair's kp_uuid)" if field == "signing_key" else ""))
    if service == "jocasta":
        access.update(_jocasta_application(access))
    else:
        scopes = ("ha.read", "ha.control", "ha.read ha.control", "ha.control ha.read") if service == "ha" else (profile["scope"],)
        if access["scope"] not in scopes:
            _refuse(f"access scope must be {' or '.join(scopes)}")
        if (not isinstance(access["token_seconds"], int) or isinstance(access["token_seconds"], bool)
                or access["token_seconds"] != TOKEN_SECONDS):
            _refuse(f"access token_seconds must be {TOKEN_SECONDS}")
        if access["resource"] != profile["resource"]:
            _refuse(f"{service} resource must be {profile['resource']}")
        issuer = _resource(access["issuer"])
        parts = urlsplit(issuer)
        origin = f"{parts.scheme}://{parts.netloc}"
        if issuer != f"{origin}/application/o/{access['slug']}/":
            _refuse("issuer must be the explicit per-provider URL with trailing slash")
        if access["jwks_url"] != issuer + "jwks/":
            _refuse("jwks_url must be the explicit issuer URL plus jwks/ with trailing slash")
        machine_identity_api_url(origin + "/api/v3")
        access["mapping"] = f"{access['name']}: {profile['scope']}"
    access["service"] = service
    access["scope_mappings"] = {scope: f"{access['name']}: {scope}" for scope in access["scope"].split(" ")}
    return access


def _service_specs(spec):
    """Project shared lifecycle data onto each service for exact verification."""
    for service, access in spec["services"].items():
        identities = [i for i in spec["identities"] if i["service"] == service]
        names = {("scopemapping", name) for name in access["scope_mappings"].values()}
        names.update({("provider", access["name"]), ("application", access["slug"]), ("group", access["group"])})
        for i in identities:
            names.add(("user", i["username"]))
            names.update(("token", t) for t in [i["token"]] + i["old_tokens"])
        yield {"access": access, "identities": identities,
               "objects": [o for o in spec["objects"] if tuple(o[:2]) in names]}


# --- Accounts, app passwords and their host files: service-independent -----

def _destinations(value, identities):
    """Optional operator binding of each identity host to one machine and account.

    Inventory names and ssh_user names alone cannot tell two machines apart:
    the same login and home can exist on both, and a local connection means
    whichever machine runs the play. When bound, the role checks the actual
    node name and numeric user ID on the destination before any change.
    """
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict):
        _refuse("authentik_machine_identity_destinations must be a mapping")
    declared = {item["host"] for item in identities}
    if set(value) != declared:
        _refuse("destination bindings must name exactly the declared identity hosts")
    for host, binding in value.items():
        _keys(binding, {"hostname", "uid"}, f"destination {host}")
        _match(NODENAME, binding["hostname"], f"destination {host} hostname")
        uid = binding["uid"]
        if not isinstance(uid, int) or isinstance(uid, bool) or not 0 <= uid <= 2147483646:
            _refuse(f"destination {host} uid must be a numeric user ID")
    return value


def machine_identity_spec(identities, access, hosts, max_days, now, services=None, destinations=None):
    """Validate the declared identities and derive every name the role uses.

    hosts maps each inventory host an identity names to its ssh_user, an
    optional home override and its connection. destinations optionally binds
    each of those hosts to an expected node name and numeric user ID.
    """
    if services is None or services == {}:
        services = {"jocasta": access}
    if not isinstance(services, dict) or not services or set(services) - set(SERVICES):
        _refuse("services must be a nonempty mapping of closed service profiles")
    services = {name: _application(raw, name) for name, raw in services.items()}
    for field in ("slug", "name", "group", "client_id", "resource"):
        values = [a[field] for a in services.values()]
        if len(set(values)) != len(values):
            _refuse(f"services have colliding {field}")

    if not isinstance(max_days, int) or isinstance(max_days, bool) or not 1 <= max_days <= 365:
        _refuse("authentik_machine_identity_max_days must be 1..365")
    now = _time(now, "now")
    if not isinstance(identities, list):
        _refuse("authentik_machine_identities must be a list")
    if not isinstance(hosts, dict):
        _refuse("inventory host details must be a mapping")

    out, seen_users, seen_pairs = [], set(), set()
    for n, raw in enumerate(identities):
        name = f"identity {n}"
        if not isinstance(raw, dict):
            _refuse(name + " must be a mapping")
        service = raw.get("service", "jocasta")
        if not isinstance(service, str) or service not in services:
            _refuse(name + " service must name a declared closed service profile")
        profile_spec = SERVICES[service]
        access = services[service]
        item = _keys(raw, ACCOUNT_KEYS | profile_spec["account_keys"] |
                     ({"service"} if "service" in raw else set()), name)
        host = _match(HOST, item["host"], name + " host")
        profile = _match(PROFILE, item["profile"], name + " profile")
        name = f"identity {host}/{profile}"
        grant, grant_attributes = profile_spec["grant"](item, name)
        if service == "ha":
            scopes = access["scope"].split(" ")
            for action in grant["ha_actions"]:
                needed = "ha.read" if action == "state.read" else "ha.control"
                if needed not in scopes:
                    _refuse(name + " ha_actions require the corresponding explicit scope")
        state = item["state"]
        if state not in STATES:
            _refuse(name + " state must be present or absent")
        if access["state"] == "absent" and state != "absent":
            _refuse(name + " must be absent before the shared application is removed")
        generation = item["generation"]
        if not isinstance(generation, int) or isinstance(generation, bool) or not 1 <= generation <= 1000:
            _refuse(name + " generation must be 1..1000")
        expires = _time(item["expires"], name + " expires")
        if state == "present":
            if expires < now + MIN_REMAINING:
                _refuse(name + " app password expires within a day; set a later expiry or a higher generation")
            if expires > now + timedelta(days=max_days):
                _refuse(f"{name} expiry is more than {max_days} days ahead")
        if host not in hosts:
            _refuse(name + " host is not in the inventory")
        target = hosts[host] if isinstance(hosts[host], dict) else {}
        owner = _match(LOGIN, target.get("ssh_user"), name + " host ssh_user")
        connection = target.get("connection") or "ssh"
        if connection not in ("ssh", "local"):
            _refuse(name + " host must be reached over ssh or be the local controller")
        home = target.get("home") or f"/home/{owner}"
        if not HOME.match(home) or posixpath.normpath(home) != home or "/.." in home or "/./" in home:
            _refuse(name + " home is not a normalized absolute path")
        username = f"chaldea-{host}-{profile}"
        if (host, profile) in seen_pairs or username in seen_users:
            _refuse(name + " is declared twice or its account name collides with another")
        seen_pairs.add((host, profile))
        seen_users.add(username)
        directory = f"{home}/.config/chaldea/authentik"
        out.append({
            "host": host, "profile": profile, "service": service, "grant": grant, "state": state,
            "generation": generation, "expires": item["expires"],
            "username": username, "token": f"{username}-g{generation}",
            "old_tokens": [f"{username}-g{g}" for g in range(1, generation)],
            "owner": owner, "connection": connection, "home": home,
            "ancestors": [home, f"{home}/.config", f"{home}/.config/chaldea", directory],
            "directory": directory, "secret_path": f"{directory}/{profile}.app-password",
            "grant_attributes": grant_attributes,
            "attributes": grant_attributes | {MAX_LIFETIME: f"days={max_days}",
                                              "homelab-infra": {"host": host, "profile": profile}},
        })
    for host, binding in _destinations(destinations, out).items():
        for item in out:
            if item["host"] == host:
                item["destination"] = dict(binding)

    objects = []
    for service, access in services.items():
        if service != "jocasta":
            access["account_grants"] = {i["username"]: i["grant"] for i in out
                                        if i["service"] == service and i["state"] == "present"}
            access["expression"] = _bounded_expression(access, service, next(iter(access["scope_mappings"])))
        access["scope_expressions"] = {
            scope: _bounded_expression(access, service, scope) if service == "ha" else access["expression"]
            for scope in access["scope_mappings"]}
        for mapping in access["scope_mappings"].values():
            objects.append(["scopemapping", mapping, access["state"]])
        for kind, ident in (("provider", access["name"]), ("application", access["slug"]),
                            ("group", access["group"])):
            objects.append([kind, ident, access["state"]])
    for item in out:
        objects.append(["user", item["username"], item["state"]])
        objects.append(["token", item["token"], item["state"]])
        objects.extend(["token", old, "absent"] for old in item["old_tokens"])
    marker = OWNED_PREFIX + json.dumps({"version": 1, "objects": sorted(objects)},
                                       sort_keys=True, separators=(",", ":"))
    return {"access": services.get("jocasta", next(iter(services.values()))),
            "services": services, "identities": out, "objects": sorted(objects), "marker": marker}


def machine_identity_owned(content):
    """[kind, name, state] for every object a stored blueprint's content names."""
    lines = [line for line in (content or "").splitlines() if line.startswith(OWNED_PREFIX)]
    if not lines:
        if content:
            _refuse("the stored blueprint has no ownership record; inspect it before running this role")
        return []
    if len(lines) != 1:
        _refuse("the stored blueprint has more than one ownership record")
    try:
        record = json.loads(lines[0][len(OWNED_PREFIX):])
        objects = record["objects"]
        assert record["version"] == 1 and isinstance(objects, list)
        assert all(isinstance(o, list) and len(o) == 3 and o[0] in LOOKUPS and o[2] in STATES
                   for o in objects)
    except (ValueError, KeyError, TypeError, AssertionError):
        _refuse("the stored blueprint's ownership record is unreadable")
    return [list(o) for o in objects]



def machine_identity_existing_provider(spec, instance, baseline, inventory, applications, actor):
    """Authorize one existing provider detail read from a prior public baseline.

    This is an operator-confirmed prior baseline, not a creation receipt. Never
    learn or refresh it from the current HTTP response. The tooling entrypoint
    requires fresh confirmation after each completed content change.
    """
    if (not isinstance(baseline, dict)
            or set(baseline) != {"provider_pk", "application_pk", "content_sha512"}
            or not isinstance(baseline["provider_pk"], int) or isinstance(baseline["provider_pk"], bool)
            or baseline["provider_pk"] <= 0
            or not isinstance(baseline["application_pk"], str)
            or not UUID.fullmatch(baseline["application_pk"])
            or not isinstance(baseline["content_sha512"], str)
            or not re.fullmatch(r"[0-9a-f]{128}", baseline["content_sha512"])):
        _refuse("existing-only mode requires an independently confirmed prior public baseline")
    if (not isinstance(actor, dict) or actor.get("status") != 200
            or not isinstance(actor.get("json"), dict)
            or not isinstance(actor["json"].get("user"), dict)
            or actor["json"]["user"].get("is_superuser") is not True):
        _refuse("existing-only public discovery requires a superuser")
    # Exactly one closed service profile: the one existing application this
    # blueprint owns. The mode cannot create, add or remove an application.
    if len(spec["services"]) != 1 or spec["access"]["state"] != "present":
        _refuse("existing-only mode requires exactly one unchanged present application")
    access = spec["access"]
    content = instance.get("content", "") if isinstance(instance, dict) else ""
    if (not content or sha512(content.encode()).hexdigest() != baseline["content_sha512"]
            or instance.get("last_applied_hash") != baseline["content_sha512"]
            or instance.get("status") != "successful" or instance.get("enabled") is not True
            or instance.get("path") != "" or instance.get("context") != {}):
        _refuse("existing-only stored blueprint is missing, changed or not confirmed successful")
    owned = machine_identity_owned(content)
    if sorted(o for o in owned if o[0] in ("provider", "application")) != sorted([
            ["provider", access["name"], "present"],
            ["application", access["slug"], "present"]]):
        _refuse("existing-only provider and application lack original stored-present ownership")

    # Compose nodes rather than constructing arbitrary blueprint tags. Check the
    # original typed binding; a matching name alone does not authorize detail.
    import yaml
    try:
        document = yaml.compose(content, Loader=yaml.SafeLoader)

        def mapping(node):
            if not isinstance(node, yaml.MappingNode):
                raise ValueError()
            pairs = [(scalar(k), v) for k, v in node.value]
            if len({k for k, _ in pairs}) != len(pairs):
                raise ValueError()
            return dict(pairs)

        def scalar(node):
            if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
                raise ValueError()
            return node.value

        entries = mapping(document)["entries"]
        if not isinstance(entries, yaml.SequenceNode):
            raise ValueError()
        rows = [mapping(n) for n in entries.value]

        def entry(model, field, value):
            matches = [row for row in rows
                       if scalar(row["model"]) == model
                       and scalar(mapping(row["identifiers"])[field]) == value]
            if len(matches) != 1:
                raise ValueError()
            row = matches[0]
            if "state" in row and scalar(row["state"]) != "present":
                raise ValueError()
            return row

        provider = entry("authentik_providers_oauth2.oauth2provider", "name", access["name"])
        application = entry("authentik_core.application", "slug", access["slug"])
        if scalar(mapping(provider["attrs"])["client_id"]) != access["client_id"]:
            raise ValueError()
        binding = mapping(application["attrs"])["provider"]
        if (not isinstance(binding, yaml.ScalarNode) or binding.tag != "!KeyOf"
                or binding.value != scalar(provider["id"])):
            raise ValueError()
    except (yaml.YAMLError, ValueError, KeyError, TypeError, AttributeError):
        _refuse("existing-only original blueprint provider/client/application binding changed")
    if not isinstance(inventory, dict) or inventory.get("status") != 200:
        _refuse("existing-only public provider inventory is unavailable")
    providers = _complete_rows(inventory.get("json"), "public providers", "name")
    matches = [r for r in providers if r.get("name") == access["name"]]
    pinned = [r for r in providers if r.get("pk") == baseline["provider_pk"]]
    if (len(matches) != 1 or len(pinned) != 1 or matches[0] != pinned[0]
            or type(pinned[0].get("pk")) is not int
            or pinned[0].get("meta_model_name") != "authentik_providers_oauth2.oauth2provider"):
        _refuse("existing-only public provider ID, name or type differs from the prior baseline")
    if not isinstance(applications, dict) or applications.get("status") != 200:
        _refuse("existing-only public application is unavailable")
    apps = _complete_rows(applications.get("json"), "public application", "slug")
    if (len(apps) != 1 or apps[0].get("slug") != access["slug"]
            or apps[0].get("pk") != baseline["application_pk"]
            or type(apps[0].get("provider")) is not int
            or apps[0]["provider"] != baseline["provider_pk"]):
        _refuse("existing-only public application binding differs from the prior baseline")
    return {"kind": "owned_provider", "id": access["name"], "pk": baseline["provider_pk"],
            "client_id": access["client_id"],
            "path": f"/providers/oauth2/{baseline['provider_pk']}/"}


def machine_identity_existing_baseline(identities, services, max_days, now, anchor,
                                       instance, inventory, applications, actor):
    """Produce the existing-only gate's public baseline from one read-only pass.

    The independent anchor is a retained historical blueprint PK and SHA-256 of
    its successful stored content. Only content with exactly that SHA-256 can
    yield a SHA-512, so the response being checked never supplies its own
    accepted hash. The stored ownership record must name exactly the declared
    historical identities, and every condition of the existing-only gate must
    already hold for the derived values. Only IDs, hashes and fixed booleans
    are returned; no response body, expression or credential leaves here.
    """
    if (not isinstance(anchor, dict) or set(anchor) != {"blueprint_pk", "content_sha256"}
            or not isinstance(anchor["blueprint_pk"], str) or not UUID.fullmatch(anchor["blueprint_pk"])
            or not isinstance(anchor["content_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", anchor["content_sha256"])):
        _refuse("baseline read requires the retained blueprint PK and content SHA-256")
    # Hosts never affect object names; a fixed placeholder keeps this pure.
    hosts = {i.get("host"): {"ssh_user": "baseline", "connection": "ssh"}
             for i in identities if isinstance(i, dict)} if isinstance(identities, list) else {}
    spec = machine_identity_spec(identities, {}, hosts, max_days, now, services)
    content = instance.get("content") if isinstance(instance, dict) else None
    if (instance.get("pk") if isinstance(instance, dict) else None) != anchor["blueprint_pk"] \
            or not isinstance(content, str) or not content \
            or sha256(content.encode()).hexdigest() != anchor["content_sha256"]:
        _refuse("baseline read: stored blueprint PK or content differs from the retained anchor")
    if sorted(machine_identity_owned(content)) != sorted(spec["objects"]):
        _refuse("baseline read: stored ownership differs from the declared historical identities")
    rows = []
    if isinstance(inventory, dict) and inventory.get("status") == 200:
        rows = [r for r in _complete_rows(inventory.get("json"), "public providers", "name")
                if r.get("name") == spec["access"]["name"]]
    apps = []
    if isinstance(applications, dict) and applications.get("status") == 200:
        apps = _complete_rows(applications.get("json"), "public application", "slug")
    if len(rows) != 1 or type(rows[0].get("pk")) is not int or len(apps) != 1 \
            or not isinstance(apps[0].get("pk"), str):
        _refuse("baseline read: public provider or application is not exactly one object")
    baseline = {"provider_pk": rows[0]["pk"], "application_pk": apps[0]["pk"],
                "content_sha512": sha512(content.encode()).hexdigest()}
    # The same checks a later online run makes, against the derived values.
    machine_identity_existing_provider(spec, instance, baseline, inventory, applications, actor)
    return {"blueprint_pk": anchor["blueprint_pk"], "content_sha256": anchor["content_sha256"],
            "authentik_machine_identity_existing_provider_baseline": baseline,
            "stored_content_matches_anchor": True, "ownership_matches_declaration": True,
            "existing_only_gate_holds": True}


def _found(results, problems):
    """(kind, name) -> the one object with exactly that name, or None."""
    found = {}
    for result in results:
        item = result.get("item", {})
        kind, ident = item.get("kind"), item.get("id")
        body = result.get("json") if isinstance(result.get("json"), dict) else {}
        if kind == "owned_provider":
            if (result.get("status") != 200 or type(body.get("pk")) is not int
                    or body["pk"] != item.get("pk") or body.get("name") != ident
                    or body.get("client_id") != item.get("client_id")):
                _refuse("existing-only exact owned provider detail or unchanged client ID differs")
            found[("provider", ident)] = body
            found[("client_id", item["client_id"])] = [body]
            continue
        if kind == "keypair":
            found[(kind, ident)] = body if result.get("status") == 200 else None
            continue
        if result.get("status") != 200:
            problems.append(f"{kind} {ident}: lookup answered in an unexpected shape")
            found[(kind, ident)] = MISSING
            continue
        field = LOOKUPS[kind][1]
        try:
            rows = _complete_rows(body, f"{kind} {ident}", field)
        except AnsibleFilterError as exc:
            problems.append(str(exc))
            found[(kind, ident)] = MISSING
            continue
        exact = [row for row in rows if isinstance(row, dict) and row.get(field) == ident]
        if kind == "client_id":
            found[(kind, ident)] = exact
        elif len(exact) > 1:
            problems.append(f"{kind} {ident}: more than one object has this name")
            found[(kind, ident)] = MISSING
        else:
            found[(kind, ident)] = exact[0] if exact else None
    return found


def machine_identity_lookup(results, kind, ident):
    """The object a lookup found under exactly this name, or {}."""
    found = _found(results, [])
    obj = found.get((kind, ident))
    return obj if isinstance(obj, dict) else {}


def _usernames(group):
    rows = group.get("users_obj")
    if not isinstance(rows, list):
        return MISSING
    return sorted(str(row.get("username")) for row in rows if isinstance(row, dict))


def _binding_problems(spec, found, owned_names):
    """Owned objects that exist must still look like this role's own objects."""
    problems = []
    access = spec["access"]
    expected_users = {i["username"]: i for i in spec["identities"]}
    for (kind, ident), obj in found.items():
        if not isinstance(obj, dict) or (kind, ident) not in owned_names:
            continue
        if kind == "token":
            owner = (obj.get("user_obj") or {}).get("username", MISSING)
            base = ident.rsplit("-g", 1)[0]
            if owner != base or obj.get("intent") != "app_password":
                problems.append(f"token {ident}: owner or intent is not this role's; inspect it")
        elif kind == "user":
            if (obj.get("type") != "service_account" or obj.get("is_superuser") is not False
                    or obj.get("roles") != []):
                problems.append(f"user {ident}: type, superuser state or roles changed outside this role")
            group = found.get(("group", access["group"]))
            allowed = {group.get("pk")} if isinstance(group, dict) else set()
            if not isinstance(obj.get("groups"), list) or not set(obj["groups"]) <= allowed:
                problems.append(f"user {ident}: member of a group this role does not own")
        elif kind == "group":
            members = _usernames(obj)
            if (obj.get("is_superuser") is not False or obj.get("roles") != [] or obj.get("parents") != []
                    or members is MISSING or not set(members) <= set(expected_users)):
                problems.append(f"group {ident}: superuser state, roles, parents or members changed outside this role")
        elif kind == "application":
            name = (obj.get("provider_obj") or {}).get("name")
            if obj.get("provider") is not None and name != access["name"]:
                problems.append(f"application {ident}: bound to a provider this role does not own")
    return problems


def machine_identity_refusals(spec, owned, results, applications=None):
    """Reasons not to store the blueprint. Empty: storing it is safe."""
    problems = []
    found = _found(results, problems)
    recorded_state = {(k, i): s for k, i, s in owned}
    owned_state = {key: state for key, state in recorded_state.items() if state == "present"}
    desired = {(k, i): s for k, i, s in spec["objects"]}
    for (kind, ident), state in sorted(desired.items()):
        obj = found.get((kind, ident), MISSING)
        if obj is MISSING:
            continue
        if obj is not None and (kind, ident) not in owned_state:
            verb = "take over" if state == "present" else "delete"
            problems.append(f"{kind} {ident} exists but no stored blueprint of this role created it; "
                            f"refusing to {verb} it")
    for (kind, ident), state in sorted(recorded_state.items()):
        if (kind, ident) in desired:
            continue
        if state == "present":
            problems.append(f"{kind} {ident} was created by this role; declare it absent before dropping it")
        elif found.get((kind, ident)) is not None:
            problems.append(f"{kind} {ident} still exists; keep it declared absent until it is gone")
    for part in _service_specs(spec):
        names = {tuple(o[:2]) for o in part["objects"]}
        local_results = [r for r in results if (r["item"]["kind"], r["item"]["id"]) in names
                         or r["item"]["kind"] in ("client_id", "keypair", "owned_provider")]
        problems.extend(_service_refusals(part, owned, local_results, applications))
    return problems


def _service_refusals(spec, owned, results, applications):
    problems = []
    found = _found(results, problems)
    owned_state = {(k, i) for k, i, state in owned if state == "present"}
    access = spec["access"]
    clients = found.get(("client_id", access["client_id"]))
    if isinstance(clients, list) and any(row.get("name") != access["name"] for row in clients):
        problems.append(f"client_id {access['client_id']} belongs to another provider")
    if access["state"] == "present":
        key = found.get(("keypair", access["signing_key"]))
        if not isinstance(key, dict) or key.get("private_key_available") is not True:
            problems.append(f"signing key {access['signing_key']} is missing or has no private key")
        if access["service"] != "jocasta" and (not isinstance(key, dict) or key.get("key_type") != "rsa"):
            problems.append(f"signing key {access['signing_key']} must have key_type rsa for {access['service']}")
    if access["state"] == "absent":
        provider = found.get(("provider", access["name"]))
        try:
            apps = _complete_rows(applications, "applications", "slug")
        except AnsibleFilterError as exc:
            problems.append(str(exc))
            apps = []
        for app in apps:
            consumers = app.get("backchannel_providers")
            if ("provider" not in app or not isinstance(consumers, list)
                    or any(type(pk) is not int for pk in consumers)
                    or (app["provider"] is not None and type(app["provider"]) is not int)):
                problems.append("applications: lookup answered in an unexpected shape")
            elif (isinstance(provider, dict) and app["slug"] != access["slug"]
                  and (app["provider"] == provider.get("pk") or provider.get("pk") in consumers)):
                problems.append(f"provider {access['name']}: foreign application {app['slug']} uses it")
    problems.extend(_binding_problems(spec, found, set(owned_state)))
    return problems


def _same_time(a, b):
    try:
        return _time(a, "a") == _time(b, "b")
    except AnsibleFilterError:
        return False


def _single_problems(spec, results, applications, bindings, checks):
    """Differences between Authentik after the apply and the declared state."""
    problems = []
    found = _found(results, problems)
    access = spec["access"]
    identities = spec["identities"]
    present = [i for i in identities if i["state"] == "present"]
    for kind, ident, state in spec["objects"]:
        if state == "absent" and found.get((kind, ident)) is not None:
            problems.append(f"{kind} {ident} still exists")
    if access["state"] == "absent":
        return problems
    if access["service"] != "jocasta":
        key = found.get(("keypair", access["signing_key"]))
        if (not isinstance(key, dict) or key.get("private_key_available") is not True
                or key.get("key_type") != "rsa"):
            problems.append(f"signing key {access['signing_key']} must retain private-key availability and RSA certificate metadata")

    def need(kind, ident):
        obj = found.get((kind, ident))
        if not isinstance(obj, dict):
            problems.append(f"{kind} {ident} is missing")
            return {}
        return obj

    def expect(obj, field, value, label):
        if obj and obj.get(field, MISSING) != value:
            problems.append(f"{label}: {field} is not as declared")

    mapping_pks = []
    for scope, name in access["scope_mappings"].items():
        mapping = need("scopemapping", name)
        expect(mapping, "scope_name", scope, "scope mapping")
        expect(mapping, "expression", access["scope_expressions"][scope], "scope mapping")
        mapping_pks.append(mapping.get("pk"))
    provider = need("provider", access["name"])
    for field, value in (("client_id", access["client_id"]), ("client_type", "confidential"),
                         ("grant_types", ["client_credentials"]),
                         ("access_token_validity", f"seconds={access['token_seconds']}"),
                         ("include_claims_in_id_token", True), ("issuer_mode", "per_provider"),
                         ("signing_key", access["signing_key"])):
        expect(provider, field, value, "provider")
    mappings = provider.get("property_mappings")
    if provider and (not isinstance(mappings, list) or len(mappings) != len(mapping_pks)
                     or set(mappings) != set(mapping_pks)):
        problems.append("provider: property_mappings are not as declared")
    app = need("application", access["slug"])
    expect(app, "provider", provider.get("pk"), "application")
    expect(app, "policy_engine_mode", "any", "application")
    group = need("group", access["group"])
    for field, value in (("is_superuser", False), ("roles", []), ("parents", [])):
        expect(group, field, value, "group")
    if group and _usernames(group) != sorted(i["username"] for i in present):
        problems.append("group: members are not exactly the present identities")

    try:
        rows = _complete_rows(bindings, "application bindings", "pk")
    except AnsibleFilterError as exc:
        problems.append(str(exc))
        rows = []
    if len(rows) != 1:
        problems.append("application: expected exactly one policy binding")
    else:
        b = rows[0]
        if (b.get("group") != group.get("pk") or b.get("user") is not None or b.get("policy") is not None
                or b.get("enabled") is not True or b.get("negate") is not False):
            problems.append("application: its binding is not the identities group alone")

    for item in present:
        user = need("user", item["username"])
        for field, value in (("type", "service_account"), ("is_active", True), ("is_superuser", False),
                             ("roles", []), ("groups", [group.get("pk")]), ("path", "homelab-infra/machine-identities")):
            expect(user, field, value, f"user {item['username']}")
        attributes = (user.get("attributes") or {}) if user else {}
        if user and any(attributes.get(k) != v for k, v in item["grant_attributes"].items()):
            problems.append(f"user {item['username']}: {item['service']} grant attribute is not as declared")
        token = need("token", item["token"])
        if token:
            if (token.get("user_obj") or {}).get("username") != item["username"]:
                problems.append(f"token {item['token']}: owned by another account")
            expect(token, "intent", "app_password", f"token {item['token']}")
            expect(token, "expiring", True, f"token {item['token']}")
            if not _same_time(token.get("expires"), item["expires"]):
                problems.append(f"token {item['token']}: expiry is not as declared")

    try:
        apps = _complete_rows(applications, "applications", "slug")
    except AnsibleFilterError as exc:
        problems.append(str(exc))
        return problems
    answers = {}
    for result in checks:
        item = result.get("item") or {}
        passing = (result.get("json") or {}).get("passing") if result.get("status") == 200 else None
        answers[(item.get("username"), item.get("slug"))] = passing
    for item in present:
        for row in apps:
            slug = row.get("slug")
            answer = answers.get((item["username"], slug))
            if answer is not (slug == access["slug"]):
                problems.append(f"user {item['username']}: Authentik's access answer for {slug} is {answer}")
    return problems


def machine_identity_queries(spec, owned, proven_provider=None):
    """One complete lookup per owned/declared name, client ID and signing key."""
    wanted = {(k, i) for k, i, _ in spec["objects"]} | {(k, i) for k, i, _ in owned}
    wanted.update(("client_id", access["client_id"]) for access in spec["services"].values())
    queries = [{"kind": k, "id": i, "path": LOOKUPS[k][0].format(quote(i, safe=""))}
               for k, i in sorted(wanted)
               if proven_provider is None or k not in ("provider", "client_id")]
    if proven_provider is not None:
        queries.append(proven_provider)
    keys = {access["signing_key"] for access in spec["services"].values() if access["state"] == "present"}
    queries.extend({"kind": "keypair", "id": key, "path": f"/crypto/certificatekeypairs/{key}/"}
                   for key in sorted(keys))
    return queries


def machine_identity_problems(spec, results, applications, bindings, checks):
    problems = []
    # The old single-service public function accepted one binding response.
    by_slug = bindings
    if isinstance(bindings, dict) and "results" in bindings and "pagination" in bindings:
        by_slug = {spec["access"]["slug"]: bindings}
    for part in _service_specs(spec):
        problems.extend(_single_problems(part, results, applications,
                                        by_slug.get(part["access"]["slug"], {}), checks))
    return problems


def machine_identity_apply_state(instance, content, pk, name, since):
    """2026.8.4 has no apply request ID: bind a fresh save to the exact content.

    PATCH also advances last_applied but leaves the old status/hash intact.
    The baseline must therefore be read after storage, immediately before POST.
    A successful worker saves the SHA-512 of the UTF-8 blueprint it applied.
    """
    if not isinstance(instance, dict) or instance.get("enabled") is not True or any(
            instance.get(field, MISSING) != expected for field, expected in (
                ("pk", pk), ("name", name), ("content", content), ("path", ""),
                ("context", {}), ("enabled", True))):
        _refuse("blueprint apply: stored request changed or answered in an unexpected shape")
    status = instance.get("status")
    digest = instance.get("last_applied_hash")
    if (status not in ("unknown", "successful", "warning", "error", "orphaned")
            or not isinstance(digest, str)
            or (digest != "" and re.fullmatch(r"[0-9a-f]{128}", digest) is None)):
        _refuse("blueprint apply: invalid status or content hash")
    applied = _time(instance.get("last_applied"), "blueprint last_applied")
    baseline = _time(since, "blueprint apply baseline")
    if applied <= baseline:
        return "pending"
    if status in ("warning", "error", "orphaned"):
        return "failed"
    if status == "successful" and digest == sha512(content.encode("utf-8")).hexdigest():
        return "successful"
    return "pending"


class FilterModule:
    def filters(self):
        return {
            "machine_identity_apply_state": machine_identity_apply_state,
            "machine_identity_api_url": machine_identity_api_url,
            "machine_identity_blueprint": machine_identity_blueprint,
            "machine_identity_spec": machine_identity_spec,
            "machine_identity_owned": machine_identity_owned,
            "machine_identity_queries": machine_identity_queries,
            "machine_identity_existing_provider": machine_identity_existing_provider,
            "machine_identity_existing_baseline": machine_identity_existing_baseline,
            "machine_identity_lookup": machine_identity_lookup,
            "machine_identity_refusals": machine_identity_refusals,
            "machine_identity_problems": machine_identity_problems,
        }
