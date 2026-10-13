#!/usr/bin/env python3
"""Resolve declared physical drives to stable IDs using read-only identification."""
import glob
import json
import os
import re
import stat
import subprocess
import sys


def identify(path, smartctl):
    result = subprocess.run(
        [smartctl, "--info", "--json", "--nocheck=standby", path],
        capture_output=True, text=True, timeout=20, check=False,
    )
    data = json.loads(result.stdout)
    if result.returncode & 3:
        raise ValueError("smartctl could not identify " + path)
    return str(data.get("serial_number", "")).strip()


def namespace_id(path):
    real = os.path.basename(os.path.realpath(path))
    with open("/sys/class/block/" + real + "/nsid", encoding="utf-8") as handle:
        return int(handle.read().strip())


def discover(drives, smartctl, paths=None, info=None, nsid=None):
    paths = paths if paths is not None else sorted(glob.glob("/dev/disk/by-id/*"))
    info = info if info is not None else lambda path: identify(path, smartctl)
    nsid = nsid if nsid is not None else namespace_id
    output = []
    seen = set()
    for drive in drives:
        serial = str(drive.get("serial", "")).strip()
        if not serial or serial in seen:
            raise ValueError("Drive serials must be nonempty and unique")
        seen.add(serial)
        candidates = []
        for path in paths:
            if re.search(r"-part[0-9]+$", path) or serial not in os.path.basename(path):
                continue
            if not stat.S_ISBLK(os.stat(path).st_mode):
                continue
            if drive.get("namespace") is not None and nsid(path) != int(drive["namespace"]):
                continue
            if info(path) == serial:
                candidates.append(path)
        physical = {os.path.realpath(path) for path in candidates}
        if len(physical) != 1:
            raise ValueError("Expected one physical drive for declared serial " + serial)
        # Prefer descriptive by-id aliases over namespace suffix aliases.
        chosen = sorted(candidates, key=lambda path: (len(path), path))[0]
        output.append({"serial": serial, "device": chosen, "model": drive.get("model", "")})
    return output


def main():
    try:
        print(json.dumps(discover(json.loads(sys.argv[1]), sys.argv[2])))
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
