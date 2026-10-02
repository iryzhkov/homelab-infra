# SMART and temperature collection, phase 0 item 7

Date: 2026-10-02, 12:58 to 13:06 PDT.
Hosts: proxmox-a (hp-elite-mini-01), proxmox-b (hp-workstation-01), homelab
(InfluxDB), normandy (Gatus), from the laptop.

## Mismatch with the brief

The brief assumed `roles/base` existed; it did not. A minimal `base` role was
created containing only this collection, applied to the `hypervisors` group
through a new `playbooks/base.yml` (the hypervisors are the only machines that
see the physical drives; the VMs see virtio disks). It was later extended with
host metrics, recorded in [2026-10-02-host-metrics.md](2026-10-02-host-metrics.md).

## Drives and what is already installed

Command: `ssh proxmox-X 'ls -l /dev/disk/by-id/ | grep -v -- -part; dpkg -l smartmontools nvme-cli jq curl'`

```
proxmox-a  nvme-CT500P3SSD8_24244958383E   -> nvme0n1   Crucial P3 500 GB, boot
proxmox-a  nvme-CT2000P3SSD8_2420E8AFBCE0  -> nvme1n1   Crucial P3 2 TB
proxmox-b  nvme-WD_PC_SN810_SDCPNRY-256G-1006_231231800792 -> nvme0n1   WD SN810 256 GB, boot
proxmox-b  ata-CT1000BX500SSD1_2438E98C8AD2 -> sda      Crucial BX500 1 TB SATA
both: smartmontools 7.5-pve2, curl 8.14.1; no nvme-cli, no jq
```

Answer: the four drives match the plan. smartmontools ships with Proxmox and
Python 3.13 is present, so the collector is standard-library Python and
nothing was installed.

## Route from the hypervisors to InfluxDB and Gatus

Command (on both nodes): `curl -s -o /dev/null -w '%{http_code}' <url>` for
`http://192.168.70.223:8087/health`, `http://10.42.0.2:8087/health`,
`http://192.168.70.223:3002/health`, `http://192.168.70.234:3002/health`.

All four answered 200 from both nodes in 1 to 4 ms. InfluxDB v2.9.1 is
published by the `logging` stack on homelab as `8087:8086`; no Traefik route
exists for it.

