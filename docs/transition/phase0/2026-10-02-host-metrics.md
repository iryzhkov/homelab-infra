# Host metrics: Proxmox CPU and guests, temperatures, iGPU busy time

Date: 2026-10-02, 13:06 to 13:14 PDT.
Hosts: proxmox-a (hp-elite-mini-01), proxmox-b (hp-workstation-01), homelab
(VM 101, UHD 770 passed through; also InfluxDB), normandy (VM 202, UHD 630
passed through to Frigate).

Scope added to phase 0 item 7 by Igor: Proxmox host and guest CPU, host
temperatures and iGPU usage into InfluxDB. Everything below is deployed
through `roles/base` and `playbooks/base.yml`; nothing on homelab touched
Docker or a container.

## 1. Proxmox's built-in metric server (hypervisor change)

Read-only first:

```
ssh proxmox-a 'cat /etc/pve/status.cfg; pvesh get /cluster/metrics/server --output-format json'
cat: /etc/pve/status.cfg: No such file or directory
[]
```

Answer: no metric server was configured.

The entry is cluster-wide (`/etc/pve/status.cfg`, token in
`/etc/pve/priv/metricserver/`), so `roles/base/tasks/proxmox_metrics.yml`
runs on the first hypervisor only, reads the entry with `pvesh get`, and
creates or corrects it with `pvesh`. The command Ansible ran on proxmox-a
(13:10), token elided:

```
pvesh create /cluster/metrics/server/homelab-influx --type influxdb --token <PROXMOX_INFLUX_TOKEN> \
  --server 192.168.70.223 --port 8087 --influxdbproto http --organization ryzhkov --bucket proxmox
```

The token comes from `secrets/hardware-metrics.sops.yaml` key
`PROXMOX_INFLUX_TOKEN`; it is on pvesh's command line, in a root-only process
list, for the moment pvesh runs. It was issued by the first play of
`playbooks/base.yml`: InfluxDB bucket `proxmox`, 90-day retention, token
`proxmox-write` that can write only that bucket.

Result:

```
$ cat /etc/pve/status.cfg
influxdb: homelab-influx
	port 8087
	server 192.168.70.223
	bucket proxmox
	influxdbproto http
	organization ryzhkov
$ ls -l /etc/pve/priv/metricserver/
-rw------- 1 root www-data 88 Oct  2 13:10 homelab-influx.pw
```

`pvesh get /cluster/metrics/server` lists only id, type, server, port and
disable, so the role reads `/cluster/metrics/server/homelab-influx` for the
comparison. A changed token is not detected; rotation is `pvesh delete
/cluster/metrics/server/homelab-influx`, delete `proxmox-write` in InfluxDB
and its key in the secret, then run the playbook.

Rollback: `pvesh delete /cluster/metrics/server/homelab-influx`.

Verification (Flux, bucket `proxmox`):

```
schema.measurements(bucket:"proxmox")
  ballooninfo blockstat cpustat memory nics proxmox-support system

from(bucket:"proxmox") |> range(start:-5m)
  |> filter(fn:(r)=> r._measurement=="cpustat" and r._field=="cpu" and r.object=="nodes") |> last()
_time                 _value             host
2026-10-02T20:11:18Z  0.219955763086753  hp-elite-mini-01
2026-10-02T20:11:18Z  0.238659106070714  hp-workstation-01

from(bucket:"proxmox") |> range(start:-2m)
  |> filter(fn:(r)=> r._measurement=="system" and r._field=="cpu" and (r.object=="qemu" or r.object=="lxc")) |> last()
homelab qemu 101 0.590   omarchy qemu 202 0.287   homeassistant qemu 103 0.038
black-pearl qemu 102 0.027   public-api qemu 106 0.010   VM 9202 qemu 9202 0
adguard lxc 100, adguard lxc 107, alpine-adguard lxc 201, healthcheck lxc 110,
mqtt lxc 104, zigbee2mqtt lxc 105 (all below 0.03)
```

Answer: both nodes and all 12 guests report CPU (a 0 to 1 fraction), memory,
disk and network every 10 seconds. VM 9202 was another executor's scratch
restore at the time.

## 2. Temperatures

Read-only first: every `temp*_input` under `/sys/class/hwmon`.

```
proxmox-a acpitz: -267.4 (invalid)  nvme (P3 500 GB): Composite 53.85
          nvme (P3 2 TB): Composite 58.85, Sensor 2 78.85
          coretemp: Package id 0 71, Core 0-5 61-71   iwlwifi_1: no data
proxmox-b r8169 NIC: 65   nvme (SN810): Composite 34.85   pch_cannonlake: 67
          coretemp: Package id 0 61, Core 0-5 60-62
```

