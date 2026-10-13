# Native physical GPU readings

A bounded oneshot/timer reads explicitly declared physical NVIDIA/AMD devices
into the existing root-owned Alloy textfile directory. NVIDIA uses only
nvidia-smi query flags; AMD uses read-only PCI/sysfs/hwmon attributes. No driver,
power setting, benchmark, application or Intel VM PMU/Influx collector changes.

Gauges have host and stable PCI device labels: gpu_utilization_ratio,
gpu_temperature_celsius, gpu_memory_total_bytes, gpu_memory_used_bytes,
gpu_power_watts when natively supported, gpu_last_success_timestamp_seconds and
gpu_scrape_success. Unsupported fields are omitted, not zero. A failed adapter
publishes success0 with no measurements/timestamp, clearing old values at the
next successful textfile write. Gate graphs on success1 and timestamp age<180s.
Utilization is a sampled duty cycle; NVIDIA/AMD busy is not equivalent to an
Intel video/render engine nor a FLOP or throughput sum. Memory is vendor-reported
VRAM (AMD's integrated VRAM reservation), not every possible shared allocation.
AMD APU/package power is omitted unless the native sensor explicitly says GPU.

Inventory metadata includes all five physical GPUs once, with the Apple AGX
entry inventory_only. Display controllers and aliases do not become extra GPUs.
Apple has no supported utilization or GPU temperature attributes on this kernel.
The source uses no credentials, captures native stderr privately and reports
only success status. Atomic temporary files are exclusively created under the
root-owned directory; only the owned timer/service is enabled or refreshed.

Rollback: stop/disable observability-native-gpu.timer, stop its oneshot, remove
only native-gpu.prom and the owned config/script/units. Existing Intel writers
and all applications remain untouched.
