#!/usr/bin/env python3
"""Compare the canonical fleet adapter's additive registry with fixed infra data."""
import argparse
import json
from pathlib import Path
import yaml

parser = argparse.ArgumentParser()
parser.add_argument("render")
parser.add_argument("--pc-reader", action="store_true")
parser.add_argument("--pc-quota", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = yaml.safe_load((root / "vars/akasha-enrollment-registry.yml").read_text())
expected = data["akasha_enrollment_registry_additions"]
if args.pc_reader:
    expected += data["akasha_enrollment_pc_reader"]
if args.pc_quota:
    expected += data["akasha_enrollment_pc_quota"]
actual = json.loads(Path(args.render).read_text())["registry"]
order = lambda row: (row["issuer"], row["source"])
if sorted(actual, key=order) != sorted(expected, key=order):
    raise SystemExit("enrollment registry differs from the reviewed fleet render")
print("fixed infra registrations equal canonical fleet render")
