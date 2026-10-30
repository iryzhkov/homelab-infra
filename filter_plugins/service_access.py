"""Pure service-access inspection over explicit, resolved JSON data.

No inventory evaluation, environment lookup, I/O, rendering or application.
The opt-in CLI is the only file reader. This module deliberately does not
certify deployment readiness or equivalence with unmodelled ingress.
"""
import copy
import hashlib
import json
import re

CONTEXTS = ("lan", "nebula", "public")
TRUSTED_LAN = {"192.168.70.0/24", "192.168.90.0/24",
               "192.168.1.0/24", "192.168.120.0/24"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _changes(before, after, path=""):
    if type(before) is not type(after):
        return [{"path": path, "before": before, "after": after}]
    if isinstance(before, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            child = path + "/" + key
            if key not in before:
                result.append({"path": child, "operation": "add", "after": after[key]})
            elif key not in after:
                result.append({"path": child, "operation": "remove", "before": before[key]})
            else:
                result.extend(_changes(before[key], after[key], child))
        return result
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


def _inspect(snapshot):
    """Normalize version-1 resolved policy input; malformed input is diagnostic."""
    diagnostics = []

    def issue(code, path, message):
        diagnostics.append({"code": code, "path": path, "message": message})

    def mapping(value, path):
        if not isinstance(value, dict):
            issue("type", path, "Expected an object.")
            return {}
        return value

    def sequence(value, path):
        if not isinstance(value, list):
            issue("type", path, "Expected an array.")
            return []
        return value

    def text(value, path):
        if not isinstance(value, str) or not value.strip():
            issue("required", path, "Expected a nonempty string.")
            return ""
        return value

    def fields(obj, allowed, path):
        for key in sorted(obj):
            if key not in allowed:
                issue("unsupported_field", path + "/" + key, "Field is outside the pure schema.")

    def unique(records, path):
        result = {}
        for index, item in enumerate(sequence(records, path)):
            item = mapping(item, path + "/" + str(index))
            name = text(item.get("id"), path + "/" + str(index) + "/id")
            if name in result:
                issue("duplicate", path + "/" + str(index), "Duplicate identity: " + name)
            elif name:
                result[name] = item
        return result

    def templates(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                templates(item, path + "/" + str(key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                templates(item, path + "/" + str(index))
        elif isinstance(value, str) and any(x in value for x in ("{{", "{%", "{#")):
            issue("unresolved_lookup", path, "Supply resolved data; template evaluation is forbidden.")

    snapshot = mapping(snapshot, "")
    templates(snapshot)
    fields(snapshot, {"version", "source_pin", "resolved", "placement", "backends",
                      "services", "channels", "unmodelled_routes"}, "")
    if snapshot.get("version") != 1 or snapshot.get("resolved") is not True:
        issue("resolved_input", "", "Require version 1 and resolved: true.")
    pin = snapshot.get("source_pin")
    if not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{40}", pin):
        issue("source_pin", "/source_pin", "Require an exact 40-character source commit.")
    placement = mapping(snapshot.get("placement"), "/placement")
    fields(placement, {"state", "origin", "observed_at", "sha256", "entries"}, "/placement")
    state = placement.get("state")
    entries = mapping(placement.get("entries", {}), "/placement/entries")
    if state not in ("present", "verified-none"):
        issue("placement_unknown", "/placement/state", "Unknown placement cannot establish baseline parity.")
    for name in ("origin", "observed_at"):
        text(placement.get(name), "/placement/" + name)
    digest = hashlib.sha256(canonical(entries).encode()).hexdigest()
    if placement.get("sha256") != digest:
        issue("placement_hash", "/placement/sha256", "Placement entries do not match their content hash.")
    if state == "verified-none" and entries:
        issue("placement_conflict", "/placement", "verified-none requires empty entries.")
    for key, value in entries.items():
        text(value, "/placement/entries/" + key)
    channels = unique(snapshot.get("channels"), "/channels")
    for name, channel in channels.items():
        path = "/channels/" + name
        fields(channel, {"id", "context", "classification", "owner", "qualified"}, path)
        if channel.get("context") not in CONTEXTS:
            issue("context", path, "Unknown context.")
        if channel.get("classification") != "transport":
            issue("source_classification", path, "Headers or source CIDR cannot select ingress context.")
        text(channel.get("owner"), path + "/owner")
        if channel.get("qualified") is not True:
            issue("channel_unqualified", path, "Channel requires a separate transport qualification receipt.")

    # Candidates are grouped before selecting precedence; no dictionary overwrite.
    candidates = {}
    for index, backend in enumerate(sequence(snapshot.get("backends"), "/backends")):
        path = "/backends/" + str(index)
        backend = mapping(backend, path)
        fields(backend, {"id", "kind", "host", "endpoints", "health", "strategy",
                         "owner", "provenance"}, path)
        name = text(backend.get("id"), path + "/id")
        candidates.setdefault(name, []).append(copy.deepcopy(backend))
    backends = {}
    for name, group in sorted(candidates.items()):
        path = "/backends/" + name
        swarm = [x for x in group if x.get("kind") == "swarm"]
        selected = swarm if swarm else group
        if len(selected) != 1 or (swarm and any(x.get("kind") not in ("compose", "swarm") for x in group)):
            issue("backend_ambiguity", path, "Multiple candidates lack an unambiguous Swarm-over-Compose relationship.")
        backend = selected[0]
        if backend.get("kind") not in ("compose", "swarm", "native", "static"):
            issue("backend_kind", path, "Unsupported backend kind.")
        text(backend.get("owner"), path + "/owner")
        text(backend.get("provenance"), path + "/provenance")
        strategy = backend.get("strategy")
        if strategy not in ("load_balance", "failover"):
            issue("backend_strategy", path, "Specify load_balance or ordered failover.")
        endpoints = sequence(backend.get("endpoints"), path + "/endpoints")
        if not endpoints:
            issue("backend_empty", path, "At least one explicit endpoint is required.")
        seen_endpoints = set()
        for i, endpoint in enumerate(endpoints):
            ep_path = path + "/endpoints/" + str(i)
            endpoint = mapping(endpoint, ep_path)
            fields(endpoint, {"host", "url"}, ep_path)
            host = text(endpoint.get("host"), ep_path + "/host")
            url = text(endpoint.get("url"), ep_path + "/url")
            if not re.fullmatch(r"https?://[^/\s@]+(?:/[^\s]*)?", url):
                issue("endpoint", ep_path, "Require explicit HTTP(S) URL without userinfo.")
            if (host, url) in seen_endpoints:
                issue("duplicate_endpoint", ep_path, "Duplicate endpoint.")
            seen_endpoints.add((host, url))
        if backend.get("kind") == "compose" and name in entries and state == "present":
            host = entries[name]
            endpoints = [x for x in endpoints if isinstance(x, dict) and x.get("host") == host]
            if not endpoints:
                issue("placement_endpoint", path, "Explicit placement has no resolved endpoint.")
            backend["host"] = host
        elif backend.get("kind") == "compose" and backend.get("host"):
            endpoints = [x for x in endpoints if isinstance(x, dict) and x.get("host") == backend["host"]]
            if not endpoints:
                issue("placement_endpoint", path, "Selected host has no resolved endpoint.")
        if backend.get("kind") == "swarm" and strategy == "load_balance":
            endpoints = sorted(endpoints, key=canonical)
        backend["endpoints"] = endpoints
        backend["retained_provenance"] = sorted(
            [x.get("provenance", "") for x in group if isinstance(x.get("provenance"), str)])
        backends[name] = backend

    services = unique(snapshot.get("services"), "/services")
    normalized = {}
    router_ids = set()
    matches = set()
    for name, service in sorted(services.items()):
        path = "/services/" + name
        fields(service, {"id", "hostname", "owner", "backend_ref", "application_auth",
                         "contexts", "routes", "dependencies", "publication",
                         "admin", "lan_segments", "nebula_peers", "controls",
                         "dns_ref", "hold_ref", "custody_ref"}, path)
        for key in ("hostname", "owner", "backend_ref", "dns_ref", "hold_ref", "custody_ref"):
            text(service.get(key), path + "/" + key)
        hostname = service.get("hostname", "")
        if not isinstance(hostname, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", hostname):
            issue("hostname", path, "Supply a canonical lowercase hostname.")
        if service.get("backend_ref") not in backends:
            issue("backend_missing", path, "Backend reference is unresolved.")
        auth = mapping(service.get("application_auth"), path + "/application_auth")
        fields(auth, {"mode", "owner", "coverage", "issuer_ref", "callback_ref", "secret_ref"}, path + "/application_auth")
        mode = auth.get("mode")
        if mode not in ("none", "native_login", "native_oidc"):
            issue("auth_unknown", path, "Declare application auth explicitly.")
        if mode != "none":
            text(auth.get("owner"), path + "/application_auth/owner")
            text(auth.get("coverage"), path + "/application_auth/coverage")
        if mode == "native_oidc":
            for key in ("issuer_ref", "callback_ref", "secret_ref"):
                text(auth.get(key), path + "/application_auth/" + key)
        contexts = mapping(service.get("contexts"), path + "/contexts")
        fields(contexts, set(CONTEXTS), path + "/contexts")
        for context in CONTEXTS:
            cp = path + "/contexts/" + context
            policy = mapping(contexts.get(context), cp)
            fields(policy, {"allowed", "channel", "proxy_auth", "extra_gate_decision",
                            "provider_ref", "callback_ref", "groups"}, cp)
            if not isinstance(policy.get("allowed"), bool):
                issue("context_unknown", cp, "Explicit allowed/denied policy required.")
            if policy.get("proxy_auth") not in ("none", "forward_auth"):
                issue("proxy_auth", cp, "Declare proxy auth explicitly.")
            channel = channels.get(policy.get("channel"))
            if policy.get("allowed") is True and (not channel or channel.get("context") != context):
                issue("channel_missing", cp, "Context needs a matching explicit transport channel.")
            if policy.get("proxy_auth") == "forward_auth":
                for key in ("provider_ref", "callback_ref"):
                    text(policy.get(key), cp + "/" + key)
                if mode in ("native_login", "native_oidc") and not policy.get("extra_gate_decision"):
                    issue("extra_gate", cp, "Native auth has no default second gate.")
            if context == "public" and policy.get("allowed") is True:
                if name in ("whisper", "ollama"):
                    issue("private_service", cp, "Public Whisper/Ollama is forbidden.")
                if mode == "none" and policy.get("proxy_auth") != "forward_auth":
                    issue("public_unprotected", cp, "Public application without native auth requires forward auth.")
        publication = mapping(service.get("publication"), path + "/publication")
        fields(publication, {"approved", "owner", "channel"}, path + "/publication")
        if publication.get("approved") not in (True, False):
            issue("publication_unknown", path, "Publication approval must be explicit.")
        if publication.get("approved") is True:
            public = contexts.get("public", {})
            if not isinstance(public, dict) or public.get("allowed") is not True:
                issue("publication_context", path, "Publication cannot bypass denied public context.")
            channel = channels.get(publication.get("channel"), {})
            if channel.get("context") != "public":
                issue("publication_channel", path, "Public forwarders require the public transport channel.")
            text(publication.get("owner"), path + "/publication/owner")
            if name in ("whisper", "ollama"):
                issue("private_service", path, "Private service cannot be published.")
        if service.get("admin") is True and publication.get("approved") is not False:
            issue("admin_publication", path, "Administration publication requires a separate future supported decision.")

        dependencies = sequence(service.get("dependencies"), path + "/dependencies")
        for dependency in dependencies:
            if not isinstance(dependency, str) or dependency not in services:
                issue("dependency_missing", path, "Unresolved service dependency.")
        if name == "whisper":
            segments = service.get("lan_segments")
            if not isinstance(segments, list) or any(not isinstance(x, str) for x in segments) or set(segments) != TRUSTED_LAN:
                issue("trusted_lan", path, "Whisper requires the four explicit Trusted LAN IPv4 segments.")
            if service.get("nebula_peers") != "all-valid-direct":
                issue("nebula_entitlement", path, "All valid direct peers remain entitled, including OCI and agents.")
            controls = mapping(service.get("controls"), path + "/controls")
            fields(controls, {"ordinary_denied_paths", "owner_ref", "backend_policy_ref"}, path + "/controls")
            if "/load" not in sequence(controls.get("ordinary_denied_paths"), path + "/controls/ordinary_denied_paths"):
                issue("control_path", path, "Ordinary transcription must exclude /load.")
            for key in ("owner_ref", "backend_policy_ref"):
                text(controls.get(key), path + "/controls/" + key)
        routes = sequence(service.get("routes"), path + "/routes")
        if not routes:
            issue("route_missing", path, "Explicit supported route records required.")
        for i, route in enumerate(routes):
            rp = path + "/routes/" + str(i)
            route = mapping(route, rp)
            fields(route, {"id", "context", "entrypoints", "rule", "priority", "middlewares",
                           "callback", "tls", "provenance"}, rp)
            rid = text(route.get("id"), rp + "/id")
            if rid in router_ids:
                issue("router_duplicate", rp, "Router identity already exists.")
            router_ids.add(rid)
            context = route.get("context")
            if context not in CONTEXTS:
                issue("context", rp, "Route needs a supported context.")
            elif not isinstance(contexts.get(context), dict) or contexts[context].get("allowed") is not True:
                issue("route_denied_context", rp, "Route cannot exist in a denied context.")
            ep = sequence(route.get("entrypoints"), rp + "/entrypoints")
            if not ep or any(not isinstance(x, str) or not x for x in ep):
                issue("entrypoints", rp, "Explicit entrypoints required.")
            rule = text(route.get("rule"), rp + "/rule")
            if not isinstance(route.get("priority"), int) or isinstance(route.get("priority"), bool):
                issue("priority", rp, "Explicit integer priority required.")
            signature = canonical([hostname, context, sorted(ep, key=canonical), rule, route.get("priority")])
            if signature in matches:
                issue("router_overlap", rp, "Duplicate match/priority/context needs an explicitly modelled relationship.")
            matches.add(signature)
            middlewares = sequence(route.get("middlewares"), rp + "/middlewares")
            if any(not isinstance(x, str) for x in middlewares):
                issue("middleware", rp, "Middleware references must be strings in behavior order.")
            callback = mapping(route.get("callback"), rp + "/callback")
            fields(callback, {"path", "priority", "proxy_auth", "backend_ref"}, rp + "/callback")
            if callback:
                if callback.get("proxy_auth") != "none":
                    issue("callback_recursion", rp, "Callback cannot require its own proxy gate.")
                if callback.get("backend_ref") not in backends:
                    issue("callback_backend", rp, "Callback backend must resolve.")
                if not isinstance(callback.get("priority"), int) or not isinstance(callback.get("path"), str):
                    issue("callback", rp, "Callback needs explicit path and priority.")
            mapping(route.get("tls"), rp + "/tls")
            text(route.get("provenance"), rp + "/provenance")
        normalized[name] = copy.deepcopy(service)
        normalized[name]["routes"] = sorted(routes, key=canonical)

    # Dependency cycles cannot establish move or baseline readiness.
    visited, visiting = set(), set()

    def visit(name):
        if name in visiting:
            issue("dependency_cycle", "/services/" + name, "Dependency cycle requires separate resolution.")
            return
        if name in visited or name not in services:
            return
        visiting.add(name)
        deps = services[name].get("dependencies", [])
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, str):
                    visit(dep)
        visiting.remove(name)
        visited.add(name)

    for name in sorted(services):
        visit(name)
    unmodelled = sequence(snapshot.get("unmodelled_routes"), "/unmodelled_routes")
    if unmodelled:
        issue("coverage_incomplete", "/unmodelled_routes", "Special/static routes remain visible; whole-ingress equivalence is blocked.")
    diagnostics.sort(key=lambda x: (x["path"], x["code"], x["message"]))
    return {"schema_version": 1, "source_pin": pin, "placement": copy.deepcopy(placement),
            "backends": backends, "services": normalized, "channels": channels,
            "unmodelled_routes": copy.deepcopy(unmodelled), "diagnostics": diagnostics,
            "planning_ready": not diagnostics, "deployment_ready": False}


def inspect(snapshot):
    """Reject malformed JSON shapes without exposing a partial valid model."""
    try:
        return _inspect(snapshot)
    except (TypeError, ValueError, KeyError, RecursionError):
        return {"schema_version": 1, "source_pin": None, "placement": {},
                "backends": {}, "services": {}, "channels": {},
                "unmodelled_routes": [], "diagnostics": [{
                    "code": "input_shape", "path": "",
                    "message": "Malformed input shape; see the version-1 schema."}],
                "planning_ready": False, "deployment_ready": False}


def plan(snapshot, baseline=None, move_set=None, available=None):
    """Return normalized inspection and conservative modeled semantic changes.

    Move checks use the same dependency graph as inspection. 'available' is an
    explicit set of service IDs available at the destination/retained phase;
    it is never inferred from reachability.
    """
    result = inspect(snapshot)
    result["changes"] = []
    result["model_equivalent"] = None
    if baseline is not None:
        previous = inspect(baseline)
        semantic = ("backends", "services", "channels", "unmodelled_routes")
        result["changes"] = _changes({x: previous[x] for x in semantic},
                                     {x: result[x] for x in semantic})
        result["baseline_diagnostics"] = previous["diagnostics"]
        result["model_equivalent"] = not (result["changes"] or result["diagnostics"] or previous["diagnostics"])
    if move_set is not None:
        if not isinstance(move_set, list) or not all(isinstance(x, str) for x in move_set):
            result["diagnostics"].append({"code": "move_set", "path": "/move_set", "message": "Expected service ID array."})
        elif not isinstance(available, list) or not all(isinstance(x, str) for x in available):
            result["diagnostics"].append({"code": "move_available", "path": "/available", "message": "Explicit available service ID array required."})
        else:
            selected, served = set(move_set), set(available) | set(move_set)
            for name in sorted(selected | set(available)):
                if name not in result["services"]:
                    result["diagnostics"].append({"code": "move_unknown", "path": "/move_set/" + name, "message": "Unknown service."})
            for name in sorted(selected):
                service = result["services"].get(name, {})
                dependencies = service.get("dependencies", [])
                if isinstance(dependencies, list):
                    for dep in dependencies:
                        if isinstance(dep, str) and dep not in served:
                            result["diagnostics"].append({"code": "move_dependency", "path": "/move_set/" + name, "message": "Unserved dependency: " + dep})
    result["diagnostics"].sort(key=lambda x: (x["path"], x["code"], x["message"]))
    result["planning_ready"] = not result["diagnostics"]
    if result["diagnostics"] and result["model_equivalent"] is not None:
        result["model_equivalent"] = False
    return result


class FilterModule:
    def filters(self):
        return {"service_access_plan": plan}
