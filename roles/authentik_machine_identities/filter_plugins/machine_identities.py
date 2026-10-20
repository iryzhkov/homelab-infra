"""Plan, ownership and verification rules for roles/authentik_machine_identities.

Pure functions over plain data, so every refusal can be tested without Ansible
or Authentik. Messages name objects and fields, never a credential.

The blueprint's stored content is the ownership record: its "owned" comment
line lists every object the blueprint names. An object is this role's only if
a stored blueprint named it; anything else with a colliding name is refused,
never adopted or deleted.
"""
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
SCOPE = "jocasta.read"
TOKEN_SECONDS = 300
# The attribute the scope mapping reads. Only an administrator can change a
# service account's attributes.
ATTRIBUTE = "chaldea_jocasta"
MAX_LIFETIME = "goauthentik.io/user/token-maximum-lifetime"
# A present identity whose app password expires sooner than this is refused:
# renew it (a later expiry) or rotate it (a higher generation) first.
MIN_REMAINING = timedelta(days=1)

HOST = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\Z")
PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,63}\Z")
PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,49}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,99}\Z")
CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
LOGIN = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
HOME = re.compile(r"/[A-Za-z0-9._/-]{1,255}\Z")

ACCESS_KEYS = {"state", "slug", "name", "client_id", "resource", "scope", "token_seconds",
               "signing_key", "group"}
IDENTITY_KEYS = {"host", "profile", "project", "generation", "expires", "state"}
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


def _resource(value):
    if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{1,512}", value):
        _refuse("access resource must be a printable URL")
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or "*" in value or "\\" in value):
        _refuse("access resource must be an https URL without credentials, query or fragment")
    return value


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


def machine_identity_spec(identities, access, hosts, max_days, now):
    """Validate the declared identities and derive every name the role uses.

    hosts maps each inventory host an identity names to its ssh_user, an
    optional home override and its connection.
    """
    access = dict(_keys(access, ACCESS_KEYS, "authentik_jocasta_machine_access"))
    if access["state"] not in STATES:
        _refuse("access state must be present or absent")
    if access["scope"] != SCOPE:
        _refuse(f"access scope must be {SCOPE}")
    if access["token_seconds"] != TOKEN_SECONDS or isinstance(access["token_seconds"], bool):
        _refuse(f"access token_seconds must be {TOKEN_SECONDS}")
    _match(SLUG, access["slug"], "access slug")
    _match(NAME, access["name"], "access name")
    _match(NAME, access["group"], "access group")
    _match(CLIENT_ID, access["client_id"], "access client_id")
    _match(UUID, access["signing_key"], "access signing_key (an existing keypair's kp_uuid)")
    _resource(access["resource"])
    access["mapping"] = f"{access['name']}: {SCOPE}"
    access["expression"] = expression(access)

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
        item = _keys(raw, IDENTITY_KEYS, name)
        host = _match(HOST, item["host"], name + " host")
        profile = _match(PROFILE, item["profile"], name + " profile")
        name = f"identity {host}/{profile}"
        project = _match(PROJECT, item["project"], name + " project")
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
            "host": host, "profile": profile, "project": project, "state": state,
            "generation": generation, "expires": item["expires"],
            "username": username, "token": f"{username}-g{generation}",
            "old_tokens": [f"{username}-g{g}" for g in range(1, generation)],
            "owner": owner, "connection": connection, "home": home,
            "ancestors": [home, f"{home}/.config", f"{home}/.config/chaldea", directory],
            "directory": directory, "secret_path": f"{directory}/{profile}.app-password",
            "attributes": {ATTRIBUTE: {"project": project}, MAX_LIFETIME: f"days={max_days}",
                           "homelab-infra": {"host": host, "profile": profile}},
        })

    objects = []
    for kind, ident in (("scopemapping", access["mapping"]), ("provider", access["name"]),
                        ("application", access["slug"]), ("group", access["group"])):
        objects.append([kind, ident, access["state"]])
    for item in out:
        objects.append(["user", item["username"], item["state"]])
        objects.append(["token", item["token"], item["state"]])
        objects.extend(["token", old, "absent"] for old in item["old_tokens"])
    marker = OWNED_PREFIX + json.dumps({"version": 1, "objects": sorted(objects)},
                                       sort_keys=True, separators=(",", ":"))
    return {"access": access, "identities": out, "objects": sorted(objects), "marker": marker}


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


def machine_identity_queries(spec, owned):
    """The read-only lookups that show what exists under every name involved."""
    wanted = {(k, i) for k, i, _ in spec["objects"]} | {(k, i) for k, i, _ in owned}
    wanted.add(("client_id", spec["access"]["client_id"]))
    queries = [{"kind": k, "id": i, "path": LOOKUPS[k][0].format(quote(i, safe=""))}
               for k, i in sorted(wanted)]
    if spec["access"]["state"] == "present":
        key = spec["access"]["signing_key"]
        queries.append({"kind": "keypair", "id": key, "path": f"/crypto/certificatekeypairs/{key}/"})
    return queries


