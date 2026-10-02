# Ansible baseline and after-check, phase 0

Date: 2026-10-02, 12:57 to 13:20 PDT.
Controller: the laptop (omarchy-macbook-air-igor), ansible-core 2.21.3, from
the repository root on branch `transition/phase-0`.
Hosts: homelab, public-api, normandy, blackpearl, proxmox-a, proxmox-b, oci.

The fleet-wide checks that bracket phase 0 item 7 (the `base` role). The
before runs happened before any file of that item was written.

## Before: connectivity

Command: `ansible-playbook playbooks/ping.yml`

```
homelab: Debian 12.15, python 3.11.2, 4 vcpu, 28064 MB
public-api: Debian 12.15, python 3.11.2, 2 vcpu, 3915 MB
normandy: Archlinux 4.0.4, python 3.14.7, 4 vcpu, 23956 MB
blackpearl: Debian 12.15, python 3.11.2, 3 vcpu, 7940 MB
proxmox-a: Debian 13.6, python 3.13.5, 12 vcpu, 63974 MB
proxmox-b: Debian 13.6, python 3.13.5, 6 vcpu, 31871 MB
oci: Ubuntu 24.04, python 3.12.3, 2 vcpu, 954 MB
```

Every host: ok=2 changed=0 unreachable=0 failed=0.

Answer: all seven hosts reachable, exit 0.

## Before: converge check

Command: `ansible-playbook playbooks/site.yml --check --diff`

```
blackpearl : ok=20  changed=0 unreachable=0 failed=0 skipped=13
homelab    : ok=386 changed=1 unreachable=0 failed=0 skipped=160
normandy   : ok=65  changed=1 unreachable=0 failed=0 skipped=17
public-api : ok=25  changed=1 unreachable=0 failed=0 skipped=24
```

Reported changes, verbatim task and host:

- `drift_check : Write the manifest of what was deployed` on homelab
  (`/home/iryzhkov/.local/share/homelab-infra/drift-manifest.json`).
- `drift_check : Write the manifest of what was deployed` on normandy
  (`/home/igor/.local/share/homelab-infra/drift-manifest.json`).
- `drift_check : Install the checker` on public-api
  (`~/.local/bin/homelab-drift-check`): the deployed copy lacks the UpKeeper
  integration (`upkeeper_findings()`, `import shutil`, exit status 2).

The two manifest diffs were parsed as JSON on both sides: before and after are
equal (36 stacks on homelab, 4 on normandy). Only the formatting differs: the
file on the host was last written by a targeted `-e only=` deploy, which writes
`to_nice_json` with 4-space indentation, while the template writes 2-space.
The public-api change is real: the checker in the repository is newer than the
one deployed there.

Answer: no drift on any Docker host's stacks; three pre-existing changes,
none of them a service, left as found (not fixed, per the phase 0 rules).

## After: converge check

Command: `ansible-playbook playbooks/site.yml --check --diff`, run after the
SMART collection was deployed and again after the host metrics were deployed
(see the two files named below).

After SMART (13:05):

```
blackpearl : ok=20  changed=0 unreachable=0 failed=0 skipped=13
homelab    : ok=386 changed=1 unreachable=0 failed=0 skipped=160
normandy   : ok=67  changed=1 unreachable=0 failed=0 skipped=16
public-api : ok=25  changed=1 unreachable=0 failed=0 skipped=24
```

The same three tasks as before; both manifest diffs again parse equal as JSON.
normandy has two more ok tasks because its `gatus` entry now has
`prepare_tasks: smart.yml`.

After the host metrics (13:14), identical counts to the run after SMART:

```
blackpearl : ok=20  changed=0 unreachable=0 failed=0 skipped=13
homelab    : ok=386 changed=1 unreachable=0 failed=0 skipped=160
normandy   : ok=67  changed=1 unreachable=0 failed=0 skipped=16
public-api : ok=25  changed=1 unreachable=0 failed=0 skipped=24
```

