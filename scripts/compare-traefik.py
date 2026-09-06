#!/usr/bin/env python3
"""Compare two sets of Traefik dynamic configuration by what they route.

Router and service names are free to change; what must not change is the
mapping from a hostname to the backends behind it. This reduces each side to
that mapping and prints the difference.

    scripts/compare-traefik.py <old-dir> <new-dir>

Both arguments are directories of dynamic YAML files. The usual invocation
compares the captured snapshot of the hand-written configuration against a
rendered copy of the generated one.
"""

import re
import sys
from pathlib import Path

import yaml


def load(directory):
    """Return {rule_key: (backends, checks, middlewares, priority)} for a directory.

    The health check is part of the comparison because leaving it out is what
    let a working configuration be replaced by a broken one: the generated file
    probed every service on `/`, Traefik counts the 401 that Sonarr answers
    there as unhealthy, and its router started returning 503 while this script
    reported the change as identical.
    """
    routers, services = {}, {}
    for path in sorted(Path(directory).glob("*.yml")):
        doc = yaml.safe_load(path.read_text()) or {}
        http = doc.get("http") or {}
        routers.update(http.get("routers") or {})
        services.update(http.get("services") or {})

    def backends(name, seen=()):
        """Resolve a service to its list of URLs, following failover chains."""
        if name in seen:
            return ["<cycle>"]
        service = services.get(name)
        if service is None:
            return [f"<external:{name}>"]
        if "failover" in service:
            failover = service["failover"]
            return (
                backends(failover["service"], seen + (name,))
                + backends(failover["fallback"], seen + (name,))
            )
        lb = service.get("loadBalancer", {})
        return sorted(s.get("url", "") for s in lb.get("servers", []))

    def checks(name, seen=()):
        """Resolve a service to how it is health checked, following failovers."""
        if name in seen:
            return ["<cycle>"]
        service = services.get(name)
        if service is None:
            return []
        if "failover" in service:
            failover = service["failover"]
            return (
                checks(failover["service"], seen + (name,))
                + checks(failover["fallback"], seen + (name,))
            )
        check = (service.get("loadBalancer") or {}).get("healthCheck")
        return [check.get("path", "/") if check else "none"]

    routed = {}
    for name, router in routers.items():
        rule = re.sub(r"\s+", "", router.get("rule", ""))
        routed[rule] = (
            backends(router.get("service", "")),
            checks(router.get("service", "")),
            sorted(router.get("middlewares", [])),
            router.get("priority"),
        )
    return routed


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    old, new = load(sys.argv[1]), load(sys.argv[2])

    problems = 0
    for rule in sorted(set(old) | set(new)):
        if rule not in new:
            print(f"REMOVED  {rule}\n         was {old[rule]}")
            problems += 1
        elif rule not in old:
            print(f"ADDED    {rule}\n         now {new[rule]}")
            problems += 1
        elif old[rule] != new[rule]:
            print(f"CHANGED  {rule}\n         was {old[rule]}\n         now {new[rule]}")
            problems += 1

    print(f"\n{len(old)} rules before, {len(new)} after, {problems} differing")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
