#!/usr/bin/env python3
"""Run with the deployment Ansible venv; these tests never invoke Docker."""
import unittest
import base64
import json
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
from ansible._internal._datatag._tags import TrustedAsTemplate

ROLE = Path(__file__).parents[1]
loader = DataLoader()
tasks = loader.load_from_file(str(ROLE / "tasks/main.yml"))

def task(name):
    return next(value for value in tasks if value["name"] == name)

def render(expression, variables):
    return Templar(loader=loader, variables=variables).template(
        TrustedAsTemplate().tag(expression))

class RollbackTest(unittest.TestCase):
    def test_rollback_does_not_revert_new_grafana_or_loki_or_restore_driver_dependency(self):
        original = {
            "services": {
                "influxdb": {"image": "original-influx@sha256:abc", "logging": {"driver": "loki"},
                             "volumes": [{"source": "/original-cold", "target": "/var/lib/influxdb2"}]},
                "grafana": {"image": "older-grafana", "environment": {"OLD_OPTION": "true"}},
                "loki": {"image": "older-loki"},
            },
            "secrets": {"influx_pass": {"name": "original-password"},
                        "influx_token": {"name": "original-token"},
                        "grafana_password": {"name": "older-grafana-password"}},
        }
        expression = task("Prepare a rollback that cannot revert Loki or Grafana")["ansible.builtin.set_fact"]["influx_rollback_rendering"]
        rollback = loader.load(render(expression, {"influx_original_doc": original}))
        self.assertEqual(set(rollback["services"]), {"influxdb"})
        self.assertEqual(rollback["services"]["influxdb"]["image"], "original-influx@sha256:abc")
        self.assertEqual(rollback["services"]["influxdb"]["volumes"][0]["source"], "/original-cold")
        self.assertEqual(rollback["services"]["influxdb"]["logging"]["driver"], "local")
        self.assertEqual(set(rollback["secrets"]), {"influx_pass", "influx_token"})
        self.assertEqual(rollback["networks"]["default"], {"external": True, "name": "logging_default"})

    def test_accepted_failed_and_interrupted_runs_cannot_overwrite_retained_state(self):
        marker_paths = task("Inspect completed or interrupted transition markers")["loop"]
        self.assertEqual(set(marker_paths), {
            "logging/.influx-isolated.json", "legacy-influx/.transition-failed.json",
            "legacy-influx/.transition-attempt.json", "legacy-influx/.transition-accepted.json"})
        condition = task("Refuse completed or interrupted transition retries")["ansible.builtin.assert"]["that"]
        self.assertFalse(render("{{ " + condition + " }}", {"item": {"stat": {"exists": True}}}))
        self.assertTrue(render("{{ " + condition + " }}", {"item": {"stat": {"exists": False}}}))

    def test_scaled_to_zero_preexisting_service_is_still_an_unsafe_destination(self):
        condition = task("Require no preexisting legacy service before copying")["ansible.builtin.assert"]["that"]
        self.assertFalse(render("{{ " + condition + " }}", {"influx_existing_legacy_service": {"stdout": "service-reserving-8087\n"}}))
        self.assertTrue(render("{{ " + condition + " }}", {"influx_existing_legacy_service": {"stdout": ""}}))

    def test_ordinary_convergence_requires_true_acceptance_and_never_bypasses_failed_state(self):
        prepare = loader.load_from_file(str(ROLE.parents[1] / "stacks/legacy-influx/swarm-prepare.yml"))
        gate = next(t for t in prepare if t["name"] == "Refuse ordinary convergence before acceptance or after failed cutover")
        conditions = gate["ansible.builtin.assert"]["that"]
        for accepted, active, failed, expected in [
            (False, False, False, False), (True, False, False, True),
            (False, True, False, True), (True, True, True, False),
        ]:
            variables = {
                "legacy_influx_transition_active": active,
                "legacy_influx_barriers": {"results": [{"stat": {}}, {"stat": {"exists": failed}}]},
                "legacy_influx_acceptance": {"content": base64.b64encode(json.dumps({"accepted": accepted}).encode()).decode()},
            }
            allowed = all(render("{{ " + c + " }}", variables) for c in conditions)
            self.assertEqual(allowed, expected)

if __name__ == "__main__":
    unittest.main()
