"""Closed M3 host contract. Survey bb2fc17f@1; no rotation provider or PKI."""
import copy
import datetime as dt
import json
import re
import uuid

MATRIX = {
    "laptop": {
        "jocasta": {
            "operator": True
        },
        "ov": {
            "ov_actions": [
                "read",
                "write",
                "remove",
                "maintain"
            ],
            "ov_personal": {
                "user": "default",
                "account": "default",
                "actions": [
                    "memory.read",
                    "memory.write",
                    "session.read",
                    "session.create",
                    "session.append"
                ]
            },
            "ov_namespaces": [
                "viking://resources"
            ]
        },
        "holonet": {
            "purposes": {
                "catalog": {
                    "scopes": [
                        "holonet.read"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "catalog.read"
                        ],
                        "holonet_libraries": [
                            "3"
                        ],
                        "holonet_namespace": "movies-catalog",
                        "holonet_target_users": [],
                        "holonet_catalog_users": [
                            "54b7d36cef804d3c8478474553ed3a82"
                        ],
                        "holonet_history_users": []
                    }
                },
                "writer": {
                    "scopes": [
                        "holonet.read",
                        "holonet.curate",
                        "holonet.home"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "catalog.read",
                            "playlist.read",
                            "playlist.manage",
                            "object.share",
                            "home.read",
                            "home.manage",
                            "object.delete",
                            "history.read"
                        ],
                        "holonet_libraries": [
                            "3",
                            "615",
                            "1132"
                        ],
                        "holonet_namespace": "managed-recommendations",
                        "holonet_target_users": [
                            "545014d1c9d1416b8e0b5a445d6ff9ec",
                            "b8f4f6de49b443f1948cf0118679e863",
                            "db2f3bfb1f84438ebcde94f228183011",
                            "313e39bb4d8c4b92b81d4b170c3f1b8e",
                            "c06c7d2c3e224d8ba2e466efa5bcb97b",
                            "22703968ab0b48eebb4faf7c1d16262c",
                            "e05efa68303043b893b0f5c2e200ccd7"
                        ],
                        "holonet_catalog_users": [
                            "54b7d36cef804d3c8478474553ed3a82"
                        ],
                        "holonet_history_users": [
                            "545014d1c9d1416b8e0b5a445d6ff9ec",
                            "b8f4f6de49b443f1948cf0118679e863",
                            "db2f3bfb1f84438ebcde94f228183011",
                            "313e39bb4d8c4b92b81d4b170c3f1b8e",
                            "c06c7d2c3e224d8ba2e466efa5bcb97b",
                            "22703968ab0b48eebb4faf7c1d16262c",
                            "e05efa68303043b893b0f5c2e200ccd7"
                        ]
                    }
                },
                "janitor": {
                    "scopes": [
                        "holonet.read",
                        "holonet.janitor"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "janitor.importers.read",
                            "janitor.custody.read"
                        ],
                        "holonet_libraries": [
                            "3"
                        ],
                        "holonet_namespace": "managed-janitor",
                        "holonet_target_users": [],
                        "holonet_catalog_users": [],
                        "holonet_history_users": []
                    }
                },
                "torrents": {
                    "scopes": [
                        "holonet.read",
                        "holonet.janitor",
                        "holonet.torrents"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "janitor.torrents.read"
                        ],
                        "holonet_libraries": [
                            "3"
                        ],
                        "holonet_namespace": "managed-janitor",
                        "holonet_target_users": [],
                        "holonet_catalog_users": [],
                        "holonet_history_users": []
                    }
                },
                "seed_join": {
                    "scopes": [
                        "holonet.read",
                        "holonet.janitor",
                        "holonet.seedjoin"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "janitor.seed_join.read"
                        ],
                        "holonet_libraries": [
                            "3"
                        ],
                        "holonet_namespace": "managed-janitor",
                        "holonet_target_users": [],
                        "holonet_catalog_users": [],
                        "holonet_history_users": []
                    }
                }
            }
        },
        "shizune": {
            "shizune_owner": "igor"
        }
    },
    "omarchy-pc": {
        "jocasta": {
            "operator": True
        },
        "ov": {
            "ov_actions": [
                "read",
                "write",
                "remove",
                "maintain"
            ],
            "ov_personal": {
                "user": "default",
                "account": "default",
                "actions": [
                    "memory.read",
                    "memory.write",
                    "session.read",
                    "session.create",
                    "session.append"
                ]
            },
            "ov_namespaces": [
                "viking://resources"
            ]
        },
        "shizune": {
            "shizune_owner": "igor"
        }
    },
    "agent-a": {
        "jocasta": {
            "operator": True
        }
    },
    "agent-b": {
        "jocasta": {
            "operator": True
        }
    },
    "homelab": {
        "holonet": {
            "purposes": {
                "catalog": {
                    "scopes": [
                        "holonet.read"
                    ],
                    "claims": {
                        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
                        "holonet_actions": [
                            "catalog.read"
                        ],
                        "holonet_libraries": [
                            "3"
                        ],
                        "holonet_namespace": "movies-catalog",
                        "holonet_target_users": [],
                        "holonet_catalog_users": [
                            "54b7d36cef804d3c8478474553ed3a82"
                        ],
                        "holonet_history_users": []
                    }
                }
            }
        },
        "ha": {
            "ha_actions": [
                "state.read"
            ],
            "ha_entities": [
                "sensor.zigbee2mqtt_bridge_version"
            ]
        },
        "homelab": {
            "homelab_hosts": [
                "homelab"
            ],
            "homelab_services": []
        }
    }
}
# Normandy has only Akasha identity admission; registry supplies its backup grant.
MATRIX["normandy"] = {}
AKASHA_HOSTS = frozenset({"laptop", "omarchy-pc", "agent-a", "agent-b", "homelab", "normandy"})
AKASHA_AUDIENCE = "https://akasha.ryzhkov.dev"
AKASHA_CLIENT = "chaldea-akasha"

