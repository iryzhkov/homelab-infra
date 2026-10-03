"""Filters for finding where a stack runs.

A service that talks to another service should not carry its address as a
literal: cv-site pointed at Immich on homelab for a week after Immich moved to
normandy, and nothing complained. These filters answer "which host runs stack
X" from the inventory, so a cross-service URL follows the stack it names.

    OLLAMA_URL: "http://{{ hostvars | stack_host_ip(groups, 'ollama') }}:11434"

A stack runs either as a Compose project, listed in the `stacks` of one host
in inventory/host_vars/, or as a Docker Swarm stack, listed in one of the
`swarm_stacks_*` variables in inventory/group_vars/swarm/. A Swarm entry wins
over a host_vars entry of the same name: the host_vars entries of converted
stacks stay until the cleanup at the end of phase 5, and the Compose project
they describe no longer runs.

A Swarm stack's published ports answer on every worker through the routing
mesh, so it has no single host. `stack_host` and `stack_host_ip` give the
bootstrap manager for it, which is right for any consumer that can name only
one address, as long as that node is up; `stack_hosts` and `stack_host_ips`
give every worker, for the consumers that can use more than one (Traefik's
load balancer, Gatus).
"""

from ansible.errors import AnsibleFilterError

SWARM_STACKS_PREFIX = "swarm_stacks_"


def _swarm_workers(groups):
    return sorted(groups.get("swarm_workers", []))


def swarm_stacks(hostvars, groups):
    """Return every Swarm stack entry: the union of the swarm_stacks_* lists.

    The lists are group variables of `swarm`, so they are read from a worker's
    variables, whichever host the play runs on (the edge proxy is not a Swarm
    node). Variables are taken in name order, as playbooks/swarm.yml does.
    """
    workers = _swarm_workers(groups)
    if not workers:
        return []
    worker_vars = hostvars[workers[0]]
    entries = []
    for key in sorted(k for k in worker_vars if k.startswith(SWARM_STACKS_PREFIX)):
        entries.extend(worker_vars[key] or [])
    return entries


def swarm_stack_names(hostvars, groups):
    """Return the names of every Swarm stack."""
    return [entry["name"] for entry in swarm_stacks(hostvars, groups)]


def _swarm_bootstrap_manager(hostvars, groups):
    workers = _swarm_workers(groups)
    return hostvars[workers[0]]["swarm_bootstrap_manager"]


def stack_host(hostvars, groups, stack_name, group="docker_hosts"):
    """Return the inventory name of the host that runs this stack.

    For a Swarm stack, the bootstrap manager: through the routing mesh it
    answers on the stack's ports wherever the task runs.
    """
    if stack_name in swarm_stack_names(hostvars, groups):
        return _swarm_bootstrap_manager(hostvars, groups)
    for host in groups.get(group, []):
        for stack in hostvars[host].get("stacks") or []:
            if stack.get("name") == stack_name:
                return host
    raise AnsibleFilterError(
        f"No host in group '{group}' declares a stack named '{stack_name}', "
        "and no swarm_stacks_* entry does. "
        "Either the stack is not adopted yet, or the name is misspelled."
    )


def stack_host_ip(hostvars, groups, stack_name, group="docker_hosts"):
    """Return the address of the host running this stack."""
    host = stack_host(hostvars, groups, stack_name, group)
    return hostvars[host]["ansible_host"]


def stack_hosts(hostvars, groups, stack_name, group="docker_hosts"):
    """Return every host that answers for this stack, in a stable order.

    Every worker for a Swarm stack (sorted by name), the one host otherwise.
    """
    if stack_name in swarm_stack_names(hostvars, groups):
        return _swarm_workers(groups)
    return [stack_host(hostvars, groups, stack_name, group)]


def stack_host_ips(hostvars, groups, stack_name, group="docker_hosts"):
    """Return the addresses of every host that answers for this stack."""
    return [
        hostvars[host]["ansible_host"]
        for host in stack_hosts(hostvars, groups, stack_name, group)
    ]


class FilterModule:
    def filters(self):
        return {
            "stack_host": stack_host,
            "stack_host_ip": stack_host_ip,
            "stack_hosts": stack_hosts,
            "stack_host_ips": stack_host_ips,
            "swarm_stacks": swarm_stacks,
            "swarm_stack_names": swarm_stack_names,
        }
