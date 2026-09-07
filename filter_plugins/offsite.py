"""Render an offsite target as the colon-separated form the script parses."""


def to_target(target):
    """{'name': 'oci', 'host': 'oci', 'path': 'x', 'keep': 7} -> 'oci:oci:x:7'."""
    return "{name}:{host}:{path}:{keep}".format(**target)


class FilterModule:
    def filters(self):
        return {"to_target": to_target}
