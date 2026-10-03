#!/usr/bin/env python3
"""Refuse a Swarm stack file that breaks the phase 5 conversion rules.

`docker stack deploy` accepts a file it cannot honour and runs the service
anyway: `devices:` is dropped with a one-line warning, so Emby would lose its
GPU and keep running; `build:` is ignored; an address in a published port is
ignored. A stack without placement constraints can land anywhere, and one
without limits can starve the worker it lands on. This lint turns each of
those into an error before anything is deployed.

Errors (exit 1):
  - a service without deploy.placement.constraints
  - a service without deploy.resources.limits.memory
  - devices:, a bind mount of anything under /dev, privileged:, build:,
    network_mode:
  - a published port bound to a host address
  - update_config.order: start-first on a service with a volume or bind
    mount, which would run two writers on the same data

Warnings (printed, exit 0): keys Swarm ignores (container_name, depends_on,
restart, shm_size, mem_limit, cpus), and more than one replica of a service
with a mount.

    scripts/stack-lint.py stacks/*/stack.yml
"""

import re
import sys

import yaml

IGNORED = ("container_name", "depends_on", "restart", "shm_size", "mem_limit",
           "memswap_limit", "cpus", "cpu_shares", "mem_reservation", "links")
FORBIDDEN = {
    "devices": "Swarm services cannot map devices; GPU stacks stay Compose (compose-pinned)",
    "privileged": "Swarm ignores privileged",
    "build": "stack deploy ignores build; push the image to the registry and name it",
    "network_mode": "Swarm does not support network_mode",
}
# address:published:target, as in "192.168.70.223:3002:8080" or
# "${HOST_IP}:3002:8080".
ADDRESS_PORT = re.compile(r"^(\$\{[^}]+\}|\[?[0-9a-fA-F.:]+\]?):\d+(-\d+)?:\d+")


def mounts(service):
    """Yield (type, source) for every volume entry of a service."""
    for entry in service.get("volumes") or []:
        if isinstance(entry, str):
            source = entry.split(":", 1)[0] if ":" in entry else ""
            kind = "bind" if source.startswith(("/", ".", "$", "~")) else "volume"
            yield kind, source
        elif isinstance(entry, dict):
            yield entry.get("type", "volume"), str(entry.get("source", ""))


def lint(path):
    errors, warnings = [], []
    with open(path) as handle:
        try:
            document = yaml.safe_load(handle) or {}
        except yaml.YAMLError as error:
            return [f"{path}: not valid YAML: {error}"], []
    services = document.get("services") or {}
    if not services:
        errors.append(f"{path}: no services")
    for name, service in services.items():
        where = f"{path}: {name}"
        service = service or {}
        deploy = service.get("deploy") or {}
        if not (deploy.get("placement") or {}).get("constraints"):
            errors.append(f"{where}: no deploy.placement.constraints")
        limits = (deploy.get("resources") or {}).get("limits") or {}
        if not limits.get("memory"):
            errors.append(f"{where}: no deploy.resources.limits.memory")
        for key, reason in FORBIDDEN.items():
            if key in service:
                errors.append(f"{where}: {key}: {reason}")
        has_mount = False
        for kind, source in mounts(service):
            has_mount = True
            if kind == "bind" and source.startswith("/dev"):
                errors.append(f"{where}: bind mount of {source}: device access does not"
                              " work in a Swarm service; keep this stack compose-pinned")
        for port in service.get("ports") or []:
            if isinstance(port, str) and ADDRESS_PORT.match(port):
                errors.append(f"{where}: port {port} is bound to an address; Swarm"
                              " publishes on every interface of every node")
            if isinstance(port, dict) and port.get("host_ip"):
                errors.append(f"{where}: port host_ip {port['host_ip']} is ignored by Swarm")
        order = (deploy.get("update_config") or {}).get("order")
        rollback = (deploy.get("rollback_config") or {}).get("order")
        if has_mount and "start-first" in (order, rollback):
            errors.append(f"{where}: start-first with a mount runs two writers on one"
                          " volume; use stop-first")
        if has_mount and int(deploy.get("replicas", 1) or 1) > 1:
            warnings.append(f"{where}: {deploy.get('replicas')} replicas share a mount")
        for key in IGNORED:
            if key in service:
                warnings.append(f"{where}: {key} is ignored by Swarm")
    return errors, warnings


def main(paths):
    if not paths:
        print("usage: stack-lint.py STACK_FILE...", file=sys.stderr)
        return 2
    failed = False
    for path in paths:
        errors, warnings = lint(path)
        for line in warnings:
            print(f"warning: {line}")
        for line in errors:
            print(f"error: {line}")
        failed = failed or bool(errors)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
