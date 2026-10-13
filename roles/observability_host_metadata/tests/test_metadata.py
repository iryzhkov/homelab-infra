#!/usr/bin/env python3
"""Inventory/template acceptance fixtures; run in the deployment Ansible venv."""
from pathlib import Path
import unittest

from ansible.parsing.dataloader import DataLoader
from ansible.inventory.manager import InventoryManager
from ansible.vars.manager import VariableManager
from jinja2 import Environment, FileSystemLoader, StrictUndefined

ROOT = Path(__file__).resolve().parents[3]
ROLE = ROOT / "roles/observability_host_metadata"
KEYS = {"observability_host_kind", "observability_host_capabilities",
        "observability_guest_id", "observability_gpu_owners", "proxmox_vmid", "smart_drives"}
env = Environment(loader=FileSystemLoader(str(ROLE / "templates")), undefined=StrictUndefined)

class MetadataAcceptanceTest(unittest.TestCase):
    def setUp(self):
        self.loader = DataLoader()
        self.loader.set_basedir(str(ROOT))
        self.inventory = InventoryManager(loader=self.loader, sources=[str(ROOT / "inventory/hosts.yml")])
        self.variables = VariableManager(loader=self.loader, inventory=self.inventory)

    def metadata(self, names):
        values = {}
        for name in self.inventory.get_hosts("all"):
            original = self.variables.get_vars(host=name)
            values[name.name] = {k: v for k, v in original.items() if k in KEYS}
        return env.get_template("host-metadata.prom.j2").render(
            observability_metadata_hosts=names, hostvars=values)

    def test_new_inventory_vm_appears_without_a_dashboard_edit(self):
        self.inventory.add_host("metadata-fixture-vm", group="docker_hosts")
        text = self.metadata(["metadata-fixture-vm"])
        self.assertIn('node_monitoring_host_info{host="metadata-fixture-vm",kind="vm"} 1', text)
        self.assertNotIn('node_monitoring_capability_info{', text)
        self.assertNotIn('node_monitoring_gpu_owner_info{', text)
        # Only in-memory inventory changed; no dashboard fixture or host name
        # list is consulted or modified to publish the new classification.

    def test_hardware_claims_include_only_real_physical_sources_and_passthrough_owner(self):
        text = self.metadata(["proxmox-a", "proxmox-b", "gaming-pc", "laptop", "homelab", "normandy", "ct105"])
        for name in ["proxmox-a", "proxmox-b", "gaming-pc", "laptop"]:
            self.assertIn('host="' + name + '",kind="physical"', text)
        self.assertIn('host="ct105",kind="lxc",guest_id="lxc/105"', text)
        self.assertNotIn('node_monitoring_capability_info{host="ct105"', text)
        self.assertNotIn('host="gaming-pc",capability="smart"', text)
        self.assertNotIn('host="laptop",capability="smart"', text)
        self.assertIn('node_monitoring_smart_expected_devices{host="proxmox-a"} 2', text)
        self.assertIn('node_monitoring_smart_expected_devices{host="proxmox-b"} 2', text)
        self.assertNotIn('node_monitoring_smart_expected_devices{host="gaming-pc"}', text)
        self.assertNotIn('node_monitoring_smart_expected_devices{host="ct105"}', text)
        self.assertIn('host="homelab",physical_host="proxmox-a",physical_device="0000:00:02.0",gpu_kind="igpu",mode="passthrough"', text)
        self.assertIn('host="normandy",physical_host="proxmox-b",physical_device="0000:00:02.0",gpu_kind="igpu",mode="passthrough"', text)

    def test_parent_exported_guest_metadata_preserves_explicit_guest_identity(self):
        text = self.metadata(["proxmox-a", "ct100", "ct104", "ct107"])
        for number in [100, 104, 107]:
            self.assertIn('host="ct' + str(number) + '",kind="lxc",guest_id="lxc/' + str(number) + '"', text)
        samples = [line for line in text.splitlines() if line.startswith("node_monitoring_host_info{")]
        self.assertEqual(len(samples), 4)

if __name__ == "__main__":
    unittest.main()
