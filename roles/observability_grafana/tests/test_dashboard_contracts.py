"""Dashboard accounting and selector acceptance contracts."""
import json
import pathlib
import unittest
ROOT = pathlib.Path(__file__).parents[1] / "files/dashboards"
def dashboard(name): return json.loads((ROOT / (name + ".json")).read_text())
def panels(items):
    for item in items:
        yield item
        yield from panels(item.get("panels", []))
class DashboardContracts(unittest.TestCase):
    def test_host_focus_requires_one_inventory_identity(self):
        doc = dashboard("hosts")
        selector = doc["templating"]["list"]
        self.assertEqual(len(selector), 1)
        self.assertFalse(selector[0]["multi"])
        self.assertFalse(selector[0]["includeAll"])
        self.assertIn("node_monitoring_host_info", selector[0]["definition"])
        for panel in panels(doc["panels"]):
            for target in panel.get("targets", []):
                self.assertIn("$host", target["expr"], panel["title"])
    def test_lxc_parent_threads_are_not_guest_cpu(self):
        doc = dashboard("hosts")
        core = next(p for p in doc["panels"] if p["id"] == 6)
        self.assertIn('kind!="lxc"', core["targets"][0]["expr"])
        cpu = next(p for p in doc["panels"] if p["id"] == 2)
        self.assertIn("max by(guest_id)", cpu["targets"][0]["expr"])
        self.assertIn("pve_cpu_usage_ratio", cpu["targets"][0]["expr"])
    def test_inventory_coverage_does_not_follow_drive_selection(self):
        panel = next(p for p in dashboard("disks")["panels"] if p["id"] == 14)
        self.assertNotIn("$drive", panel["targets"][0]["expr"])
    def test_fleet_capacity_has_no_single_host_selector(self):
        doc = dashboard("capacity")
        self.assertEqual(doc["templating"]["list"], [])
        for p in panels(doc["panels"]):
            for t in p.get("targets", []): self.assertNotIn("$host", t["expr"])
    def test_navigation_does_not_override_single_host_scope(self):
        for path in ROOT.glob("*.json"):
            for link in json.loads(path.read_text()).get("links", []):
                self.assertFalse(link.get("includeVars", False), path.name)
if __name__ == "__main__": unittest.main()
