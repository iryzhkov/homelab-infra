"""Bounded packaging contracts. Service authorization remains in each binary."""
from datetime import datetime
import hashlib
import ipaddress
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit

from ansible.errors import AnsibleFilterError

SERVICES = {
    "ha-mcp": ("homeassistant-mcp", "027505484cf33947e64f5232cf17a1ff0d96d522"),
    "homelab-observer": ("homelab-cli", "8af24606b7cf5cd89e727660266104699313e993"),
    "holonet": ("homelab-cli", "4aceab9770f0098b4369e37657f6b64051884baf"),
}

# The historical builds keep the accepted command/config/catalog-only boundary.
# The original remains admissible for rollback; this is not an arbitrary source
# selector. Artifact bytes still require the operator's exact reviewed SHA256.
HOLONET_COEXISTENCE_SOURCE = "3d4dcb35cab58a02aee7fa3dc7804d5a1790b7d0"
HOLONET_COEXISTENCE_SHA256 = "189717cc9c16df3bf1842ece8f3312306ea55d526278d6c54424992a6348210d"
HOLONET_CATALOG_SOURCE_REVISIONS = frozenset({
    SERVICES["holonet"][1],
    "31817f70f7fed5900622bc7274c6a8a31275189b",
})
HOLONET_WRITER_SOURCE = "f0620d641710b6da89b0cd04587b68a998fe22b4"
HOLONET_WRITER_CONTRACT = "c0c429ed04aa06f46c5e0c639a4dcc000dde4431"
HOLONET_SOURCE_REVISIONS = HOLONET_CATALOG_SOURCE_REVISIONS | {HOLONET_WRITER_SOURCE}

# Exact reviewed full-lifecycle build and wire fixture/interface pins.
# Deploy the compatible worker before this binary. Never downgrade the client
# while a lifecycle request is unfinished; complete it or use protected recovery.
HOLONET_OPERATOR_SOURCE = "49022d6fc1cccb6f76c225a760b401cce2b115f6"
HOLONET_OPERATOR_CONTRACT = "2cd99eb967690de97e9be01b411f4df119283d4c"

# Exact playback source and compatible worker/interface source. The Go build
# passed independent review and CI; these packaging and worker pins require the
# final integration review before activation. Older builds refuse playback.
# The playback build also accepts playlist_operator.
HOLONET_PLAYBACK_SOURCE = "6c1312693a9d20432f46e6a33ac256698ee46f74"
HOLONET_PLAYBACK_CONTRACT = "cfea03b6e99e8b501ce5b6f1abbe0d32545ecd3e"
# Frozen Series source and compatible worker fixture for integration review.
HOLONET_SERIES_SOURCE = "9efca7904b6b142c765bda4574185615fd374518"
HOLONET_SERIES_CONTRACT = "748e89df0c765ba82133d9b45b8be00e76925128"
# Exact history-capable client and compatible unchanged worker/interface.
# CI, artifact checksum and final release acceptance still gate deployment.
HOLONET_TORRENT_SOURCE = "06b7162d865b89c36385705c0583dab4b043e939"
HOLONET_TORRENT_CONTRACT = "d07c64a5f291e7c0e47e5ebb9f25bf3fcb8f6eb2"
HOLONET_TORRENT_SHA256 = "d282c09f241cf95df9c37baa2ab818d757e1ddc8bc0d0bd4fdd848f7dc91cef2"
TORRENT_DESCRIPTOR = "/etc/holonet-playlists/qbit.json"
TORRENT_AUTH = "/etc/holonet-playlists/qbit-auth.json"
TORRENT_RESOURCE = "homelab-qbit-movies"
# Exact candidate artifact. Independent review, CI and access approval still gate activation.
HOLONET_ACQUIRE_SOURCE = "4e6f87442b88f61b4017f86e507bf90567ad2c29"
HOLONET_ACQUIRE_SHA256 = "8516cc4b398c528ba968e3b70f54932000f0c0bca8d8ffee5cf658eec9ca90ed"
HOLONET_ACQUIRE_CONTRACT = "4e6f87442b88f61b4017f86e507bf90567ad2c29"
# Stat-only seed/library pair (jocasta:c3070dfe196e7f67732749db4b279689@3).
# Exact reviewed client/worker pair; publication and CI still gate deployment.
# Static linux/amd64 artifact: Chaldea b02e84fd-94d4-4491-bfa8-76370db21cc9.
HOLONET_SEED_JOIN_SOURCE = "ddd1a223b99ee65abec33cf91b01b0c78ae4f688"
HOLONET_SEED_JOIN_CONTRACT = "c37b45fe27233fe36779fc0741cacefa51331425"
HOLONET_SEED_JOIN_SHA256 = "98369bb77b1d56b4b2ae87709f0860e4123a1ad4777e121c3e0b89384a2505f6"
SEED_JOIN_SOURCE = "stat_pair_v1"
SEED_JOIN_ACTION = "janitor.seed_join.read"
# The user-approved fixed stat-only ceiling; never an installer input.
SEED_CEILING = "/mnt/shared/downloads/complete/movies"

HOLONET_CUSTODY_SOURCE = "f2f91e1c1f9e7346a324567ad206110a259da306"
HOLONET_CUSTODY_CONTRACT = "50127da581f2429c92ff6d40fe23156bde0485a1"
HOLONET_HISTORY_RANGE_SOURCE = "2e7c19fe017e29b15c6e2f0ba9ccb84cadcdc6ff"
HOLONET_HISTORY_RANGE_CONTRACT = "8a29d33611497ab44a32b06e38cfd78f085b2c93"
# Fixed runtime paths: the Jellystat key is its own credential file, never the
# Emby backend file, and the only Jellystat URL besides an HTTPS origin is the
# existing host-local listener.
HOLONET_PLAYBACK_CREDENTIAL = "jellystat.json"
HOLONET_PLAYBACK_LOOPBACK = "http://127.0.0.1:3012"

