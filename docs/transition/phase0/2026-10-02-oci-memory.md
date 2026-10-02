# Phase 0 check: OCI micro memory headroom

Measured 2026-10-02 13:00 PDT. Host: oci (free-tier-vm-instance,
150.230.40.9), x86_64, Ubuntu 24.04.

## Output

`free -m`:

```
       total  used  free  shared  buff/cache  available
Mem:     954   430   100       4         592        523
Swap:   2047    67  1980
```

`/proc/meminfo` a moment later: `MemAvailable 568680 kB`, `Committed_AS
1516648 kB`, `CommitLimit 2585692 kB`. `/proc/pressure/memory`: all averages
0.00. Swap is a 2 GiB `/swapfile`, 67 MB used.

`nproc`: 2. `uptime`: up 32 days, load average 0.01, 0.02, 0.00.

Services (`systemctl is-active`, cgroup `MemoryCurrent`):

```
nebula           active   22 MiB
corosync-qnetd   active  2.3 MiB
caddy            active   50 MiB
docker           active   66 MiB
```

`ps -eo rss,comm --sort=-rss | head` (KiB): systemd-journal 36832, fwupd 30200,
gatus 29932, caddy 29816, dockerd 26932, snapd 23608, containerd 19148, oci-wlp
19004, gomon 17712, agent 15772, nebula 15624.

`docker --version`: `Docker version 29.1.3, build 29.1.3-0ubuntu3~24.04.2`.
Docker already runs one container, `gatus-oci` (21 MiB). `docker info`:
`swarm=inactive`.

`df -h /`: 45G, 6.4G used, 38G free.

## Assessment

Docker is already installed and running, so a Swarm manager adds only the
swarmkit and raft state to the existing dockerd: about 50-100 MB more resident,
not the full 100-150 MB of a fresh daemon. Against 523-568 MB available that
leaves roughly 400 MB, with 2 GiB of nearly idle swap behind it and no memory
pressure. Raft's write-ahead log and snapshots are small for a three-manager
cluster with a few dozen services. fwupd and snapd (about 54 MB together) can
be removed if more margin is wanted.

## Recorded answer

523 MB available (100 MB free, 592 MB cache) of 954 MB with nebula, qnetd, Caddy
and Docker (gatus) already running; a drained Swarm manager fits, with about
400 MB margin.

## Implications for later phases

- Phase 4: the three-manager layout (homelab, normandy, OCI drained) is viable
  on memory. The plan's single-point-of-failure caveat stands: OCI also carries
  the QDevice and the Nebula lighthouse.
