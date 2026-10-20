"""Bounded packaging contracts. Service authorization remains in each binary."""
import ipaddress
import re
from urllib.parse import quote_from_bytes, unquote_to_bytes, urlsplit

from ansible.errors import AnsibleFilterError

SERVICES = {
    "ha-mcp": ("homeassistant-mcp", "027505484cf33947e64f5232cf17a1ff0d96d522"),
    "homelab-observer": ("homelab-cli", "8af24606b7cf5cd89e727660266104699313e993"),
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


def endpoint_contract(value):
    keys(value, ("service", "uid", "gid", "listen", "artifact", "config", "credentials"))
    service = value["service"]
    require(service in SERVICES, "only HA and homelab observer are packaged")
    integer(value["uid"], 100, 999)
    integer(value["gid"], 100, 999)
    private_address(value["listen"])
    artifact = value["artifact"]
    keys(artifact, ("url", "sha256", "source_revision", "contract_revision"))
    match(artifact["sha256"], r"[0-9a-f]{64}")
    match(artifact["source_revision"], r"[0-9a-f]{40}")
    _, contract = SERVICES[service]
    require(artifact["contract_revision"] == contract, "unaccepted source interface")
    p = url(artifact["url"])
    require(p.path not in ("", "/") and "/latest/" not in p.path,
            "expected an explicit reviewed static binary URL")
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
    return value


def route_contract(value):
    keys(value, ("service", "state", "directory_uid"), ("resource", "upstream"))
    require(value["service"] in SERVICES and value["state"] in ("present", "absent"),
            "invalid route selection")
    integer(value["directory_uid"], 0, 2147483647)
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
    return dict(value, host=resource.hostname)


class FilterModule:
    def filters(self):
        return {"web_mcp_endpoint_contract": endpoint_contract,
                "web_mcp_route_contract": route_contract}
