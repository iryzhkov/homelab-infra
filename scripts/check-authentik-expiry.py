#!/usr/bin/env python3
"""Check public vars declarations only; never query Authentik or read credentials."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sys

import yaml

REFERENCE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")


def resolve(value, variables, seen=()):
    if isinstance(value, str):
        match = REFERENCE.fullmatch(value)
        if match:
            name = match[1]
            if name in seen or name not in variables:
                raise ValueError("unresolved or cyclic expiry reference")
            return resolve(variables[name], variables, (*seen, name))
    return value


def declarations(document, location=""):
    """Yield every expires key, including optional and repeated declarations."""
    if isinstance(document, dict):
        for key, value in document.items():
            path = f"{location}.{key}" if location else str(key)
            if key == "expires":
                yield path, value
            else:
                yield from declarations(value, path)
    elif isinstance(document, list):
        for index, value in enumerate(document):
            yield from declarations(value, f"{location}[{index}]")


def check_file(path, now, warning_days=22):
    # Only paths in public vars directories are supplied by main.
    document = yaml.safe_load(path.read_text()) or {}
    if not isinstance(document, dict):
        raise ValueError("vars must be a mapping")
    results = []
    for location, raw in declarations(document):
        value = resolve(raw, document)
        if value in ("", None):
            results.append((False, f"{path}:{location}: unspecified (operator input required; not monitored)"))
            continue
        try:
            expiry = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                raise ValueError("timezone required")
        except (ValueError, TypeError, AttributeError):
            # Do not echo input values: only validated public timestamps reach logs.
            raise ValueError(f"{path}:{location}: invalid expiry timestamp") from None
        due = expiry <= now + timedelta(days=warning_days)
        status = "DUE" if due else "OK"
        results.append((due, f"{path}:{location}: {status} {expiry.isoformat()}"))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    now = datetime.now(timezone.utc)
    failed = False
    count = 0
    # These are public source declarations, not secrets/, receipts or SOPS inputs.
    for directory in ("playbooks/vars", "vars"):
        for path in sorted((args.root / directory).rglob("*.y*ml")):
            if "sops" in path.name.lower() or "secret" in path.name.lower():
                continue
            try:
                results = check_file(path, now)
            except (ValueError, yaml.YAMLError):
                print(f"ERROR {path.relative_to(args.root)}: invalid expiry declaration or vars YAML")
                failed = True
                continue
            for due, message in results:
                print(message.replace(str(args.root) + "/", ""))
                count += 1
                failed |= due
    if not count:
        print("ERROR no expiry declarations found")
        return 1
    print(f"Checked {count} expiry declarations; alert window: 22 days (daily cadence leaves at least 21 days).")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