Answer: the LAN path (homelab's inventory address) is used, as
`roles/notify` does; the URL is derived with `stack_host_ip(groups,
'logging')`, so it follows the stack. Plain HTTP on the LAN with a token that
can only write one bucket.

## InfluxDB tokens cannot be read back

Command: `GET /api/v2/authorizations` with the admin token, printing only
whether each entry carries a token.

Result: `admin's Token active token_present=False`.

Answer: InfluxDB 2.9 stores tokens hashed, so the write token must be captured
when it is created. `playbooks/base.yml` (first play, against homelab) creates
the bucket and the token through the API, as `playbooks/gatus-influx.yml`
does, and writes the token straight into the sops secret with `sops set
--value-stdin`. It refuses to run if only one half (token in InfluxDB, key in
the secret) exists.

## Design

- `roles/base/files/homelab-smart-report`: reads `smartctl -j -a` for every
  drive found under `/dev/disk/by-id` (de-duplicated by the device it
  resolves to), writes one `smart` point per drive with tags host, device,
  type, model, serial and fields temperature_c, wear_pct, bytes_written,
  data_units_written or total_lbas_written, power_on_hours, media_errors,
  critical_warning, smart_passed, available_spare_pct, unsafe_shutdowns,
  reallocated. NVMe wear is `percentage_used`; the BX500's is 100 minus the
  normalised value of attribute 202 Percent_Lifetime_Remain.
- Health per drive: healthy if SMART passed, no critical warning, no media
  errors, temperature below 70 C and wear below 80 %. Pushed to Gatus as an
  external endpoint (group `SMART`, one per drive in the hypervisor's
  `smart_drives` host variable) with a 45-minute heartbeat, so a collector
  that stops is a failure too. The endpoints exist only on normandy's Gatus,
  the instance that owns Discord alerts, and inherit its alerting.
- `homelab-smart.timer` at `*:0/15` runs `homelab-smart.service` (root,
  oneshot, `ProtectSystem=strict`). Configuration and tokens in
  `/etc/homelab-smart/env`, mode 0600.
- InfluxDB bucket `smart`, 400-day retention, write-only token
  `smart-write`.
- Secrets: `secrets/hardware-metrics.sops.yaml` (`GATUS_TOKEN`,
  `INFLUX_TOKEN`, later `PROXMOX_INFLUX_TOKEN`), encrypted to the repository's
  age recipient. The file was first created as `secrets/smart.sops.yaml` and
  renamed when the host metrics were added.
- Gatus: Gatus v5.37.0 supports external endpoints with a heartbeat. The
  token is rendered into normandy's `~/app/gatus/config.yaml`, which has to
  stay mode 0644 because the container runs as root with every capability
  dropped and so cannot read a file it does not own. The token only lets its
  holder report SMART results. homelab's Gatus renders the same template
  without the block, so its configuration did not change and its container
  was not restarted.

While creating the secret, a redaction filter failed and printed the first
Gatus token to the session transcript; it was replaced with a new random
value (`sops set --value-stdin`) before it was deployed anywhere.

## Apply

Commands, in order:

```
ansible-playbook playbooks/base.yml -l homelab --check --diff   # ok=9 changed=0 (bucket and token creation skip in check mode)
ansible-playbook playbooks/base.yml -l homelab --diff           # ok=12 changed=3: bucket created, token created, token recorded
ansible-playbook playbooks/base.yml --check --diff              # proxmox-a/b ok=7 changed=4 each, as expected
ansible-playbook playbooks/base.yml --diff                      # proxmox-a/b ok=8 changed=5 each
ansible-playbook playbooks/site.yml -l normandy -e only=gatus --check --diff  # changed=2: config file, restart
ansible-playbook playbooks/site.yml -l normandy -e only=gatus --diff          # changed=2
```

Gatus on normandy came back in seconds: `[config.ValidateEndpointsConfig]
Validated 4 external endpoints`, `/health` 200.

The first manual run exited 1 because Gatus was not deployed yet (`HTTP Error
404`), and it also showed a bug: proxmox-b's SN810 was not read, because the
filter that dropped the NVMe namespace aliases (`..._1`) also dropped a name
ending in an all-digit serial. The suffix test was removed; resolving each
link to its device already de-duplicates. Redeployed: changed=1 per node.

## Verification

Command: `systemctl start homelab-smart.service; echo $?` on each node.

```
proxmox-a exit 0
/dev/nvme0n1 CT500P3SSD8 24244958383E: 55 C, wear 19 %, healthy
/dev/nvme1n1 CT2000P3SSD8 2420E8AFBCE0: 59 C, wear 7 %, healthy
proxmox-b exit 0
/dev/nvme0n1 WD PC SN810 SDCPNRY-256G-1006 231231800792: 35 C, wear 2 %, healthy
/dev/sda CT1000BX500SSD1 2438E98C8AD2: 32 C, wear 0 %, healthy
```

Command: `systemctl list-timers homelab-smart.timer` on each node.

```
Fri 2026-10-02 13:15:24 PDT 10min - - homelab-smart.timer homelab-smart.service
```

Command (Flux, bucket `smart`):

```
from(bucket:"smart") |> range(start:-15m)
  |> filter(fn:(r)=> r._measurement=="smart" and contains(value: r._field,
       set: ["temperature_c","wear_pct","power_on_hours","media_errors"]))
  |> last() |> pivot(rowKey:["_time"], columnKey:["_field"], valueColumn:"_value")
```

```
_time                 host       model                          serial        media_errors  power_on_hours  temperature_c  wear_pct
2026-10-02T20:04:42Z  proxmox-a  CT2000P3SSD8                   2420E8AFBCE0  0             18745           59             7
2026-10-02T20:04:42Z  proxmox-a  CT500P3SSD8                    24244958383E  0             18629           55             19
2026-10-02T20:04:42Z  proxmox-b  CT1000BX500SSD1                2438E98C8AD2  0             15897           32             0
2026-10-02T20:04:42Z  proxmox-b  WD PC SN810 SDCPNRY-256G-1006  231231800792  0             1202            35             2
```

Command: `GET /api/v1/endpoints/statuses` on normandy's Gatus.

```
smart_proxmox-a-ct2000p3ssd8-2420e8afbce0 True 2026-10-02T20:04:42Z
smart_proxmox-a-ct500p3ssd8-24244958383e True 2026-10-02T20:04:42Z
smart_proxmox-b-ct1000bx500ssd1-2438e98c8ad2 True 2026-10-02T20:04:42Z
smart_proxmox-b-wd-pc-sn810-sdcpnry-256g-1006-231231800792 True 2026-10-02T20:04:42Z
```

Answer: all four drives report to InfluxDB and Gatus, healthy.

Idempotence: `ansible-playbook playbooks/base.yml --diff` again gave
homelab, proxmox-a and proxmox-b changed=0; the Gatus deploy in check mode
gave normandy changed=0.

## Implications for later phases

- The boot P3 500 GB on proxmox-a is at 19 % of rated endurance after 18 629
  power-on hours (about 2.1 years), with 45 978 102 data units (23.5 TB)
  written. At that rate it reaches the 80 % alert in roughly 7 more years,
  sooner if phase 3 moves write-heavy data onto it.
- The BX500 starts phase 3's Frigate recording at 0 % wear and 32 C; its
  wear and `bytes_written` are now the numbers to watch after that change.
- The P3 2 TB runs at 59 C idle with a 78.85 C second sensor (hwmon); the
  70 C alert threshold is close enough that sustained writes may trip it.
