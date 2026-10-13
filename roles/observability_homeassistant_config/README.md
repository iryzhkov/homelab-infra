# Guarded Home Assistant metrics source

This role reconciles only `/config/packages/homelab_prometheus.yaml` and its explicit `homeassistant.packages` include. It preserves Influx writers/exclusions, the rest of configuration, ignored credentials, UI state and the existing unpublished Git history. It does not create identities/tokens, commit/push HA Git or replace the HA repository. The dedicated read-only scraper token remains owned by `observability_homeassistant`.

Run on the existing **laptop Ansible controller**, whose `home-assistant` SSH alias reaches the HA SSH add-on:
`ansible-playbook playbooks/observability-homeassistant-config.yml --check`.
Python runs on the controller; the remote uses existing SSH, shell, Git, tar, base64 and native `ha` CLI. Override the SSH target for another preconfigured controller; this does not bootstrap generic HA OS SSH connectivity.

An unchanged normal run is safe and never restarts Core. Check mode reads only and reports drift. A real source change requires `-e observability_homeassistant_config_maintenance=true`; before writing it requires clean owned Git paths and saves a private root-readable config/package tar under `/backup`, recording baseline Git HEAD. It rejects ambiguous or differently owned includes, applies only these two paths, and runs `ha core check`. A failed check restores both owned paths without restarting Core.

A change-only restart additionally requires `-e observability_homeassistant_config_restart=true` in an explicitly scheduled maintenance window, after the separate HA full protected-backup prerequisite and availability baseline. Without that option, validated source is prepared but awaits an operator restart. Never run an extra restart merely to converge an unchanged host. On 2026-10-13 the live package already matches; the previously approved restart remains the only one used.

Privacy exclusions omit location tracking/person domains and high-rate radar/mmwave/robot payloads. Native metrics supplement retained Influx history. After an authorized activation verify HTTPS authenticated metrics, one healthy homeassistant target, unchanged automations/integrations and continuing Influx writes. Metrics embedding uses the Grafana-owned HA UI helper and needs no Core restart.

Mock acceptance covers unchanged normal/check convergence, drift read-only check, denied maintenance-less mutation and config-check failure restoration with no restart. Live check and unchanged normal convergence are required before claiming deployed source reconciliation.