# Root playlist operator worker (docs/emby-operator-mcp.md). The fixed modules are
# installed together, byte for byte, as one release named by the digest of this
# manifest. Changing any module requires a reviewed pin update here.
OPERATOR_MODULES = {
    "emby_operator_acquire.py": "584cb04f9f0524149391081e248885905d6f0c91ffdf82b4277981ae40b37ad3",
    "emby_operator_acquire_media.py": "8c5969434b52a94a9441daef1e8d6ec47642e222bb23bea5bb2567b4b0384798",
    "emby_operator_acquire_torrents.py": "b74b311beda565dd2c81173af7a8796a827a7bc236860d5719bebeababf41992",
    "emby_operator_adapter.py": "82a9f3a1faffaecfa73c87f4e532840dee6f07f4296370080e96cc955e0b55b9",
    "emby_operator_entry.py": "af1407816aea16803d378d6bade968df26a6dcff4de4a9bee8a41cd34a664bb9",
    "emby_operator_recommendations.py": "42969569034acde9ad26dff8db9472d8612122183d5bff4a2a79c1fbb59f053e",
    "emby_operator_socket.py": "f5085434e9272a126723b2c6e7497345bec768caa5d97aad9cfbb8a63f047060",
    "emby_operator_torrents.py": "e3b5b4d125c7e51c48039c5e9153400ddcf247b00054133a31d36173acee6ec2",
    "emby_operator_custody.py": "09bbaf1a9a59ca2a2df533d5fdae669cfda4170cd0d3ecb7250cdcde34c3b79c",
    "emby_operator_seed_join.py": "deb669172df79565cd17a8774c827604432936eb2404e86a49e59b8e68eb21d2",
}
OPERATOR_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OPERATOR_RELEASES = "/opt/holonet-playlists/releases"
# Homelab's dedicated holonet identity and the existing Movies read ceiling.
OPERATOR_UID = OPERATOR_GID = 940
OPERATOR_SERVER = "3c86e6d7ff6c4a3296522cdf03d22bb0"
OPERATOR_LIBRARY = "3"
OPERATOR_CATALOG_USER = "54b7d36cef804d3c8478474553ed3a82"
OPERATOR_NAMESPACE = "managed-recommendations"
# The only protected operator configs a worker policy may name. Each journal is
# the config's sibling journal/ directory. No variable or request selects a path.
OPERATOR_RECIPIENTS = {
    "545014d1c9d1416b8e0b5a445d6ff9ec":
        "/var/lib/holonet-operator/igor-pr116-retain-20261031-next/config.json",
    "b8f4f6de49b443f1948cf0118679e863": "/var/lib/holonet-operator/viewers-20261101/tv/config.json",
    "db2f3bfb1f84438ebcde94f228183011": "/var/lib/holonet-operator/viewers-20261101/kostya/config.json",
    "313e39bb4d8c4b92b81d4b170c3f1b8e": "/var/lib/holonet-operator/viewers-20261101/lisa/config.json",
    "c06c7d2c3e224d8ba2e466efa5bcb97b": "/var/lib/holonet-operator/viewers-20261101/oleg/config.json",
    "22703968ab0b48eebb4faf7c1d16262c": "/var/lib/holonet-operator/viewers-20261101/tanya/config.json",
    "e05efa68303043b893b0f5c2e200ccd7": "/var/lib/holonet-operator/viewers-20261101/tetiana/config.json",
}


def require(ok, message):
    if not ok:
        raise AnsibleFilterError("web-mcp: " + message)


def keys(value, required, optional=()):
    require(isinstance(value, dict), "expected an object")
    require(set(required) <= value.keys() <= set(required) | set(optional),
            "missing or unknown fields")


def integer(value, low, high):
    require(isinstance(value, int) and not isinstance(value, bool)
            and low <= value <= high, "integer outside bounds")


def match(value, pattern):
    require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
            "invalid bounded string")


def url(value, schemes=("https",), origin=False):
    require(isinstance(value, str) and len(value) <= 2048, "invalid URL")
    p = urlsplit(value)
    require(p.scheme in schemes and p.hostname and p.netloc == p.netloc.lower()
            and not p.username and not p.password and not p.query and not p.fragment
            and not any(c.isspace() or ord(c) < 33 for c in value)
            and "\\" not in value, "invalid fixed URL")
    try:
        require(p.port is None or 1 <= p.port <= 65535, "invalid port")
    except ValueError:
        require(False, "invalid port")
    if origin:
        require(p.path == "", "backend must be a pathless origin")
    return p


