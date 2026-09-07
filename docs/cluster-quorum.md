# Cluster quorum

Two Proxmox nodes with no third vote is a trap: lose either one and the
survivor holds 1 of 2 votes, so the cluster is inquorate. Guests that are
already running keep running, but nothing can be started, stopped or migrated,
and after a reboot they do not come back on their own. That bites at exactly
the moment a node has already failed.

Since 2026-09-06 the third vote comes from a QDevice on the OCI lighthouse.

## What it looks like

```
Expected votes:   3
Total votes:      3
Quorum:           2
Flags:            Quorate Qdevice
```

Lose a node and the survivor has its own vote plus the QDevice's: two of three,
still quorate. Lose the QDevice and both nodes still have two votes between
them. Only losing a node *and* the arbiter at once leaves the cluster
inquorate, which is where it started.

A cluster can have exactly one QDevice, so this is the only arbiter; the
algorithm is `Fifty-Fifty split` with the lowest node ID as tie-breaker.

## Why OCI, and not a machine in the house

gaming-pc is on and would have been closer, but Arch has no corosync in its
repositories — only an AUR package flagged out of date with no votes. A quorum
arbiter that silently stops working after a system update is worse than none,
because it looks like protection. The laptop travels and sleeps.

The OCI VM has `corosync-qnetd` in Ubuntu's repositories, is always on, and is
already the mesh lighthouse.

## No port was opened to the internet

The hypervisors joined the Nebula mesh for this: `proxmox-a` is 10.42.0.8 and
`proxmox-b` is 10.42.0.9. The QDevice talks to `10.42.0.1:5403` inside the
tunnel, and on OCI the firewall rule is bound to the `nebula1` interface:

```
4    ACCEPT  nebula1  tcp dpt:5403
```

`corosync-qnetd-tool -l` confirms where the connections come from — the nodes
appear as `10.42.0.8` and `10.42.0.9`, not as the house's WAN address. The only
public port involved is the one Nebula already used.

Joining the mesh brings something else for free: the hypervisors are now
reachable from anywhere on it, which they were not before.

## What was needed to build it

Both are worth knowing because neither is in the Proxmox documentation:

- The nodes need the `corosync-qdevice` package before `pvecm qdevice setup`
  runs; without it the setup fails partway through with
  `corosync-qdevice-net-certutil: command not found`, having already created a
  certificate database on the arbiter.
- On Ubuntu the qnetd certificate database is created as root but the daemon
  runs as `coroqnetd`, so it fails to start with `Can't open NSS DB directory
  (13): Permission denied` until the directory is chowned.

## Managed from here

`playbooks/nebula.yml` deploys all three mesh configurations, validating each
with `nebula -test` and pinging the lighthouse and a peer after any restart,
rolling back if the mesh does not come back. The QDevice itself is not managed:
it is a one-time `pvecm qdevice setup 10.42.0.1`, and its certificates live in
the cluster filesystem.