GROUPS = {
    "ov": "ov-http-a63e197f-machine-identities",
    "holonet": "chaldea-holonet-machine-identities",
    "shizune": "shizune-mcp-machine-identities",
    "ha": "ha-http-a63e197f-machine-identities",
    "homelab": "homelab-observer-machine-identities",
}
GROUP_IDS = {
    "ov": "dd5ff45b-de3d-4fab-b9cc-82329f9270bf",
    "holonet": "e1c5af7f-13db-43f3-b3f1-0bab52afb792",
    "shizune": "6f26e87e-ef44-446c-b6da-719fb8487f17",
    "ha": "090097b8-18d5-4ef7-a326-2d373f883c31",
    "homelab": "1b2ce6b9-ad3b-4da0-acb6-0ea305de7f08",
}
RESOURCES = {
    "jocasta": "https://jocasta.mcp.ryzhkov.dev/mcp",
    "ov": "https://ov.mcp.ryzhkov.dev/mcp",
    "holonet": "https://media.mcp.ryzhkov.dev/mcp",
    "shizune": "https://shizune.mcp.ryzhkov.dev/mcp",
    "ha": "https://ha.mcp.ryzhkov.dev/mcp",
    "homelab": "https://homelab.mcp.ryzhkov.dev/mcp",
}
PATH = "homelab-infra/agent-hosts"

def require(ok):
    if not ok:
        raise ValueError("Host enrollment contract refused")

def declaration(hosts, owner, now, tokens=None):
    require(str(uuid.UUID(owner)) == owner)
    require(isinstance(hosts, list) and 1 <= len(hosts) <= 6)
    seen = set()
    for item in hosts:
        require(set(item) == {"host", "generation", "expires", "revoked", "destination"})
        host = item["host"]
        require(host in MATRIX and host not in seen)
        seen.add(host)
        require(type(item["generation"]) is int and 1 <= item["generation"] <= 1000000)
        require(type(item["revoked"]) is bool)
        expiry = dt.datetime.fromisoformat(item["expires"].replace("Z", "+00:00"))
        require(item["expires"].endswith("Z"))
        if not item["revoked"] and host not in (tokens or {}):
            require(now + dt.timedelta(days=1) <= expiry <= now + dt.timedelta(days=60))
        dest = item["destination"]
        require(set(dest) == {"hostname", "uid"})
        require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", dest["hostname"]) is not None)
        require(type(dest["uid"]) is int and dest["uid"] > 0)
    return copy.deepcopy(hosts)