The same three pre-existing tasks, and both manifest diffs still parse equal
as JSON.

Answer: the base role and the Gatus change introduced no new difference
between the repository and the Docker hosts.

## Resolving the three pre-existing changes

Phase 0 acceptance requires `site.yml --check` to report no changes.

**Drift manifest on homelab and normandy: a formatting bug, not drift.** The
manifest has two writers. `roles/drift_check/templates/drift-manifest.json.j2`
(a full converge) wrote 2-space JSON, with the stacks array spliced in by a
nested `to_nice_json(indent=2)`, and a trailing newline. The targeted-deploy
block in `playbooks/site.yml` (`-e only=<stack>`) rewrites the same file with
`to_nice_json`: 4-space indentation and no trailing newline. Both hosts had
last been written by a targeted deploy (homelab 2026-10-01 21:21, normandy
2026-10-01 20:46), so every full converge saw a byte difference while the
parsed content was equal. Fix: the template now renders the whole document
with the same `to_nice_json` and no trailing newline, so both writers produce
identical bytes for identical content. No host was written; the files already
had the new form.

**Drift manifest on public-api: the same bug, the other way round.** public-api
had last been written by a full converge, so once the template changed, its
2-space file became the one that differed (check: `changed=1`, JSON equal).
Fixed by rewriting that one file in place, content unchanged, with no stack
touched:

```
scp public-api:.local/share/homelab-infra/drift-manifest.json /tmp/e5-pa-manifest.json
ansible public-api -m ansible.builtin.copy -a "content={{ lookup('file', '/tmp/e5-pa-manifest.json') | from_json | to_nice_json }} dest=/home/igor/.local/share/homelab-infra/drift-manifest.json mode=0644" --diff
```

**Drift checker on public-api: real drift.** The repository's
`homelab-drift-check` gained the UpKeeper integration in commit fca2469
("Include UpKeeper fleet drift in Normandy reports") and 9d4a425, which were
deployed to normandy by the narrow `playbooks/upkeeper-drift.yml` only;
public-api kept the 2026-09-06 copy (sha256 fd72d28e..., repository
dbbab855...). Without the marker file the new checker behaves as the old one
did. Fixed by copying only the checker, not converging the role (whose
manifest task would have written an empty stack list outside a stack run):

```
ansible public-api -m ansible.builtin.copy -a "src=roles/drift_check/files/homelab-drift-check dest=/home/igor/.local/bin/homelab-drift-check mode=0755"   # --check first: one diff, then CHANGED
```

Nothing on homelab was written, restarted or converged for any of the three.

## Final check (13:27)

```
ansible-playbook playbooks/site.yml --check --diff
blackpearl : ok=20  changed=0 unreachable=0 failed=0 skipped=13
homelab    : ok=386 changed=0 unreachable=0 failed=0 skipped=160
normandy   : ok=67  changed=0 unreachable=0 failed=0 skipped=16
public-api : ok=25  changed=0 unreachable=0 failed=0 skipped=24

ansible-playbook playbooks/base.yml --check
homelab ok=13, normandy ok=6, proxmox-a ok=11, proxmox-b ok=7, changed=0 on every host
```

Answer: changed=0 on every host in both playbooks.

## The new playbook

`ansible-playbook playbooks/base.yml --check --diff` after the final apply
(13:14): changed=0 on every host (homelab ok=13, normandy ok=6, proxmox-a
ok=11, proxmox-b ok=7; blackpearl and public-api skip everything).

Related: [2026-10-02-smart-collection.md](2026-10-02-smart-collection.md),
[2026-10-02-host-metrics.md](2026-10-02-host-metrics.md).

## Implications for later phases

`site.yml --check` with changed=0 is now a usable gate. A change to
`roles/drift_check/files/homelab-drift-check` reaches public-api only through
a full converge of that host; a narrow playbook like `upkeeper-drift.yml` has
to name every host that runs the checker, or the gate turns red again.
