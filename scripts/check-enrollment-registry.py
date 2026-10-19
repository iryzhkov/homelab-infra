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
parser.add_argument("--laptop-quota", action="store_true")
parser.add_argument("--pc-quota-reader", action="store_true")
parser.add_argument("--mission-control", action="store_true")
parser.add_argument("--pc-mission-control", action="store_true")
parser.add_argument("--agent-b-mission-control", action="store_true")
parser.add_argument("--laptop-mission-control", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
data = yaml.safe_load((root / "vars/akasha-enrollment-registry.yml").read_text())
expected = data["akasha_enrollment_registry_additions"]
if args.pc_reader:
    expected += data["akasha_enrollment_pc_reader"]
if args.pc_quota:
    expected += data["akasha_enrollment_pc_quota"]
if args.laptop_quota:
    expected += data["akasha_enrollment_quota_laptop_publisher"]
if args.pc_quota_reader:
    expected += data["akasha_enrollment_quota_pc_reader"]
if args.mission_control:
    proposal = yaml.safe_load((root / "vars/mission-control-proposal.yml").read_text())["mission_control_registry_proposal"]
    if not isinstance(proposal, list) or len(proposal) != 2:
        raise SystemExit("Mission Control proposal requires an exact reviewed publisher/reader packet and selected UID")
    expected += proposal
if args.pc_mission_control:
    if args.mission_control:
        raise SystemExit('select only one Mission Control proposal')
    expected = [row for row in expected if row['profile'] != 'mission-control-reader'] + data['akasha_enrollment_pc_mission_control']
if args.agent_b_mission_control or args.laptop_mission_control:
    if args.mission_control:
        raise SystemExit('select only one Mission Control proposal')
    reader = next(row for row in expected if row['profile'] == 'mission-control-reader')
    sources = ['agent-a--mission-control'] + (['omarchy-pc--mission-control'] if args.pc_mission_control else [])
    for enabled, name in [(args.agent_b_mission_control, 'agent_b'), (args.laptop_mission_control, 'laptop')]:
        if enabled:
            rows = data['akasha_enrollment_' + name + '_mission_control']
            expected += rows
            sources += [rows[0]['source_id']]
    expected = [row for row in expected if row['profile'] != 'mission-control-reader'] + [
        reader | {'mission_control_read_sources': sources}]
actual = json.loads(Path(args.render).read_text())["registry"]
order = lambda row: (row["issuer"], row["source"])
if sorted(actual, key=order) != sorted(expected, key=order):
    raise SystemExit("enrollment registry differs from the reviewed fleet render")
print("fixed infra registrations equal canonical fleet render")