Answer: coretemp, the NVMe sensors and the PCH are readable from sysfs; no
lm-sensors needed. `homelab-smart-report` now also writes one `hwmon` point
per sensor (tags host, chip, device, sensor; field temperature_c) to the
`smart` bucket on the same 15-minute timer, skipping sensors with no data and
readings outside -40 to 150 C.

Verification: manual run `13 hwmon temperature sensors` on proxmox-a and `10`
on proxmox-b, exit 0. Flux:

```
from(bucket:"smart") |> range(start:-20m) |> filter(fn:(r)=> r._measurement=="hwmon" ...) |> last()
2026-10-02T20:11:06Z  75  coretemp        proxmox-a  Package id 0
2026-10-02T20:11:06Z  77  coretemp        proxmox-b  Package id 0
2026-10-02T20:11:06Z  67  pch_cannonlake  proxmox-b  temp1
```

Answer: deployed and reporting. Both packages run at 75 to 77 C while the
collector itself is running.

## 3. iGPU busy time

Probe, read-only, normandy first:

```
perf_event_paranoid = 2; /sys/bus/event_source/devices/i915 present (rcs0/vcs0/vecs0/bcs0-busy,
actual-frequency, rc6-residency); card0 i915 at 0000:01:00.0; intel-gpu-tools 2.5-1 installed
sudo intel_gpu_top -J -s 1000   -> Failed to detect engines! (No such file or directory)
sudo intel_gpu_top -d pci:vendor=8086,device=3E92 -J  -> same
```

`intel_gpu_top` expects a PMU named after the PCI address for a GPU that is
not at 00:02.0, while the kernel registered this passed-through GPU as plain
`i915`. Reading the PMU directly with `perf_event_open` (a 30-line Python
probe run once as root, 2-second window) worked:

```
normandy: rcs0-busy 7.0%  vcs0-busy 4.4%  vecs0-busy 0.0%  bcs0-busy 0.0%  actual-frequency 66 MHz
```

homelab (perf_event_paranoid = 3, PMU `i915_0000_00_10.0`, extra engine
vcs1): the same probe returned 0.0 % on every engine, rc 0.

Answer: a usable counter exists on both guests; root's CAP_PERFMON is enough,
so `perf_event_paranoid` is not changed, no sidecar, no privileged container,
no package install, nothing restarted. Cost: seven or eight open perf
counters and one read per minute; after four minutes the service showed
0.0 % CPU and 19.6 MB (homelab) and 24.9 MB (normandy) RSS.

Deployed: `roles/base/tasks/gpu.yml` on hosts with `gpu_metrics: true`
(homelab and normandy host_vars) installs `/usr/local/bin/homelab-gpu-report`
and a root system service `homelab-gpu.service` (`Restart=always`,
`ProtectSystem=strict`, `Nice=10`). It keeps the counters open and writes one
`igpu` point per minute (tags host, device; fields `<engine>_busy_pct`,
`actual_freq_mhz`, `requested_freq_mhz`, `rc6_pct`), the average over the
whole minute, to the `smart` bucket with the `smart-write` token.

Verification:

```
systemctl is-active homelab-gpu.service      # active on both, NRestarts=0
from(bucket:"smart") |> range(start:-5m) |> filter(fn:(r)=> r._measurement=="igpu" ...) |> last()
_time                 host      actual_freq_mhz  rc6_pct  rcs0_busy_pct  vcs0_busy_pct
2026-10-02T20:11:58Z  homelab   0                100      0              0
2026-10-02T20:11:59Z  normandy  159.1            67.9     9.28           3.99
```

Answer: deployed and reporting. homelab's UHD 770 was fully idle (100 % RC6)
in that minute; normandy's UHD 630 runs Frigate's decode at about 9 % render
and 4 % video.

## Idempotence and after-check

```
ansible-playbook playbooks/base.yml --diff          # changed=0 on homelab, normandy, proxmox-a, proxmox-b
ansible-playbook playbooks/base.yml --check --diff  # changed=0 on every host
ansible-playbook playbooks/site.yml --check --diff  # only the three pre-existing changes, see the baseline file
```

See [2026-10-02-ansible-baseline.md](2026-10-02-ansible-baseline.md).

## Implications for later phases

- The `proxmox` bucket is the high-volume one (every guest and node every
  10 seconds) on homelab's InfluxDB, whose disk sits on the thin pool phase 3
  is about. Its size should be read after a week (`influx bucket list` or the
  `_monitoring` bucket) before the retention is lengthened.
- When guests are rebuilt from templates or move to a Swarm (phases 2 and 5),
  `gpu_metrics: true` must follow the GPU, not the hostname.
- The metric server sends to homelab's LAN address; while homelab is stopped
  in phase 3, Proxmox drops those points (no buffering).
