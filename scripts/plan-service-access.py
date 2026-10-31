#!/usr/bin/env python3
"""Inspect resolved JSON snapshots; never evaluate inventory or apply changes."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "filter_plugins"))
from service_access import plan


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source, object_pairs_hook=unique_object)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", help="Explicit resolved JSON snapshot")
    parser.add_argument("--baseline", help="Explicit resolved JSON baseline")
    parser.add_argument("--move-set", nargs="+", help="Service IDs selected for a hypothetical move")
    parser.add_argument("--available", nargs="*", help="Explicit available service IDs at that phase")
    args = parser.parse_args()
    try:
        result = plan(load(args.snapshot), load(args.baseline) if args.baseline else None,
                      args.move_set, args.available)
    except (OSError, ValueError, RecursionError) as exc:
        parser.exit(2, "Input rejected: " + str(exc) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["planning_ready"] else 2


if __name__ == "__main__":
    sys.exit(main())