def attributes(host, owner, rotation=None):
    return {"agent-host-owner": owner, "agent-host": host, "services": copy.deepcopy(MATRIX[host]),
            "rotation": rotation, "goauthentik.io/user/token-maximum-lifetime": "days=60"}

def managed_role_ok(user, roles, permissions, tokens):
    if not user.get("roles"):
        return user.get("roles") == []
    if len(user["roles"]) != 1:
        return False
    role = roles.get(user["roles"][0], {})
    name = "ak-managed-role--user-" + str(user["pk"])
    # The 2026.8.5 Role API exposes only pk/name; validate its owner via
    # complete user/group memberships in the enrollment caller. The issuance
    # ORM guard independently checks the non-serialized managed marker.
    if role.get("name") != name:
        return False
    own = {str(t["pk"]) for t in tokens if t.get("user") == user["pk"]}
    if user["roles"][0] not in permissions:
        return False
    return all(p.get("codename") == "view_token_key" and p.get("model") == "token"
               and p.get("app_label") == "authentik_core" and p.get("object_pk") in own
               for p in permissions.get(user["roles"][0], []))

# Executed inside Authentik expressions. Validate roles at issuance as well as
# enrollment: a name-only allowance could hide newly assigned global powers.
RUNTIME_GUARD = """
def host_allowed(u, expected):
    from guardian.models import RoleModelPermission, RoleObjectPermission
    from authentik.core.models import Token
    if (not u.is_active or u.type != 'service_account' or u.is_superuser
            or u.path != 'homelab-infra/agent-hosts' or u.attributes != expected['attributes']
            or set(str(g.pk) for g in u.all_groups()) != set(expected['groups'])):
        return False
    roles = list(u.all_roles())
    if len(roles) > 1:
        return False
    for role in roles:
        name = 'ak-managed-role--user-' + str(u.pk)
        if role.name != name or role.managed != name or RoleModelPermission.objects.filter(role=role).exists():
            return False
        if set(role.users.values_list('pk', flat=True)) != {u.pk} or role.groups.exists():
            return False
        for perm in RoleObjectPermission.objects.filter(role=role).select_related('permission__content_type'):
            ct = perm.permission.content_type
            if (perm.permission.codename != 'view_token_key' or ct.app_label != 'authentik_core'
                    or ct.model != 'token' or not Token.objects.filter(pk=perm.object_pk, user=u).exists()):
                return False
    return True
"""

def claims(service, grant, requested):
    if not isinstance(requested, list) or len(set(requested)) != len(requested):
        return {}
    scopes = set(requested)
    if service == "holonet":
        matches = [p["claims"] for p in grant["purposes"].values() if scopes == set(p["scopes"])]
        return copy.deepcopy(matches[0]) if len(matches) == 1 else {}
    if service == "ov":
        allowed = {"ov." + a for a in grant["ov_actions"]}
        allowed |= {"ov.personal." + a for a in grant["ov_personal"]["actions"]}
        if "ov.read" not in scopes or not scopes <= allowed:
            return {}
        result = {"ov_namespaces": grant["ov_namespaces"],
                  "ov_actions": [a for a in grant["ov_actions"] if "ov." + a in scopes]}
        personal = [a for a in grant["ov_personal"]["actions"] if "ov.personal." + a in scopes]
        if personal:
            result["ov_personal"] = dict(grant["ov_personal"], actions=personal)
        return copy.deepcopy(result)
    exact = {"jocasta": {"jocasta.read", "jocasta.write"}, "ha": {"ha.read"},
             "homelab": {"homelab.read"}, "shizune": {"shizune.use"}}
    if scopes != exact[service]:
        return {}
    return {"jocasta_operator": True} if service == "jocasta" else copy.deepcopy(grant)