def private_address(value, port=True):
    match(value, r"[0-9.]+:[0-9]{1,5}" if port else r"[0-9.]+")
    host = value.rsplit(":", 1)[0] if port else value
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise AnsibleFilterError("web-mcp: expected an explicit private IPv4 address") from None
    require(any(address in ipaddress.IPv4Network(net) for net in
                ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")),
            "expected a private or loopback address")
    if port:
        integer(int(value.rsplit(":", 1)[1]), 1024, 65535)
    return str(address)


def records(value, low, high):
    require(isinstance(value, list) and low <= len(value) <= high,
            "record count outside bounds")
    return value


def unique(values):
    require(len(values) == len(set(values)), "duplicate values")


def label(value, size):
    require(isinstance(value, str) and 1 <= len(value.encode()) <= size
            and all(32 <= ord(c) <= 126 for c in value), "invalid public label")


def identifier(value):
    match(value, r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def ha_canonical_url(value, schemes=("https",), origin=False):
    p = url(value, schemes, origin)
    require(p.geturl() == value and not (p.hostname or "").endswith(".")
            and not p.netloc.endswith(":"), "HA URL must round-trip canonically")
    require(p.port != (443 if p.scheme == "https" else 80),
            "HA URL must omit an explicit default port")
    if p.port is not None:
        require(p.netloc.endswith(":" + str(p.port)), "HA port must be canonical decimal")
    # Match Go's canonical escaped path; alternate RawPath spellings are refused.
    require(quote_from_bytes(unquote_to_bytes(p.path), safe="/$&+,:;=@-_.~") == p.path,
            "HA URL path must use canonical escaping")
    return p


def trust(config, resource_key, issuer_key, check_url=url):
    resource = check_url(config[resource_key])
    require(resource.path == "/mcp" and resource.port is None,
            "resource must use canonical HTTPS /mcp on port 443")
    match(resource.hostname, r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
    issuer = check_url(config[issuer_key])
    require(issuer.path.endswith("/"), "issuer must include its canonical trailing slash")
    check_url(config["jwks_url"])


def ha_config(config, listen):
    keys(config, ("listen", "resource_url", "issuer_url", "jwks_url",
                  "ha_url", "backend_token_file", "entities"))
    trust(config, "resource_url", "issuer_url", check_url=ha_canonical_url)
    require(config["listen"] == listen, "HA listen differs from the explicit bind")
    ha_canonical_url(config["ha_url"], ("http", "https"), origin=True)
    require(config["backend_token_file"] == "/etc/ha-mcp/backend-token",
            "HA token path is fixed")
    ids = []
    for entity in records(config["entities"], 1, 64):
        keys(entity, ("id", "name", "actions"), ("control_qualified", "brightness"))
        match(entity["id"], r"(light|switch|sensor|binary_sensor)\.[a-z0-9_]{1,96}")
        ids.append(entity["id"])
        label(entity["name"], 128)
        actions = records(entity["actions"], 1, 3)
        require(all(a in ("state.read", "light.control", "switch.control") for a in actions),
                "unknown HA action")
        unique(actions)
        if "control_qualified" in entity:
            require(type(entity["control_qualified"]) is bool, "qualification must be boolean")
        for action in ("light.control", "switch.control"):
            if action in actions:
                require(entity["id"].startswith(action.split(".")[0] + ".")
                        and entity.get("control_qualified") is True, "unqualified control")
        require(not entity.get("control_qualified", False)
                or any(a in actions for a in ("light.control", "switch.control")),
                "control qualification requires a control action")
        if "brightness" in entity:
            require("light.control" in actions, "brightness requires light control")
            bounds = entity["brightness"]
            keys(bounds, ("min_percent", "max_percent"))
            integer(bounds["min_percent"], 0, 100)
            integer(bounds["max_percent"], bounds["min_percent"], 100)
    unique(ids)
    return {"backend-token"}


def observer_https_url(value, origin=False):
    p = url(value, origin=origin)
    require(len(value.encode("utf-8")) <= 512
            and quote_from_bytes(unquote_to_bytes(p.path), safe="/$&+,:;=@-_.~") == p.path,
            "observer URL must fit 512 bytes and have no alternate escaped path")
    return p


def observer_config(config):
    keys(config, ("resource", "issuer", "jwks_url", "inventory_revision",
                  "stale_after_seconds", "hosts", "services", "gatus"))
    trust(config, "resource", "issuer", check_url=observer_https_url)
    match(config["inventory_revision"], r"[0-9a-f]{40,64}")
    integer(config["stale_after_seconds"], 1, 86400)
    host_ids, backend_ids, service_ids, pairs, credentials = [], [], [], [], set()
    for host in records(config["hosts"], 0, 512):
        keys(host, ("id", "name"))
        identifier(host["id"])
        label(host["name"], 80)
        host_ids.append(host["id"])
    unique(host_ids)
    for backend in records(config["gatus"], 0, 16):
        keys(backend, ("id", "base_url", "version", "source_revision"), ("credentials_file",))
        identifier(backend["id"])
        backend_ids.append(backend["id"])
        observer_https_url(backend["base_url"], origin=True)
        require(backend["version"] == "v5.37.0"
                and backend["source_revision"] == "ae605f291928ed9b3bdbe60ae5528cc57631433b",
                "unqualified Gatus contract")
        if "credentials_file" in backend:
            name = "gatus-" + backend["id"] + ".json"
            require(backend["credentials_file"] == "/etc/homelab-observer/" + name,
                    "Gatus credential path is fixed by backend ID")
            credentials.add(name)
    unique(backend_ids)
    for service in records(config["services"], 0, 512):
        keys(service, ("id", "name", "host_id", "probes"))
        identifier(service["id"])
        service_ids.append(service["id"])
        label(service["name"], 80)
        require(service["host_id"] in host_ids, "unknown host reference")
        for probe in records(service["probes"], 1, 4):
            keys(probe, ("backend_id", "endpoint_key"))
            require(probe["backend_id"] in backend_ids, "unknown backend reference")
            identifier(probe["endpoint_key"])
            pairs.append((probe["backend_id"], probe["endpoint_key"]))
    unique(service_ids)
    unique(pairs)
    return credentials


def holonet_config(config):
    keys(config, ("resource", "issuer", "jwks_url", "server_id", "backend_url",
                  "credential_file", "policy_revision", "policy", "library_guids"),
         ("recommendations", "playlist_operator", "playback", "janitor", "purpose_grants"))
    require(config["resource"] == "https://media.mcp.ryzhkov.dev/mcp",
            "HoloNet resource is fixed")
    trust(config, "resource", "issuer", ha_canonical_url)
    match(config["server_id"], r"[0-9a-f]{32}")
    match(config["policy_revision"], r"[0-9a-f]{40,64}")
    backend = ha_canonical_url(config["backend_url"], ("https", "http"))
    require(backend.path == "/emby" and
            (backend.scheme == "https" or config["backend_url"] == "http://127.0.0.1:8096/emby"),
            "HoloNet backend must be trusted HTTPS /emby or exact host-local Emby")
    require(config["credential_file"] == "/etc/holonet/backend.json",
            "HoloNet runtime credential path is fixed")
    policy = config["policy"]
    keys(policy, ("Server", "Actions", "Libraries", "CatalogUsers", "HistoryUsers"))
    playback = config.get("playback") is not None
    require(policy["Server"] == config["server_id"], "policy server must be the configured server")
    if not playback:
        require(policy["Actions"] == ["catalog.read"] + janitor_read_actions(config) and policy["HistoryUsers"] == [],
                "initial HoloNet release is catalog-only")
    libraries = records(policy["Libraries"], 1, 1)
    users = records(policy["CatalogUsers"], 1, 1)
    for library in libraries:
        match(library, r"[1-9][0-9]{0,18}")
        require(int(library) <= 9223372036854775807, "catalog ID outside int64")
    for user in users:
        match(user, r"[0-9a-f]{32}")
    mapping = config["library_guids"]
    require(isinstance(mapping, dict) and set(mapping) == set(libraries),
            "exact catalog-to-policy folder mapping required")
    for guid in mapping.values():
        match(guid, r"[0-9a-f]{32}")
    if "recommendations" in config:
        holonet_recommendations(config["recommendations"], libraries, users)
    if config.get("playlist_operator") is not None:
        holonet_playlist_operator(config, libraries, users)
    holonet_purposes(config)
    if "janitor" in config:
        holonet_janitor(config)
    if playback:
        holonet_playback(config)
        return {"backend.json", HOLONET_PLAYBACK_CREDENTIAL}
    return {"backend.json"}


def holonet_playback(config):
    """The Go playback rules plus homelab's fixed paths and scoped history ceiling.

    Only with management configured; only history.read added to the server
    ceiling (never userdata.read); history users a subset of the fixed cohort.
    """
    value, operator = config["playback"], config.get("playlist_operator")
    require(operator is not None, "playback requires playlist_operator")
    keys(value, ("jellystat_url", "credential_file", "credential_expires_at"),
         ("history_max_lookback_days",))
    if "history_max_lookback_days" in value:
        integer(value["history_max_lookback_days"], 0, 3650)
    jellystat = value["jellystat_url"]
    if jellystat != HOLONET_PLAYBACK_LOOPBACK:
        p = ha_canonical_url(jellystat, origin=True)
        require(p.scheme == "https" and "%" not in jellystat,
                "Jellystat must be the host-local listener or an HTTPS origin")
    require(value["credential_file"] == "/etc/holonet/" + HOLONET_PLAYBACK_CREDENTIAL
            and value["credential_file"] != config["credential_file"],
            "Jellystat runtime credential path is fixed and separate")
    expires = value["credential_expires_at"]
    match(expires, r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
    try:
        datetime.strptime(expires, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        require(False, "credential expiry must be a valid UTC time")
    policy = config["policy"]
    require(policy["Actions"] == ["catalog.read", "history.read"] + janitor_read_actions(config),
            "playback adds only history.read to the catalog ceiling")
    history = records(policy["HistoryUsers"], 1, 16)
    require(all(isinstance(u, str) for u in history), "invalid history user")
    unique(history)
    require(set(history) <= set(operator["recipients"]),
            "history users must be configured management recipients")


def holonet_purposes(config):
    operator = config.get("playlist_operator")
    grants = config.get("purpose_grants")
    require(grants is not None or not operator or len(holonet_subjects(operator)) < 2,
            "coexistence requires exact purpose grants")
    if grants is None:
        return
    scopes = {
        "catalog": ["holonet.read"],
        "writer": ["holonet.read", "holonet.curate", "holonet.home"],
        "importer": ["holonet.read", "holonet.janitor"],
        "torrent": ["holonet.read", "holonet.janitor", "holonet.torrents"],
        "seed_join": ["holonet.read", "holonet.janitor", "holonet.seedjoin"],
    }
    require(isinstance(grants, dict) and 1 <= len(grants) <= 5 and set(grants) <= set(scopes),
            "invalid purpose map")
    for name, grant in grants.items():
        keys(grant, ("subjects", "scopes", "namespace", "libraries", "catalog_users",
                     "history_users", "target_users", "actions"))
        for field in ("subjects", "scopes", "libraries", "catalog_users", "history_users", "target_users", "actions"):
            require(isinstance(grant[field], list) and all(isinstance(v, str) for v in grant[field]),
                    "purpose fields must be string lists")
            unique(grant[field])
        require(1 <= len(grant["subjects"]) <= 5 and set(grant["scopes"]) == set(scopes[name]),
                "invalid purpose subjects or scopes")
        for subject in grant["subjects"]:
            match(subject, r"[!-~]{1,128}")
        require(set(grant["catalog_users"]) <= set(config["policy"]["CatalogUsers"]) and
                set(grant["history_users"]) <= set(config["policy"]["HistoryUsers"]),
                "purpose exceeds user ceiling")
        if name == "catalog":
            require(grant["namespace"] == "movies-catalog" and grant["actions"] == ["catalog.read"]
                    and not grant["history_users"] and not grant["target_users"]
                    and set(grant["libraries"]) <= set(config["policy"]["Libraries"]),
                    "catalog purpose must remain read only")
        else:
            require(operator is not None and set(grant["subjects"]) == holonet_subjects(operator),
                    "purpose writer subjects differ")
            if name == "writer":
                allowed = {"catalog.read", "playlist.read", "playlist.manage", "object.share",
                           "home.read", "home.manage", "object.delete", "history.read"}
                require(grant["namespace"] == OPERATOR_NAMESPACE and set(grant["actions"]) <= allowed
                        and set(grant["target_users"]) <= set(operator["recipients"])
                        and set(grant["history_users"]) <= set(operator["recipients"])
                        and set(grant["libraries"]) <= {operator["library"], *operator.get("series_libraries", {})},
                        "writer purpose exceeds ceiling")
            else:
                actions = {"importer": {"janitor.importers.read", "janitor.custody.read"},
                           "torrent": {"janitor.torrents.read"}, "seed_join": {"janitor.seed_join.read"}}
                require(grant["namespace"] == "managed-janitor" and grant["libraries"] == [operator["library"]]
                        and set(grant["actions"]) == actions[name]
                        and not grant["history_users"] and not grant["target_users"],
                        "janitor purpose exceeds read ceiling")


def holonet_subjects(value):
    subject = value["subject"]
    subjects = value.get("subjects", [subject])
    require(isinstance(subjects, list) and 1 <= len(subjects) <= 2,
            "invalid bounded coexistence subjects")
    require(all(isinstance(s, str) and 1 <= len(s) <= 128
                and all(33 <= ord(c) <= 126 for c in s) for s in subjects),
            "invalid bounded subject")
    unique(subjects)
    require(subject in subjects, "original subject must remain in coexistence set")
    return set(subjects)


def holonet_playlist_operator(config, libraries, users):
    """The Go playlist_operator rules plus homelab's fixed ceiling and cohort."""
    value = config["playlist_operator"]
    require("recommendations" not in config, "playlist_operator excludes legacy recommendations")
    keys(value, ("wire_version", "subject", "namespace", "library", "catalog_user", "recipients"),
         ("series_libraries", "subjects",))
    integer(value["wire_version"], 1, 1)
    holonet_subjects(value)
    subject = value["subject"]
    require(isinstance(subject, str) and 1 <= len(subject) <= 128
            and all(33 <= ord(c) <= 126 for c in subject), "invalid bounded subject")
    require(value["namespace"] == OPERATOR_NAMESPACE, "fixed writer namespace required")
    require(len(config["issuer"]) <= 512, "operator issuer exceeds the wire bound")
    require(config["server_id"] == OPERATOR_SERVER
            and value["library"] == OPERATOR_LIBRARY and value["library"] in libraries
            and value["catalog_user"] == OPERATOR_CATALOG_USER and value["catalog_user"] in users,
            "operator ceiling must be the existing homelab Movies read ceiling")
    recipients = records(value["recipients"], 1, 16)
    require(all(isinstance(r, str) for r in recipients), "invalid operator recipient")
    unique(recipients)
    require(all(r in OPERATOR_RECIPIENTS for r in recipients),
            "recipient outside the fixed protected operator cohort")
    series = value.get("series_libraries")
    require(series is None or (isinstance(series, dict) and 1 <= len(series) <= 2),
            "invalid bounded Series library map")
    series = series or {}
    require(not series or config.get("playback") is not None,
            "Series libraries require scoped playback")
    guids = []
    for library, metadata in series.items():
        match(library, r"[1-9][0-9]{0,18}")
        require(int(library) <= 9223372036854775807 and library not in libraries
                and library != value["library"], "Series libraries must not widen the catalog ceiling")
        keys(metadata, ("guid", "name", "category"))
        match(metadata["guid"], r"[0-9a-f]{32}")
        require(metadata["guid"] not in config["library_guids"].values(),
                "Series GUID must differ from catalog libraries")
        label(metadata["name"], 128)
        require(metadata["category"] in ("tv", "anime"), "invalid Series category")
        guids.append(metadata["guid"])
    unique(guids)


def holonet_recommendations(value, libraries, users):
    keys(value, ("state_directory", "owner", "subjects", "namespaces", "actions",
                 "libraries", "catalog_users", "targets"))
    require(value["state_directory"] == "/var/lib/holonet/recommendations", "fixed private journal required")
    require(value["owner"] == users[0], "recommendation owner must be the dedicated catalog user")
    require(value["libraries"] == libraries and value["catalog_users"] == users,
            "recommendations cannot widen the independent catalog ceiling")
    subjects = records(value["subjects"], 1, 16)
    unique(subjects)
    for subject in subjects:
        require(isinstance(subject, str) and 1 <= len(subject) <= 128
                and all(33 <= ord(c) <= 126 for c in subject), "invalid bounded subject")
    require(value["namespaces"] == ["managed-recommendations"], "fixed writer namespace required")
    actions = ["catalog.read", "playlist.read", "playlist.manage", "object.share", "home.read", "home.manage"]
    require(value["actions"] in (actions, actions + ["object.delete"]), "exact writer actions required")
    targets = value["targets"]
    require(isinstance(targets, dict) and 1 <= len(targets) <= 16, "explicit bounded recipient map required")

    def row_id(raw):
        if type(raw) is int:
            require(0 <= raw <= 9223372036854775807, "invalid numeric home row ID")
            return ("integer", raw)
        require(isinstance(raw, str) and 1 <= len(raw) <= 128
                and all(33 <= ord(c) <= 126 for c in raw), "invalid string home row ID")
        return ("string", raw)

    for target, home in targets.items():
        match(target, r"[0-9a-f]{32}")
        keys(home, ("baseline_ids", "anchor_id"))
        baseline = [row_id(raw) for raw in records(home["baseline_ids"], 1, 64)]
        unique(baseline)
        require(row_id(home["anchor_id"]) in baseline, "placement anchor must be a baseline row")


CUSTODY_ACTIONS = ["janitor.importers.read", "janitor.custody.read"]
CUSTODY_DESCRIPTOR = "/etc/holonet-playlists/radarr.json"


def janitor_read_actions(config):
    if "janitor" not in config:
        return []
    value = config["janitor"]
    return ((CUSTODY_ACTIONS if "custody_source" in value else CUSTODY_ACTIONS[:1])
            + (["janitor.torrents.read"] if "torrent_source" in value else [])
            + ([SEED_JOIN_ACTION] if "seed_join_source" in value else [])
            + (["janitor.acquire.read", "janitor.acquire.start", "janitor.acquire.media"]
               if "acquire_source" in value else [])) if isinstance(value, dict) else []


def holonet_janitor(config):
    value = config["janitor"]
    operator = config.get("playlist_operator")
    keys(value, ("importer_source", "state_directory", "subject",
                 "library", "protected_users", "recipients", "revision"), ("subjects", "custody_source", "torrent_source", "torrent_resource_id", "seed_join_source",
                 "acquire_source", "acquire_series_libraries", "acquire_torrent_resource_id"))
    require(operator is not None and ("custody_source" not in value or value["custody_source"] == "arr_custody_v1")
            and value["importer_source"] == "arr_inventory_v2"
            and value["state_directory"] == "/var/lib/holonet/janitor"
            and value["subject"] == operator["subject"]
            and holonet_subjects(value) == holonet_subjects(operator)
            and value["library"] == operator["library"]
            and value["revision"] == config["policy_revision"],
            "custody requires its fixed independent binding and journal")
    acquire_keys = {"acquire_source", "acquire_series_libraries", "acquire_torrent_resource_id"} & set(value)
    require(not acquire_keys or (
        acquire_keys == {"acquire_source", "acquire_series_libraries", "acquire_torrent_resource_id"} and
        value["acquire_source"] == "sonarr_qbit_stage_v1" and
        value["acquire_torrent_resource_id"] == "homelab-qbit-stage" and
        value["acquire_series_libraries"] == ["615"] and
        "615" in operator.get("series_libraries", {})),
        "acquisition requires the explicit Anime stage binding")
    torrent_keys = {"torrent_source", "torrent_resource_id"} & set(value)
    require(not torrent_keys or (torrent_keys == {"torrent_source", "torrent_resource_id"}
            and value["torrent_source"] == "qbit_inventory_v1"
            and value["torrent_resource_id"] == TORRENT_RESOURCE
            and value["library"] == OPERATOR_LIBRARY), "invalid torrent source binding")
    require("seed_join_source" not in value or (value["seed_join_source"] == SEED_JOIN_SOURCE
            and torrent_keys == {"torrent_source", "torrent_resource_id"}),
            "seed join requires its fixed source and the torrent pair")
    for name in ("protected_users", "recipients"):
        users = records(value[name], 1, 16)
        require(all(isinstance(u, str) for u in users), "invalid custody cohort")
        unique(users)
        require(set(users) == set(operator["recipients"]), "custody cohort must match operator")


def custody_install_plan(value):
    """Root-owned metadata only. Never copy or inspect the existing XML key."""
    config = value["config"]
    require(("janitor" in config) == ("custody" in value), "custody inputs must be paired")
    if "custody" not in value:
        return None
    require(value["service"] == "holonet", "custody is HoloNet only")
    holonet_janitor(config)
    data = value["custody"]
    keys(data, ("descriptor",), ("roots", "recycle"))
    full_custody = "custody_source" in config["janitor"]
    require(("roots" in data) == ("recycle" in data) == full_custody,
            "filesystem mappings require explicit custody opt-in")
    desc = data["descriptor"]
    keys(desc, ("version", "backend", "resource_id", "origin", "expected_version",
                "app_config_file", "app_config_owner_uid", "app_config_read_gid"))
    integer(desc["version"], 1, 1)
    require(desc["backend"] == "radarr" and desc["expected_version"] == "6.4.4.10685"
            and desc["app_config_file"] == "/mnt/docker-data/swarm/radarr-movie/config/config.xml",
            "only the fixed Radarr instance is qualified for custody enrollment")
    integer(desc["app_config_owner_uid"], 1002, 1002)
    integer(desc["app_config_read_gid"], 988, 988)
    match(desc["resource_id"], r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    origin = url(desc["origin"], schemes=("https", "http"), origin=True)
    require(origin.scheme == "https" or desc["origin"] == "http://127.0.0.1:7878",
            "Radarr HTTP origin must be the fixed loopback listener")
    plan = {"path": CUSTODY_DESCRIPTOR, "descriptor": dict(desc),
            "importers": {"target_library": config["janitor"]["library"],
                          "revision": config["janitor"]["revision"],
                          "backends": {"radarr": CUSTODY_DESCRIPTOR}}}
    if not full_custody:
        return plan
    roots = records(data["roots"], 1, 1)
    require(roots[0] == {"app_prefix": "/movies", "host_prefix": "/mnt/shared/media/movies"}
            or roots[0] == {"app_prefix": "/mnt/shared/media/movies",
                            "host_prefix": "/mnt/shared/media/movies"},
            "custody media mapping must be the existing Movies root")
    mappings = [*roots, data["recycle"]]
    for mapping in mappings:
        keys(mapping, ("app_prefix", "host_prefix"))
        for key in ("app_prefix", "host_prefix"):
            path = mapping[key]
            require(isinstance(path, str) and 1 < len(path.encode("utf-8")) <= 4096
                    and path.startswith("/") and not path.startswith("//")
                    and str(PurePosixPath(path)) == path
                    and all(part not in (".", "..") for part in path.split("/")[1:]),
                    "invalid normalized custody mapping")
        require(mapping["host_prefix"].startswith("/mnt/shared/"),
                "custody host roots must be on the existing shared mount")
    for key in ("app_prefix", "host_prefix"):
        for i, left in enumerate(mappings):
            a = left[key]
            require(not any(a == right[key] or a.startswith(right[key] + "/")
                            or right[key].startswith(a + "/") for right in mappings[i + 1:]),
                    "custody mappings must be disjoint")
    return {**plan, "custody": {"backends": {"radarr": {"roots": roots, "recycle": data["recycle"]}}}}


def qbit_auth(value):
    keys(value, ("username", "password"))
    for key, cap in (("username", 256), ("password", 4096)):
        text = value[key]
        require(isinstance(text, str) and 1 <= len(text.encode("utf-8")) <= cap
                and not any(c in text for c in "\x00\r\n"), "invalid qBit credential schema")
    return value


def torrent_install_plan(value):
    config = value["config"]
    janitor = config.get("janitor")
    selected = isinstance(janitor, dict) and bool({"torrent_source", "torrent_resource_id"} & set(janitor))
    require(selected == ("torrents" in value), "torrent inputs must be paired")
    if not selected:
        return None
    require(value["service"] == "holonet", "torrents are HoloNet only")
    holonet_janitor(config)
    data = value["torrents"]
    keys(data, ("descriptor",))
    expected = dict(version=1, resource_id=TORRENT_RESOURCE,
        origin="https://torrent.ryzhkov.dev", expected_version="v5.3.0",
        credential_file=TORRENT_AUTH, categories=["radarr-movies"],
        payload_prefixes=["/mnt/shared/downloads/complete/movies"])
    require(data["descriptor"] == expected,
            "torrent descriptor must match the canonical Movies source")
    integer(data["descriptor"].get("version"), 1, 1)
    return {"path": TORRENT_DESCRIPTOR, "descriptor": expected,
            "policy": {"target_library": OPERATOR_LIBRARY,
                       "revision": config["policy_revision"], "config": TORRENT_DESCRIPTOR}}



def acquire_install_plan(value):
    """Explicit default-off package support, never credential creation."""
    config = value["config"]
    j = config.get("janitor") or {}
    selected = any(k in j for k in
        ("acquire_source", "acquire_series_libraries", "acquire_torrent_resource_id"))
    require(selected == ("acquire" in value), "acquisition inputs must be paired")
    if not selected:
        return None
    holonet_janitor(config)
    data = value["acquire"]
    keys(data, ("sonarr", "start_enabled", "media_enabled"))
    require(type(data["start_enabled"]) is bool and type(data["media_enabled"]) is bool,
            "acquisition flags must be explicit booleans")
    desc = data["sonarr"]
    keys(desc, ("version", "backend", "resource_id", "origin", "expected_version",
        "app_config_file", "app_config_owner_uid", "acquire_download_origins"),
        ("app_config_read_gid",))
    integer(desc["version"], 1, 1)
    integer(desc["app_config_owner_uid"], 1002, 1002)
    require(desc["backend"] == "sonarr" and desc["expected_version"] == "4.0.20.3014" and
        desc["app_config_file"] == "/mnt/docker-data/swarm/sonarr-tv/config/config.xml" and
        desc["app_config_owner_uid"] == 1002,
        "acquisition requires its fixed Sonarr instance")
    if "app_config_read_gid" in desc:
        integer(desc["app_config_read_gid"], 988, 988)
    require(desc["origin"] == "https://sonarr.ryzhkov.dev",
            "acquisition Sonarr origin is fixed")
    match(desc["resource_id"], r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    require(desc["acquire_download_origins"] == ["https://prowlarr.ryzhkov.dev"],
            "acquisition download origin is fixed")
    require("torrents" in value, "reuse existing guarded qBit credential custody")
    stage = dict(value["torrents"]["descriptor"])
    stage.update(resource_id="homelab-qbit-stage", categories=["holonet-stage"],
                 payload_prefixes=["/mnt/shared/downloads/staging/holonet"])
    return {"sonarr_path": "/etc/holonet-playlists/sonarr.json",
            "sonarr": dict(desc), "stage_path": "/etc/holonet-playlists/qbit-stage.json",
            "stage": stage,
            "policy": {"target_library": OPERATOR_LIBRARY,
                "revision": config["policy_revision"],
                "sonarr": "/etc/holonet-playlists/sonarr.json",
                "torrents": "/etc/holonet-playlists/qbit-stage.json",
                "series_libraries": list(j["acquire_series_libraries"]),
                "start_enabled": data["start_enabled"], "media_enabled": data["media_enabled"]}}

def seed_join_install_plan(value):
    """Root policy block only. The seed root is the fixed constant, never an input."""
    config = value["config"]
    janitor = config.get("janitor")
    selected = isinstance(janitor, dict) and "seed_join_source" in janitor
    require(selected == ("seed_join" in value), "seed join inputs must be paired")
    if not selected:
        return None
    require(value["service"] == "holonet", "seed join is HoloNet only")
    holonet_janitor(config)
    require(torrent_install_plan(value) is not None and "custody" in value,
            "seed join requires the torrent descriptor and Radarr importer inputs")
    data = value["seed_join"]
    keys(data, ("library_root",))
    root = data["library_root"]
    require(root == {"app_prefix": "/movies", "host_prefix": "/mnt/shared/media/movies"}
            or root == {"app_prefix": "/mnt/shared/media/movies",
                        "host_prefix": "/mnt/shared/media/movies"},
            "seed join library root must be the existing Movies root")
    custody = value["custody"].get("roots")
    require(custody is None or custody[0] == root,
            "seed join library root must equal the custody Movies root")
    return {"policy": {"target_library": OPERATOR_LIBRARY, "revision": config["policy_revision"],
                       "library_root": dict(root),
                       "seed_root": {"app_prefix": SEED_CEILING, "host_prefix": SEED_CEILING}}}


def endpoint_contract(value):
    keys(value, ("service", "uid", "gid", "listen", "artifact", "config", "credentials"),
         ("custody", "torrents", "seed_join", "acquire"))
    service = value["service"]
    require(service in SERVICES, "unregistered fixed service package")
    integer(value["uid"], 100, 999)
    integer(value["gid"], 100, 999)
    private_address(value["listen"])
    artifact = value["artifact"]
    local_holonet = service == "holonet" and "local_source" in artifact
    keys(artifact, ("sha256", "source_revision", "contract_revision") +
         (() if local_holonet else ("url",)), ("local_source", "url") if local_holonet else ("local_source",))
    match(artifact["sha256"], r"[0-9a-f]{64}")
    match(artifact["source_revision"], r"[0-9a-f]{40}")
    _, contract = SERVICES[service]
    if service == "holonet" and artifact["source_revision"] == HOLONET_WRITER_SOURCE:
        contract = HOLONET_WRITER_CONTRACT
    acquire_build = (service == "holonet" and HOLONET_ACQUIRE_SOURCE is not None
                     and HOLONET_ACQUIRE_SHA256 is not None and HOLONET_ACQUIRE_CONTRACT is not None
                     and artifact["source_revision"] == HOLONET_ACQUIRE_SOURCE)
    if acquire_build:
        require(artifact["sha256"] == HOLONET_ACQUIRE_SHA256, "unaccepted acquisition binary")
    coexistence_build = service == "holonet" and artifact["source_revision"] == HOLONET_COEXISTENCE_SOURCE
    seed_join_build = coexistence_build or acquire_build or (service == "holonet" and HOLONET_SEED_JOIN_SOURCE is not None
                       and HOLONET_SEED_JOIN_CONTRACT is not None
                       and HOLONET_SEED_JOIN_SHA256 is not None
                       and artifact["source_revision"] == HOLONET_SEED_JOIN_SOURCE)
    if seed_join_build and not acquire_build and not coexistence_build:
        require(artifact["sha256"] == HOLONET_SEED_JOIN_SHA256, "unaccepted seed join binary")
    torrent_build = seed_join_build or (
        service == "holonet" and artifact["source_revision"] == HOLONET_TORRENT_SOURCE)
    if torrent_build and not seed_join_build:
        require(artifact["sha256"] == HOLONET_TORRENT_SHA256, "unaccepted torrent binary")
    custody_build = torrent_build or (service == "holonet" and HOLONET_CUSTODY_SOURCE is not None
                     and HOLONET_CUSTODY_CONTRACT is not None
                     and artifact["source_revision"] == HOLONET_CUSTODY_SOURCE)
    history_range_build = custody_build or (service == "holonet" and HOLONET_HISTORY_RANGE_SOURCE is not None
                           and HOLONET_HISTORY_RANGE_CONTRACT is not None
                           and artifact["source_revision"] == HOLONET_HISTORY_RANGE_SOURCE)
    series_build = history_range_build or (service == "holonet" and HOLONET_SERIES_SOURCE is not None
                    and HOLONET_SERIES_CONTRACT is not None
                    and artifact["source_revision"] == HOLONET_SERIES_SOURCE)
    playback_build = series_build or (service == "holonet" and HOLONET_PLAYBACK_SOURCE is not None
                      and HOLONET_PLAYBACK_CONTRACT is not None
                      and artifact["source_revision"] == HOLONET_PLAYBACK_SOURCE)
    operator_build = playback_build or (
        service == "holonet" and HOLONET_OPERATOR_SOURCE is not None
        and artifact["source_revision"] == HOLONET_OPERATOR_SOURCE)
    if coexistence_build:
        require(artifact["sha256"] == HOLONET_COEXISTENCE_SHA256, "unaccepted coexistence binary")
        contract = HOLONET_COEXISTENCE_SOURCE
    elif acquire_build:
        contract = HOLONET_ACQUIRE_CONTRACT
    elif seed_join_build:
        contract = HOLONET_SEED_JOIN_CONTRACT
    elif torrent_build:
        contract = HOLONET_TORRENT_CONTRACT
    elif custody_build:
        contract = HOLONET_CUSTODY_CONTRACT
    elif history_range_build:
        contract = HOLONET_HISTORY_RANGE_CONTRACT
    elif series_build:
        contract = HOLONET_SERIES_CONTRACT
    elif playback_build:
        contract = HOLONET_PLAYBACK_CONTRACT
    elif operator_build:
        contract = HOLONET_OPERATOR_CONTRACT
    require(artifact["contract_revision"] == contract, "unaccepted source interface")
    if "url" in artifact:
        p = url(artifact["url"])
        require(p.path not in ("", "/") and "/latest/" not in p.path,
                "expected an explicit reviewed static binary URL")
    if "local_source" in artifact:
        source = artifact["local_source"]
        keys(source, ("path", "owner_uid"))
        path = source["path"]
        require(isinstance(path, str) and len(path.encode("utf-8")) <= 4096,
                "invalid local artifact path")
        match(path, r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*")
        require(all(part not in (".", "..") for part in path.split("/")),
                "unsafe local artifact path")
        integer(source["owner_uid"], 0, 2147483647)
    if service == "holonet":
        require(artifact["source_revision"] in HOLONET_SOURCE_REVISIONS or operator_build,
                "unaccepted HoloNet source revision")
        require("recommendations" not in value["config"] or artifact["source_revision"] == HOLONET_WRITER_SOURCE,
                "writer configuration requires its exact reviewed artifact")
        require("janitor" not in value["config"] or custody_build,
                "custody configuration requires its exact reviewed artifact")
        require(not ("purpose_grants" in value["config"] or "subjects" in (value["config"].get("playlist_operator") or {}) or "subjects" in (value["config"].get("janitor") or {})) or coexistence_build,
                "coexistence policy requires its exact compatible binary")
        expected = holonet_config(value["config"])
        custody_install_plan(value)
        torrent_install_plan(value)
        require(not ({"torrent_source", "torrent_resource_id"} & set(value["config"].get("janitor") or {}))
                or torrent_build, "torrent keys require their exact reviewed artifact")
        # Older builds refuse the unknown key; require the exact compatible build.
        require("seed_join_source" not in (value["config"].get("janitor") or {}) or seed_join_build,
                "seed join keys require their exact reviewed artifact")
        seed_join_install_plan(value)
        require(not ({"acquire_source", "acquire_series_libraries", "acquire_torrent_resource_id"} &
                    set(value["config"].get("janitor") or {})) or acquire_build,
                "acquisition keys require the exact reviewed artifact")
        acquire_install_plan(value)
        # Older binaries refuse the unknown key, so even null needs the new build.
        require("playlist_operator" not in value["config"] or operator_build,
                "playlist_operator requires its exact reviewed artifact")
        require("playback" not in value["config"] or playback_build,
                "playback requires its exact reviewed artifact")
        if value["config"].get("playback") is not None:
            require("history_max_lookback_days" not in value["config"]["playback"] or history_range_build,
                    "history range configuration requires its exact reviewed artifact")
        if value["config"].get("playlist_operator") is not None:
            require("series_libraries" not in value["config"]["playlist_operator"] or series_build,
                    "Series configuration requires its exact reviewed artifact")
            require(value["uid"] == OPERATOR_UID and value["gid"] == OPERATOR_GID,
                    "management requires the homelab holonet identity")
    else:
        require("custody" not in value and "torrents" not in value and "seed_join" not in value,
                "root readers are HoloNet only")
        expected = (ha_config(value["config"], value["listen"]) if service == "ha-mcp"
                    else observer_config(value["config"]))
    names = []
    for credential in records(value["credentials"], 0, 16):
        keys(credential, ("name", "source", "source_uid"))
        names.append(credential["name"])
        match(credential["source"], r"/[A-Za-z0-9/_.-]+")
        require(".." not in credential["source"].split("/"), "unsafe source path")
        integer(credential["source_uid"], 0, 2147483647)
    unique(names)
    require(set(names) == expected, "credential sources must exactly match config references")
    if service == "holonet":
        # Each runtime file comes from its own protected source; the Emby key
        # is never installed again as the Jellystat key.
        unique([credential["source"] for credential in value["credentials"]])
    return value


def operator_release():
    """Release identity: SHA256 of the sorted sha256sum-style module manifest."""
    manifest = "".join(digest + "  " + name + "\n" for name, digest in sorted(OPERATOR_MODULES.items()))
    return hashlib.sha256(manifest.encode("ascii")).hexdigest()


def playlist_operator_plan(value):
    """Fixed root worker deployment for a validated endpoint; selected only when
    a HoloNet config carries a non-null playlist_operator."""
    endpoint_contract(value)
    operator = value["config"].get("playlist_operator") if value["service"] == "holonet" else None
    if operator is None:
        return {"selected": False}
    modules = []
    for name, digest in sorted(OPERATOR_MODULES.items()):
        source = OPERATOR_SCRIPTS / name
        require(not source.is_symlink() and source.is_file()
                and hashlib.sha256(source.read_bytes()).hexdigest() == digest,
                "worker module differs from its reviewed pin")
        modules.append({"name": name, "sha256": digest, "source": str(source)})
    release = operator_release()
    selected = sorted(operator["recipients"])
    config = value["config"]
    custody = custody_install_plan(value)
    torrents = torrent_install_plan(value)
    seed_join = seed_join_install_plan(value)
    acquire = acquire_install_plan(value)
    return {
        **({"acquire_descriptor": acquire} if acquire else {}),
        **({"torrent_descriptor": torrents} if torrents else {}),
        **({"custody_descriptor": custody} if custody else {}),
        "selected": True,
        "release": release,
        "release_directory": OPERATOR_RELEASES + "/" + release,
        "modules": modules,
        "pins": dict(OPERATOR_MODULES),
        "configs": [OPERATOR_RECIPIENTS[r] for r in selected],
        "journals": [str(PurePosixPath(OPERATOR_RECIPIENTS[r]).parent / "journal") for r in selected],
        "policy": {
            **({"janitor_acquire": acquire["policy"]} if acquire else {}),
            **({"janitor_torrents": torrents["policy"]} if torrents else {}),
            **({"janitor_importers": custody["importers"]} if custody else {}),
            **({"janitor_custody": custody["custody"]} if custody and "custody" in custody else {}),
            **({"janitor_seed_join": seed_join["policy"]} if seed_join else {}),
            "version": 1, "enabled": True,
            **({"subjects": operator["subjects"]} if "subjects" in operator else {}),
            "peer": {"uid": value["uid"], "gid": value["gid"]},
            "caller": {"issuer": config["issuer"], "subject": operator["subject"],
                       "namespace": operator["namespace"], "server": config["server_id"],
                       "library": operator["library"], "catalog_user": operator["catalog_user"]},
            "recipients": {r: OPERATOR_RECIPIENTS[r] for r in selected},
            **({"history_users": sorted(config["policy"]["HistoryUsers"])}
               if config.get("playback") is not None else {}),
            **({"series_libraries": {key: dict(metadata) for key, metadata in
                                     operator["series_libraries"].items()}}
               if operator.get("series_libraries") else {}),
        },
    }


# Route-only services: packaged elsewhere, routed by this contract.
ROUTE_ONLY = ("ov-http", "jocasta", "shizune")
# Jocasta's canonical resource and its existing Compose backend on homelab's
# mesh address (stacks/jocasta). Both are fixed so a route cannot send
# Jocasta-audience bearer tokens anywhere else.
JOCASTA_ROUTE = {"resource": "https://jocasta.mcp.ryzhkov.dev/mcp",
                 "upstream": "http://10.42.0.2:1935"}


def route_contract(value):
    keys(value, ("service", "state", "directory_uid"), ("resource", "upstream"))
    require(value["service"] in (*SERVICES, *ROUTE_ONLY) and value["state"] in ("present", "absent"),
            "invalid route selection")
    integer(value["directory_uid"], 0, 2147483647)
    if value["service"] == "holonet" and value["state"] == "present":
        require(value.get("resource") == "https://media.mcp.ryzhkov.dev/mcp", "HoloNet route resource is fixed")
    if value["service"] == "jocasta" and value["state"] == "present":
        require(all(value.get(key) == fixed for key, fixed in JOCASTA_ROUTE.items()),
                "Jocasta route resource and upstream are fixed")
    if value["service"] == "shizune" and value["state"] == "present":
        require(value.get("resource") == "https://shizune.mcp.ryzhkov.dev/mcp"
                and value.get("upstream") == "http://10.42.0.2:1937",
                "Shizune route resource and upstream are fixed")
    if value["state"] == "absent":
        return value
    require("resource" in value and "upstream" in value, "explicit route addresses required")
    resource = url(value["resource"])
    require(resource.path == "/mcp" and resource.port is None, "invalid resource URL")
    match(resource.hostname, r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
    upstream = url(value["upstream"], ("http", "https"), origin=True)
    require(upstream.port is not None, "upstream needs an explicit port")
    backend_host = upstream.hostname or ""
    private_address(backend_host + ":" + str(upstream.port))
    # Loopback only works when Traefik actually shares the backend network namespace.
    require(not ipaddress.ip_address(backend_host).is_loopback,
            "edge upstream must be a reachable private address")
    edge = dict(value, host=resource.hostname)
    if value["service"] == "jocasta":
        # As qualified by the Jocasta HTTPS canary: send the backend authority as
        # Host for the MCP SDK's host protection, and leave Origin untouched.
        edge["host_rewrite"] = upstream.netloc
    return edge



OV_CONTRACT = "5f5c52c2171a2fef9ebe1439342921b1e58169bd"
OV_FULL_CONTRACT = "1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319"
OV_FULL_SOURCE_REVISIONS = (
    OV_FULL_CONTRACT,
    "4cc776bc4f9a9211b1106f2ba3299d92e82690be",
)
OV_FULL_CONFIG = {
    "semantic_profile": "resources-directories-v052-v1",
    "semantic_backend_revision": "7f3ae1af822d936c1d0b070537772b0f1d59122f",
    "namespace_roots": ["viking://resources"],
    "mutation_profile": "resources-document-mutations-v1",
}


# One measured HTTP release. This is a Docker config ID, not a registry manifest.
OV_LOCAL_RELEASE = {
    "kind": "local-config",
    "reference": "sha256:ce94410e1002fbaaa6fd9f06dd0e3bf90ed7db6e98cb8515f01b189e5c80a696",
    "source_revision": "7d833b8d51b2d8074be1ef376140cd08a9190a9d",
    "contract_revision": OV_FULL_CONTRACT,
    "provenance": "https://github.com/iryzhkov/ov-mcp/actions/runs/41204069205",
}


def ov_local_image(images):
    # The daemon's immutable Id commits to the saved config and ordered RootFS
    # diff_ids. Do not derive an expected identity from tags or observed fields.
    require(isinstance(images, list) and len(images) == 1,
            "required local OV image is absent or ambiguous")
    image = images[0]
    require(isinstance(image, dict) and image.get("Id") == OV_LOCAL_RELEASE["reference"],
            "unaccepted local OV image ID")
    config = image.get("Config")
    if not isinstance(config, dict):
        raise AnsibleFilterError("web-mcp: missing local OV config")
    labels = config.get("Labels")
    require(isinstance(labels, dict), "missing local OV revision label")
    require(config.get("User") == "8098:8098"
            and config.get("WorkingDir") == "/app"
            and config.get("Entrypoint") == [
                "python", "-m", "ov_http", "--config", "/etc/ov-http/runtime.toml"]
            and config.get("Cmd") in (None, [])
            and isinstance(labels, dict)
            and labels.get("org.opencontainers.image.revision")
                == OV_LOCAL_RELEASE["source_revision"],
            "local OV image config mismatch")
    return True


OV_PERSONAL_BACKEND_REVISION = "7f3faf6982f81116d6626bc7091f87f786a309c7"

OV_PERSONAL_FIELDS = ("personal_profile", "personal_account", "personal_user",
                      "personal_subjects", "personal_backend_revision")


def ov_personal_config(config):
    """Validate protected owner inputs; package validation binds the qualified artifacts."""
    profile = config.get("personal_profile", "disabled")
    require(profile in ("disabled", "shared-owner-v1"), "invalid OV personal profile")
    if profile == "disabled":
        require(all(config.get(k, [] if k == "personal_subjects" else "") ==
                    ([] if k == "personal_subjects" else "") for k in OV_PERSONAL_FIELDS[1:]),
                "disabled OV personal profile cannot bind an owner")
        return config
    require(all(k in config for k in OV_PERSONAL_FIELDS), "incomplete OV personal configuration")
    require(config["personal_account"] == "default" and config["personal_user"] == "default",
            "OV personal owner must be the shared default owner")
    subjects = config["personal_subjects"]
    require(isinstance(subjects, list) and 1 <= len(subjects) <= 16,
            "invalid OV personal subject list")
    require(all(isinstance(s, str) and 1 <= len(s) <= 256
                and all(ord(c) >= 33 for c in s) for s in subjects),
            "invalid OV personal subject")
    require(len(set(subjects)) == len(subjects), "duplicate OV personal subject")
    match(config["personal_backend_revision"], r"[0-9a-f]{40}")
    return config


def ov_contract(value):
    keys(value, ("release", "image", "listen", "config", "credential"))
    match(value["release"], r"[a-z0-9][a-z0-9-]{0,63}")
    private_address(value["listen"])
    image = value["image"]
    keys(image, ("reference", "source_revision", "contract_revision", "provenance"), ("kind",))
    local = "kind" in image
    if local:
        require(image == OV_LOCAL_RELEASE, "unaccepted local OV release binding")
    else:
        match(image["reference"], r"[a-z0-9][a-z0-9./:_-]{0,240}@sha256:[0-9a-f]{64}")
        require(":latest@" not in image["reference"], "no latest image tag")
    match(image["source_revision"], r"[0-9a-f]{40}")
    require(image["contract_revision"] in (OV_CONTRACT, OV_FULL_CONTRACT),
            "unaccepted OV reader interface")
    full_resources = image["contract_revision"] == OV_FULL_CONTRACT
    if full_resources and not local:
        require(image["source_revision"] in OV_FULL_SOURCE_REVISIONS,
                "unaccepted full-resource OV source revision")
    p = url(image["provenance"])
    require(p.path not in ("", "/") and "/latest/" not in p.path,
            "explicit reviewed build receipt required")
    config = value["config"]
    required = ("resource", "issuer", "jwks_url", "backend_url",
                "backend_secret_file", "listen_host", "listen_port")
    keys(config, required + (tuple(OV_FULL_CONFIG) if full_resources else ()), OV_PERSONAL_FIELDS)
    ov_personal_config(config)
    if config.get("personal_profile", "disabled") != "disabled":
        require(local and image == OV_LOCAL_RELEASE
                and config["personal_backend_revision"] == OV_PERSONAL_BACKEND_REVISION,
                "OV personal activation requires the exact qualified backend and HTTP binding")
    if full_resources:
        require(all(config[key] == fixed for key, fixed in OV_FULL_CONFIG.items()),
                "full-resource OV requires the exact resources and mutation profiles")
    # Return the supplied object unchanged, including the legacy seven-field config.
    trust(config, "resource", "issuer")
    for key in ("resource", "issuer", "jwks_url", "backend_url"):
        require("%" not in config[key], "escaped runtime URLs refused")
    backend = url(config["backend_url"], ("http", "https"))
    require(backend.path == "/api/v1", "OV backend must end in /api/v1")
    integer(config["listen_port"], 8098, 8098)
    require(config["listen_host"] == "0.0.0.0", "container listen is fixed")
    require(config["backend_secret_file"] == "/etc/ov-http/backend-key", "fixed OV key mount")
    credential = value["credential"]
    keys(credential, ("source", "source_uid"))
    match(credential["source"], r"/[A-Za-z0-9/_.-]+")
    require(".." not in credential["source"].split("/"), "unsafe source path")
    integer(credential["source_uid"], 0, 2147483647)
    return value


class FilterModule:
    def filters(self):
        return {"web_mcp_qbit_auth": qbit_auth,
                "web_mcp_endpoint_contract": endpoint_contract,
                "web_mcp_playlist_operator": playlist_operator_plan,
                "web_mcp_route_contract": route_contract,
                "ov_http_contract": ov_contract,
                "ov_http_local_image": ov_local_image}