def _found(results, problems):
    """(kind, name) -> the one object with exactly that name, or None."""
    found = {}
    for result in results:
        item = result.get("item", {})
        kind, ident = item.get("kind"), item.get("id")
        body = result.get("json") if isinstance(result.get("json"), dict) else {}
        if kind == "keypair":
            found[(kind, ident)] = body if result.get("status") == 200 else None
            continue
        rows = body.get("results")
        count = (body.get("pagination") or {}).get("count")
        if result.get("status") != 200 or not isinstance(rows, list) or not isinstance(count, int):
            problems.append(f"{kind} {ident}: lookup answered in an unexpected shape")
            found[(kind, ident)] = MISSING
            continue
        if count > len(rows):
            problems.append(f"{kind} {ident}: lookup did not return every candidate")
            found[(kind, ident)] = MISSING
            continue
        field = LOOKUPS[kind][1]
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


def machine_identity_refusals(spec, owned, results):
    """Reasons not to store the blueprint. Empty: storing it is safe."""
    problems = []
    found = _found(results, problems)
    owned_state = {(k, i): s for k, i, s in owned}
    desired = {(k, i): s for k, i, s in spec["objects"]}
    for (kind, ident), state in sorted(desired.items()):
        obj = found.get((kind, ident), MISSING)
        if obj is MISSING:
            continue
        if obj is not None and (kind, ident) not in owned_state:
            verb = "take over" if state == "present" else "delete"
            problems.append(f"{kind} {ident} exists but no stored blueprint of this role created it; "
                            f"refusing to {verb} it")
    for (kind, ident), state in sorted(owned_state.items()):
        if (kind, ident) in desired:
            continue
        if state == "present":
            problems.append(f"{kind} {ident} was created by this role; declare it absent before dropping it")
        elif found.get((kind, ident)) is not None:
            problems.append(f"{kind} {ident} still exists; keep it declared absent until it is gone")
    access = spec["access"]
    clients = found.get(("client_id", access["client_id"]))
    if isinstance(clients, list) and any(row.get("name") != access["name"] for row in clients):
        problems.append(f"client_id {access['client_id']} belongs to another provider")
    if access["state"] == "present":
        key = found.get(("keypair", access["signing_key"]))
        if not isinstance(key, dict) or key.get("private_key_available") is not True:
            problems.append(f"signing key {access['signing_key']} is missing or has no private key")
    problems.extend(_binding_problems(spec, found, set(owned_state)))
    return problems


def _same_time(a, b):
    try:
        return _time(a, "a") == _time(b, "b")
    except AnsibleFilterError:
        return False


def machine_identity_problems(spec, results, applications, bindings, checks):
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

    def need(kind, ident):
        obj = found.get((kind, ident))
        if not isinstance(obj, dict):
            problems.append(f"{kind} {ident} is missing")
            return {}
        return obj

    def expect(obj, field, value, label):
        if obj and obj.get(field, MISSING) != value:
            problems.append(f"{label}: {field} is not as declared")

    mapping = need("scopemapping", access["mapping"])
    expect(mapping, "scope_name", SCOPE, "scope mapping")
    expect(mapping, "expression", access["expression"], "scope mapping")
    provider = need("provider", access["name"])
    for field, value in (("client_id", access["client_id"]), ("client_type", "confidential"),
                         ("grant_types", ["client_credentials"]),
                         ("access_token_validity", f"seconds={TOKEN_SECONDS}"),
                         ("include_claims_in_id_token", True), ("issuer_mode", "per_provider"),
                         ("signing_key", access["signing_key"]),
                         ("property_mappings", [mapping.get("pk")])):
        expect(provider, field, value, "provider")
    app = need("application", access["slug"])
    expect(app, "provider", provider.get("pk"), "application")
    expect(app, "policy_engine_mode", "any", "application")
    group = need("group", access["group"])
    for field, value in (("is_superuser", False), ("roles", []), ("parents", [])):
        expect(group, field, value, "group")
    if group and _usernames(group) != sorted(i["username"] for i in present):
        problems.append("group: members are not exactly the present identities")

    rows = bindings.get("results") if isinstance(bindings, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
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
        if user and (user.get("attributes") or {}).get(ATTRIBUTE) != item["attributes"][ATTRIBUTE]:
            problems.append(f"user {item['username']}: project attribute is not as declared")
        token = need("token", item["token"])
        if token:
            if (token.get("user_obj") or {}).get("username") != item["username"]:
                problems.append(f"token {item['token']}: owned by another account")
            expect(token, "intent", "app_password", f"token {item['token']}")
            expect(token, "expiring", True, f"token {item['token']}")
            if not _same_time(token.get("expires"), item["expires"]):
                problems.append(f"token {item['token']}: expiry is not as declared")

    apps = applications.get("results") if isinstance(applications, dict) else None
    count = ((applications or {}).get("pagination") or {}).get("count") if isinstance(applications, dict) else None
    if not isinstance(apps, list) or not isinstance(count, int) or count > len(apps):
        problems.append("applications: the full list could not be read")
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


class FilterModule:
    def filters(self):
        return {
            "machine_identity_spec": machine_identity_spec,
            "machine_identity_owned": machine_identity_owned,
            "machine_identity_queries": machine_identity_queries,
            "machine_identity_lookup": machine_identity_lookup,
            "machine_identity_refusals": machine_identity_refusals,
            "machine_identity_problems": machine_identity_problems,
        }
