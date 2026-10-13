# Physical SMART telemetry

Apply `playbooks/observability-hardware.yml` to the explicitly declared physical
hardware owners: both hypervisors, gaming-pc and laptop. The laptop's native
smartmontools utility was installed by the scoped probe play, and read-only
SMART health/temperature/wear/spare/error commands succeeded before claiming
supported telemetry. Its three namespaces share one Apple SSD controller;
namespace1 is explicitly selected and the physical drive is counted once.
Both amd64 and arm64 official0.15.0 assets have separate checksum pins.
The desktop uses its existing /usr/bin/smartctl; no app or driver changes. This adds only
`observability-smartctl-exporter.service` and its versioned binary/cache directories.
Existing SMART/Influx/Gatus collectors and timers are retained.

The official v0.15.0 linux-amd64 archive is checksum pinned:
`32ff269f2ca5708e2aa8209c715192efcc32f87bd575cae50a76d391a484976e`.
The release and checksum list were verified, and the downloaded binary's actual
`--help` validated the configured flags.

Drive selection uses existing `smart_drives` serials. The role finds stable
`/dev/disk/by-id/` aliases, excludes partitions, confirms exact SMART serials
and refuses missing or ambiguous identities. It never runs self-tests, enables
SMART, or changes drive settings. Exporter read arguments are reviewed in
the tagged upstream source. Five-minute polling and standby checks limit
additional drive activity.

The listener is strictly `127.0.0.1:9633`. Root and the RAWIO/ADMIN capabilities
provide ATA/NVMe ioctl access; filesystem and kernel protections, CPU, memory
and task limits bound the service. Physical drive devices remain visible.

The rollout needs a separate local Alloy scrape target:
`127.0.0.1:9633`, job `integrations/smartctl`, stable inventory `host`, and
`instance`. Suggested scrape interval is `60s` and timeout `60s`; the exporter
refreshes drive data at most once per five minutes. Preserve standard exporter
metric names and its `device` label. `smartctl_device` provides `serial_number`
and `model_name`; other series can be joined on `host,device`.

Useful metrics include `smartctl_device_smart_status` (1 passed, 0 failed),
`smartctl_device_temperature`, `smartctl_device_percentage_used`,
`smartctl_device_available_spare` and `smartctl_device_media_errors`.
CPU/board temperatures still come from the host hwmon collector in Alloy.
The role checks HTTP readiness and confirms every declared serial appears.

Check mode resolves and identifies drives read-only and previews directory/unit
changes. It skips downloads, extraction, service changes and metrics readiness.

NUT/UPS exporting is deferred: this repository does not declare a NUT endpoint,
UPS name or exporter credentials. An exporter needs those confirmed read-only
inputs first; the hardware role does not modify UPS shutdown coordination.

Migration: move the physical-drive declaration with the hardware and reapply.
Stable serial/by-id identities tolerate kernel disk renumbering. Missing inventory
drives fail before service configuration changes, keeping the running exporter
configuration intact.

Primary references:

- [Official release](https://github.com/prometheus-community/smartctl_exporter/releases/tag/v0.15.0)
- [Official checksum list](https://github.com/prometheus-community/smartctl_exporter/releases/download/v0.15.0/sha256sums.txt)
- [Tagged CLI documentation](https://github.com/prometheus-community/smartctl_exporter/blob/v0.15.0/README.md)
- [Read-only smartctl arguments](https://github.com/prometheus-community/smartctl_exporter/blob/v0.15.0/readjson.go)
- [Standard metric schema](https://github.com/prometheus-community/smartctl_exporter/blob/v0.15.0/metrics.go)