def canonical(value):
    # Render API data deterministically so an unchanged state renders the same
    # expression bytes. Dict keys are sorted; list order is kept because
    # host_allowed compares attributes by value.
    if isinstance(value, dict):
        return {k: canonical(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return [canonical(x) for x in value]
    return value


def expected_users(users, service):
    # Groups are compared as a set by host_allowed, so their order is free.
    return canonical({str(u["pk"]): {"username": u["username"], "attributes": u["attributes"],
                                     "groups": sorted(str(g) for g in u["groups"])}
                      for u in users if service in u["attributes"]["services"]})


def jocasta_policy(users, original):
    public_users(users)
    expected = expected_users(users, "jocasta")
    return ("expected = " + repr(expected) + "\n" + RUNTIME_GUARD + """
u = request.user
if u and u.username.startswith('chaldea-') and u.path == 'homelab-infra/agent-hosts':
    e = expected.get(str(u.pk))
    if not e or e['username'] != u.username or not host_allowed(u, e):
        return False
    http = request.http_request
    if http is None or http.method != 'POST':
        return False
    p = http.POST
    return (p.get('client_id') == 'jocasta-managed-interactive'
        and p.get('username') == u.username and bool(p.get('password'))
        and not p.get('client_secret') and not p.get('client_assertion')
        and not p.get('client_assertion_type') and p.get('grant_type') == 'client_credentials'
        and p.getlist('resource') == ['https://jocasta.mcp.ryzhkov.dev/mcp']
        and request.context.get('oauth_grant_type') == 'client_credentials'
        and request.context.get('oauth_scopes') == {'jocasta.read', 'jocasta.write'}
        and len(p.get('scope', '').split()) == 2
        and set(p.get('scope', '').split()) == {'jocasta.read', 'jocasta.write'})
""" + original)

def public_users(users):
    require(isinstance(users, list))
    seen = set()
    for user in users:
        host = user["attributes"]["agent-host"]
        require(host in MATRIX and host not in seen)
        seen.add(host)
        require(user["username"] == "chaldea-" + host and type(user["pk"]) is int and user["pk"] > 0)
        owner = user["attributes"]["agent-host-owner"]
        rotation = user["attributes"].get("rotation")
        if rotation is not None:
            require(set(rotation) == {"group", "client_id"}
                    and rotation["client_id"] == "chaldea-host-rotation"
                    and str(uuid.UUID(rotation["group"])) == rotation["group"])
        require(str(uuid.UUID(owner)) == owner and user["attributes"] == attributes(host, owner, rotation))
        require(isinstance(user["groups"], list) and len(user["groups"]) == len(set(user["groups"])))
        resource_groups = {GROUP_IDS[s] for s in MATRIX[host] if s != "jocasta"}
        # An optional rotation group is pinned by the enrollment receipt, and
        # exact current groups are embedded into each issuance guard.
        require(set(user["groups"]) == resource_groups | ({rotation["group"]} if rotation else set()))
    return users


# The only legacy exception. Protected input pins current existing-only readback;
# it cannot select a grant or another account.
ORIGINAL_WRITER = "chaldea-laptop-holonet-recommendations"
ACQUIRE_READ = {
    "scopes": ["holonet.read", "holonet.janitor", "holonet.acquire"],
    "claims": {
        "holonet_server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
        "holonet_namespace": "managed-janitor", "holonet_libraries": ["3"],
        "holonet_actions": ["janitor.acquire.read"],
        "holonet_target_users": [], "holonet_catalog_users": [], "holonet_history_users": [],
    },
}

def original_writer_declaration(value):
    require(isinstance(value, dict))
    if not value:
        return {}
    require(set(value) in ({"username", "pk", "subject", "generation", "expires",
                           "user_sha256", "token_sha256"},
                          {"username", "pk", "subject", "generation", "expires",
                           "user_sha256", "token_sha256", "state"}))
    require(value.get("state", "enabled") in ("enabled", "disabled"))
    require(value["username"] == ORIGINAL_WRITER and type(value["generation"]) is int
            and value["generation"] == 1 and type(value["pk"]) is int and value["pk"] > 0)
    require(isinstance(value["subject"], str) and re.fullmatch(r"[0-9a-f]{64}", value["subject"]))
    require(all(isinstance(value[k], str) and re.fullmatch(r"[0-9a-f]{64}", value[k])
                for k in ("user_sha256", "token_sha256")))
    expiry = dt.datetime.fromisoformat(value["expires"].replace("Z", "+00:00"))
    require(value["expires"].endswith("Z") and
            (value.get("state") == "disabled" or expiry > dt.datetime.now(dt.timezone.utc)))
    return canonical(value)


def writer_user_readback(user):
    fields = ("pk", "uid", "username", "path", "type", "is_active", "is_superuser", "attributes")
    result = {k: user[k] for k in fields}
    result.update(groups=sorted(str(g) for g in user["groups"]),
                  roles=sorted(str(r) for r in user["roles"]))
    return result

def writer_token_readback(token):
    result = {k: token[k] for k in ("pk", "identifier", "user", "intent", "expiring")}
    result["expires"] = dt.datetime.fromisoformat(token["expires"].replace("Z", "+00:00")).isoformat()
    return result

def readback_sha256(value):
    import hashlib
    import json
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def scope_expression(service, scope, users, original, original_writer=None):
    public_users(users)
    expected = expected_users(users, service)
    # All host-shaped accounts, including ungranted hosts, are denied before the
    # legacy expression. Legacy account expressions remain byte-for-byte intact.
    select = """requested = token.scope
if not isinstance(requested, list) or not requested or len(requested) != len(set(requested)):
    return {}
if SCOPE != sorted(requested)[0]:
    return {}
grant = u.attributes['services'][SERVICE]
"""
    import inspect
    pure = inspect.getsource(claims).replace("def claims(", "def selected_claims(")
    pure = "import copy\n" + pure
    branch = ("u = user\n"
              "if u and u.username.startswith('chaldea-') and u.path == 'homelab-infra/agent-hosts':\n"
              "    e = expected.get(str(u.pk))\n"
              "    if not e or e['username'] != u.username or not host_allowed(u, e):\n        return {}\n")
    selected = select.replace("SCOPE", repr(scope)).replace("SERVICE", repr(service))
    if service == "jocasta":
        selected += ("if provider.client_id != 'jocasta-managed-interactive' or "
                     "provider.application.slug != 'jocasta-managed-interactive':\n    return {}\n")
    selected += "result = selected_claims(" + repr(service) + ", grant, requested)\n"
    selected += "if not result:\n    return {}\n"
    selected += "result['aud'] = " + repr(RESOURCES[service]) + "\n"
    if service == "jocasta":
        selected += "result['webmcp_identity'] = u.username\n"
    selected += "return result\n"

    legacy = ""
    if service == "holonet" and original_writer:
        pinned = original_writer_declaration(original_writer)
        # Any acquisition-shaped request is terminal, including unions and
        # unsupported future scopes. Never fall through to old writer grants.
        guard_source = inspect.getsource(writer_user_readback) + "\n" + inspect.getsource(readback_sha256)
        legacy = (guard_source + "\noriginal_writer = " + repr(pinned) + "\n"
                  "requested = token.scope\n"
                  "if isinstance(requested, list) and any(s.startswith('holonet.acquire') for s in requested):\n"
                  "    if (original_writer.get('state') == 'disabled' or not u or u.username != original_writer['username']\n"
                  "            or u.pk != original_writer['pk'] or str(u.uid) != original_writer['subject']\n"
                  "            or u.path != 'homelab-infra/machine-identities'\n"
                  "            or not u.is_active or u.type != 'service_account' or u.is_superuser\n"
                  "            or len(requested) != 3 or set(requested) != " + "set(" + repr(ACQUIRE_READ["scopes"]) + ")" + "):\n"
                  "        return {}\n"
                  "    actual = {k: getattr(u, k) for k in (\'pk\', \'uid\', \'username\', \'path\', \'type\', \'is_active\', \'is_superuser\', \'attributes\')}\n"
                  "    actual[\'uid\'] = str(actual[\'uid\'])\n"
                  "    actual[\'groups\'] = [str(g.pk) for g in u.all_groups()]\n"
                  "    actual[\'roles\'] = [str(r.pk) for r in u.all_roles()]\n"
                  "    if readback_sha256(writer_user_readback(actual)) != original_writer[\'user_sha256\']:\n"
                  "        return {}\n"
                  "    if " + repr(scope) + " != 'holonet.acquire':\n"
                  "        return {}\n"
                  "    return " + repr(dict(ACQUIRE_READ["claims"], aud=RESOURCES["holonet"])) + "\n")
    return ("# M3 host coexistence; original expression follows\nexpected = " + repr(expected)
            + "\n" + RUNTIME_GUARD + "\n" + pure + "\n" + branch
            + "".join("    " + line + "\n" for line in selected.splitlines()) + legacy + original)
