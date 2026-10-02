# Serviceable transition: evidence and hypervisor changes

This directory holds the dated evidence of the serviceable transition: what
was measured before each step, the exact commands run, and a record of every
change made on a hypervisor, by hand or through Ansible. One directory per
phase; each file is named `YYYY-MM-DD-<topic>.md` after the day of the
measurement.

- [phase0/](phase0/) - baseline, backups and checks before any storage change.
- [phase1/](phase1/) - black-pearl's partial downloads onto the NAS media share, its media mount made hard and Ansible-managed, the 1.8 TB disk retired.
- [phase2/](phase2/) - ZFS ARC cap, `tank` pools on both nodes, normandy's data disk on `tank`, cloud-init templates; durable summary in [../storage-pools.md](../storage-pools.md).
