#!/usr/bin/env python3
"""Hash nonempty Flux CSV data without printing measurements or credentials."""
import csv
import hashlib
import io
import json
import subprocess
import sys

def canonical(csv_text):
    header = None
    records = []
    for row in csv.reader(io.StringIO(csv_text)):
        if not row or row[0].startswith("#"):
            continue
        if "_value" in row and "_time" in row:
            header = row
            continue
        if header and len(row) == len(header):
            record = {k: v for k, v in zip(header, row) if k not in ("", "result", "table")}
            if record.get("_time") and record.get("_value") is not None:
                records.append(record)
    if not records:
        raise ValueError("Flux query returned no data records")
    encoded = json.dumps(sorted(records, key=lambda r: json.dumps(r, sort_keys=True)),
                         sort_keys=True, separators=(",", ":")).encode()
    return {"rows": len(records), "sha256": hashlib.sha256(encoded).hexdigest()}

def main():
    request = json.load(sys.stdin)
    # Credentials stay in container process environment; never Docker argv.
    script = 'export INFLUX_TOKEN="$(cat /run/secrets/influx_token)"; exec influx query --raw --org ryzhkov "$1"'
    if request.get("metadata"):
        identity = {}
        for resource in ("org", "bucket", "auth"):
            identity_script = 'export INFLUX_TOKEN="$(cat /run/secrets/influx_token)"; exec influx "$1" list --json'
            result = subprocess.run(["docker", "exec", request["container"], "sh", "-c",
                                     identity_script, "identity-proof", resource],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise RuntimeError("Identity query failed; raw response suppressed")
            values = json.loads(result.stdout)
            # Tokens and links are unnecessary to establish unchanged identities,
            # permissions, statuses and bucket retention policies.
            keep = ("id", "name", "orgID", "userID", "status", "permissions", "retentionRules")
            clean = [{k: v for k, v in value.items() if k in keep} for value in values]
            identity[resource] = sorted(clean, key=lambda value: json.dumps(value, sort_keys=True))
        encoded = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        print(json.dumps({"sha256": hashlib.sha256(encoded).hexdigest(),
                          "counts": {k: len(v) for k, v in identity.items()}}))
        return
    result = subprocess.run(["docker", "exec", request["container"], "sh", "-c",
                             script, "query", request["query"]],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Flux query failed; raw response suppressed")
    print(json.dumps(canonical(result.stdout)))

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Neither raw CSV nor subprocess stderr may include secrets here.
        print(type(exc).__name__ + ": Flux proof failed", file=sys.stderr)
        sys.exit(1)
