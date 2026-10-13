# Inventory monitoring metadata

The existing Alloy node textfile collector reads a root-owned0644
`host-metadata.prom`. Convergence atomically replaces this small static file;
there is no new producer, timer, package, credential or exporter/application
restart. Each publishing node is bounded to64 host identities, three
capabilities and two GPU ownership records per identity.

Inventory explicitly declares `observability_host_kind` as physical, vm or
lxc. Docker application VMs, cloud VMs and agent VMs inherit their class from
their inventory groups; physical desktop/laptop/hypervisors and LXC guests
have explicit declarations. New hosts in an established class group receive
metadata through normal collector convergence, so dashboards discover them
without a host list in JSON. Unknown classes are refused.

The metrics are descriptive gauges with value1, never health substitutes:

- `node_monitoring_host_info{host,kind,guest_id?}`
- `node_monitoring_capability_info{host,capability,mode}`
- `node_monitoring_smart_expected_devices{host}` (inventory coverage count)
- `node_monitoring_gpu_owner_info{host,physical_host,physical_device,gpu_kind,mode}`

Capabilities exist only for explicitly supported telemetry: SMART on both PVE
hosts, gaming-pc and the laptop (seven physical SSDs; Apple namespace aliases
count once), physical temperatures on all four, native desktop NVIDIA/AMD GPU
readings and passthrough Intel iGPU on homelab/normandy. Independent inventory
families node_monitoring_physical_drive_info and node_monitoring_gpu_inventory_info
include every declared physical device, including unsupported/unproven drive
telemetry and inventory-only Apple AGX. Inventory is not a health signal; missing
measurements stay missing. All five physical GPU ownership keys remain unique. No unsupported capability emits a
zero. An existing capability with missing measurements must still show
missing data; value1 does not claim sensor health. The desktop OS hostname
omarchy-pc is the existing gaming-pc inventory identity, not a second machine.

GPU ownership maps VM101/homelab to proxmox-a PCI0000:00:02.0 and
VM202/normandy to proxmox-b PCI0000:00:02.0, verified from live Proxmox config.
Duplicate physical GPU owners are rejected across inventory. Aggregate GPUs
by distinct(physical_host,physical_device); guest ownership must not add
another physical device. This currently identifies monitored Intel iGPUs;
unavailable discrete/Apple GPU performance is not synthesized.

Optional `guest_id` matches PVE metric IDs such as qemu/101 and lxc/100.
Known mapped VMs are101,102,106,108,111,202,211; CT IDs come from the existing
`proxmox_vmid` declaration. Physical CPU capacity must join kind=physical and
count reported logical CPUs/SMT threads from node_cpu_seconds_total. VM/LXC
CPU/RAM views remain separate. PVE guest CPU/memory is preferable for LXC
capacity because a guest may expose inherited host counters/sensors. CT105
currently exposes physical hwmon metrics but is explicitly LXC with no
physical temperature capability.

Small native Debian exporters currently disable textfile collection. Their
owning PVE renders metadata for declared job=node guest scrapes with explicit
child host labels; this adds no exporter restart. Alloy's external host label
preserves those explicit labels. These metadata records have the parent's
instance label: join on(host), not on(instance).

Run `playbooks/observability-host-metadata.yml` from the laptop controller
for a textfile-only rollout; its inventory connection=local for laptop must
not run from gaming-pc. Normal collector convergence includes this role too.
The focused play skips guests with Alloy disabled because their owning
parent publishes metadata. No changes to upstream producer or build tooling.

Acceptance fixtures:
`/home/igor/.local/share/venvs/ansible/bin/python roles/observability_host_metadata/tests/test_metadata.py`.
Three tests verify new in-memory inventory VM discovery without a dashboard
edit, truthful hardware/guest/GPU declarations, and explicit parent-exported
guest identities.

Live PVE-a proof,2026-10-13: the focused play changed only the metadata file;
the second run reported changed=0. Alloy PID1664093 stayed unchanged. A real
VictoriaMetrics query returned job=node host_info for proxmox-a physical and
ct100/ct104/ct107 LXC with their correct guest_id, plus only the parent's
SMART/temperature capabilities. Explicit child labels survived remote_write;
there was one physical PVE identity. A verified empty local test directory
shadowing proxmox-a.yml was removed before proof; no tracked source was
replaced or deleted.
