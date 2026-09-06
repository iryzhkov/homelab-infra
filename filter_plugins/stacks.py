"""Filters for finding where a stack runs.

A service that talks to another service should not carry its address as a
literal: cv-site pointed at Immich on homelab for a week after Immich moved to
normandy, and nothing complained. These filters answer "which host runs stack
X" from the inventory, so a cross-service URL follows the stack it names.

    OLLAMA_URL: "http://{{ hostvars | stack_host_ip(groups, 'ollama') }}:11434"
"""

from ansible.errors import AnsibleFilterError


def stack_host(hostvars, groups, stack_name, group="docker_hosts"):
    """Return the inventory name of the host whose `stacks` list names this stack."""
    for host in groups.get(group, []):
        for stack in hostvars[host].get("stacks") or []:
            if stack.get("name") == stack_name:
                return host
    raise AnsibleFilterError(
        f"No host in group '{group}' declares a stack named '{stack_name}'. "
        "Either the stack is not adopted yet, or the name is misspelled."
    )


def stack_host_ip(hostvars, groups, stack_name, group="docker_hosts"):
    """Return the address of the host running this stack."""
    host = stack_host(hostvars, groups, stack_name, group)
    return hostvars[host]["ansible_host"]


class FilterModule:
    def filters(self):
        return {
            "stack_host": stack_host,
            "stack_host_ip": stack_host_ip,
        }
