# Physical hardware coverage acceptance — 2026-10-13

## Actual inventory and support

The four physical hosts declare seven physical SSDs and five physical GPUs. Central VictoriaMetrics queries after deployment confirmed `count(node_monitoring_physical_drive_info{job="node"}) = 7`, `count(node_monitoring_gpu_inventory_info{job="node"}) = 5`, and seven actual `smartctl_device{job="smartctl"}` series: proxmox-a 2, proxmox-b 2, gaming-pc 2, laptop 1. All seven actual `smartctl_device_smart_status` values were 1. Inventory is explicit and separate from health telemetry.

| Host | Physical disks | Physical GPU ownership |
| --- | --- | --- |
| proxmox-a | Crucial P3 2TB and 500GB | Intel UHD770, solely measured in homelab VM101 |
| proxmox-b | WD SN810 256GB and Crucial BX500 1TB | Intel UHD630, solely measured in normandy VM202 |
| gaming-pc (OS hostname omarchy-pc) | Samsung 990 EVO Plus 1TB and 2TB | NVIDIA RTX4090 `0000:01:00.0`; AMD Raphael `0000:0c:00.0` |
| laptop | Apple AP0512Z, one physical SSD | Apple AGX `206400000.gpu`, inventory-only |

Apple namespaces 1, 2 and 3 belong to one controller/serial. The explicit namespace-1 discovery fence prevents three aliases becoming three disks. The Apple display-controller DRM node is not another GPU. No native Apple GPU busy/temperature interface was available; no fabricated performance or zero-health data is emitted.

## Native SMART and Apple compatibility

The laptop's authorized bootstrap installed only smartmontools 7.5-2 from existing signed package indexes. No index refresh, system upgrade, self-test, benchmark or wake-policy change was performed; smartd remains inactive and disabled. Native read-only SMART successfully reported health, temperature, wear, spare capacity and media errors for the exact namespace-1 SSD.

The upstream pinned smartctl_exporter 0.15.0 adds an optional error-history log request that this Apple controller rejects. The narrow root-owned `smartctl-apple-supported` adapter accepts only that pinned read-only argument vector and exact declared by-id device; it removes only `--log=error`. It checks namespace, controller serial and `nvme-apple` driver, then execs native smartctl unchanged. It neither rewrites JSON nor masks native exit/health failures. Dangerous or different command vectors are rejected.

Central laptop SMART health is 1 and temperature/wear/media-error measurements are present. `smartctl_device_error_log_count{host="laptop",job="smartctl"}` has no series. Inventory explicitly declares `error_history="unsupported"`; optional error-history absence is distinct from measured media errors. The dashboard preserves that distinction.

Official ARM64 exporter archive is checksum-pinned at `9a870fe6401748f51f5edcd73057f1c90c0b975f1f48d986eacc61443320276c` (upstream v0.15.0 release checksum asset). AMD64 remains independently pinned. Both desktop/mobile exporters bind only 127.0.0.1:9633 and have MemoryMax 128MiB and TasksMax 32. Both were active, Result=success, ExecMainStatus=0, NRestarts=0 at acceptance.

## Native desktop GPU readings

The bounded 60-second owned timer emits actual NVIDIA nvidia-smi readings and AMD amdgpu sysfs readings, using stable host/PCI device labels. Metrics are gauges: `gpu_utilization_ratio`, `gpu_temperature_celsius`, `gpu_memory_total_bytes`, `gpu_memory_used_bytes`, optional `gpu_power_watts`, `gpu_scrape_success`, and `gpu_last_success_timestamp_seconds`. Unsupported fields are absent. AMD package/PPT power is not presented as GPU power. AMD VRAM reservation is not total shared system memory.

Central acceptance showed both desktop adapters with `gpu_scrape_success=1`, last-success age 18.2 seconds, and real temperatures 29C NVIDIA / 34C AMD. Their sampled duty-cycle values are not FLOPs or throughput and are separate from Intel engine-specific percent metrics. Dashboards gate desktop measurements on success and age below 180 seconds; inventory and age remain visible. A failed adapter clears its old measurements and emits success 0 without exposing error payloads.

The unit is an owned oneshot (normally inactive with successful last result), with active enabled timer, 128MiB memory cap, TasksMax16 and no capabilities. Private GPU config is root:root 0600 under root-only 0700 directory. The existing shared output directory remains root:alloy 0755; atomic output is root-owned 0644 and no temporary native-GPU files remain. Intel VM writer units stayed active with original start timestamps 2026-10-13 03:41:10 PDT; their PMU/Influx configuration was not changed.

## Source, deployment and convergence evidence

Source changes are reviewed through homelab-infra PR3. Hardware extension `504ac26` passed exact self-hosted CI38452383636; native GPU readiness/types `482e5a9` passed CI38452843319; Apple adapter `18e6eff` passed CI38453890804. Five hardware fixtures, five native-GPU fixtures and four metadata fixtures passed. Tests exercise physical namespace deduplication/ambiguity, strict Apple command fencing, native unit conversion and unsupported-field omission, failure clearing and atomic permissions, plus seven-disk/five-GPU inventory semantics.

Collector and metadata deployment used controller source `9c2459b`; the shared-directory group convergence correction is `d094efc`. Application services were not restarted. Only owned monitoring services were started/reloaded as their configuration required. No UpKeeper publication, fleet hold override, driver change or destructive hardware operation occurred.

Final check-mode recaps: hardware all four physical hosts changed=0/failed=0; collectors gaming-pc and laptop changed=0/failed=0; metadata proxmox-a, proxmox-b, gaming-pc, laptop, homelab and normandy changed=0/failed=0. Native GPU ordinary convergence (ok10) and check-mode (ok7) both changed=0/failed=0 after the shared-directory ownership correction.

Rollback is scoped to the owned native-GPU timer/unit/script/config/textfile and the owned SMART exporter role settings; preserve application/driver units and the existing shared metadata/Intel writers. Existing role READMEs document device declarations, support boundaries and collector rollback. Unsupported inventory remains visible rather than being reported healthy.
