# Automatic recovery of stale NAS mounts

Deploy on homelab with `ansible-playbook playbooks/nas-recovery.yml --diff`.
The role installs a systemd timer that runs 90 seconds after boot and 60 seconds
after the last check. Inspect with `systemctl status nas-recovery.timer` and
`journalctl -u nas-recovery.service`. Run a check immediately with
`sudo systemctl start nas-recovery.service`.

The helper handles the three approved mounts in /etc/nas-recovery.json. It
leaves healthy mounts alone. A confirmed ESTALE or missing mount triggers a
fresh mount of the fstab export at a staging path, with the existing options.
An unreachable export, missing container bind source, unexpected filesystem,
or busy unmount prevents replacement. Other read errors are reported without
remounting. There is no forced or lazy unmount.

Before replacing the host mount, the helper records currently running local
Docker containers whose bind sources lie beneath it. Once every NAS binding
for a recorded container is accessible, Docker restarts it to refresh the
private mount namespace. This includes local Swarm task containers without
scaling a service or changing another host. Stopped containers and replaced
task IDs are skipped. Failed restart requests are retried while the container
is still running. Pending work persists in /var/lib/nas-recovery/pending.json.
The helper never scans or deletes library items.

Each invocation is serialized by a file lock. Subprocesses have timeouts and
systemd limits an invocation to ten minutes. During a NAS outage, preflight
fails and the next timer invocation tries again. A failed replacement mount
is retried as a missing mount on the next invocation. A normal mount swap
briefly exposes the underlying directory to host processes; Docker consumers
retain their old bind until their restart. Existing host backup mount checks
must remain in place. This does not repair NAS hardware or prevent export
handles changing; it recovers the client once the NAS is usable.

Disable with `sudo systemctl disable --now nas-recovery.timer`. Stop an
in-progress check separately with `sudo systemctl stop nas-recovery.service`.
The playbook changes neither fstab options nor other hosts.

Fault-injection checks run through Chaldea:
`chaldea exec --host agent-a -- python3 scripts/test_nas_recovery.py`.
Use a scratch HOME and TMPDIR when running tests, as required by fleet policy.
The checks simulate stale handles, preflight failure, missing bind sources,
busy mounts, restart retries, stopped consumers, and healthy no-op behavior.
They do not interrupt the live NAS.
