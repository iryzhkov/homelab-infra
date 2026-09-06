# Drift check

A repository that describes the fleet is only useful while it stays true. The
five stale things this repository found on its first day — a dead Immich URL, an
Ollama endpoint that had become a whisper server, notifications posted to a
closed port, a 502 on the Traefik dashboard, secrets in a Compose file — all
came from the same place: someone changed something, and nothing noticed.

So homelab runs a daily check that compares itself against what was last
deployed to it, and reports into the fleet feed when it does not match.

## What it checks

`~/.local/bin/homelab-drift-check` reads a manifest that Ansible writes at
deploy time, and for every stack on that host verifies:

- the stack directory still exists
- `docker-compose.yml` has the checksum it had when it was deployed
- the env file exists, is mode 0600, and holds exactly the expected variable
  **names**
- every file listed in `config_templates` is present
- the Compose project is running

It also lists projects that are running but appear in no manifest, which is how
an unmanaged stack looks from here.

## What it deliberately cannot check

**Values.** The manifest carries checksums and variable names, never values,
because homelab holds no age key and can decrypt nothing. A Compose file edited
in place is caught; a password changed by hand on the host is not.

**The repository.** The manifest describes the last deploy, not the current
`main`. A change committed and never applied is invisible here — that is what
`ansible-playbook playbooks/site.yml --check` from the laptop is for. The two
halves are complementary: this one watches the hosts, that one watches the gap
between the repository and the hosts.

**The other three hosts.** The check is per-host by design: each host compares
itself against its own manifest, rather than one host reaching out to inspect
the others. The role is host-agnostic, so covering another host is one
`drift_check_enabled: true` in its `host_vars`.

Coverage as of 2026-09-06:

| Host | Checked | Reports |
|---|---|---|
| homelab | itself, 32 stacks | its own findings and public-api's |
| normandy | itself, 3 stacks | its own timer, its own feed post |
| public-api | itself, 2 stacks | via homelab over SSH |
| Black Pearl | not yet | — |

public-api does not report for itself on purpose. Its VLAN is not permitted to
reach the feed on homelab, and opening that path so the edge proxy could write
into a LAN service would be a worse trade than having homelab pull the answer.
It therefore has the checker and its manifest but no timer, no feed token, and
no feed configuration; `drift_check_reports: false` in its host_vars says so.

Black Pearl needs `loginctl enable-linger` and homelab-cli before it can join,
and homelab holds no key to it.

## Reporting

`homelab-drift-report` runs the check and decides what deserves attention:

| Result | Feed |
|---|---|
| clean | nothing — a daily "no change" entry teaches the reader to skip the feed |
| drift found | `warn`, with the findings as the body |
| check could not run | `fail` — silence from a broken checker looks exactly like silence from a clean host |

The systemd unit treats exit 1 as success on purpose. Drift is already reported
to the feed; failing the unit too would put the same finding in two places and
make `systemctl --user --failed` a second thing to remember to read.

## Verifying it works

Tested end to end on 2026-09-06 rather than assumed:

```sh
ssh homelab 'echo "# drift test" >> ~/app/kiwix/docker-compose.yml'
ssh homelab 'homelab-drift-report'      # 1 finding, posted to the feed as warn
ansible-playbook playbooks/site.yml -l homelab   # repairs it
ssh homelab 'homelab-drift-check'       # 32 stacks match
```

The timer runs at 08:30 with a ten-minute jitter, `Persistent=true` so a check
missed while the VM was down runs on the way back up.

## Two traps worth knowing

The manifest's checksum is taken from the file **as deployed**, with a `stat`
on the host, not from the repository's copy. Ansible's `file` lookup strips a
trailing newline, so hashing what it returns matches neither side, and the
first version of this check cheerfully reported all 31 stacks as edited.

The manifest carries no timestamp either. Embedding one made every converge
report a change, which is exactly the kind of permanent noise that teaches
people to stop reading `changed=`. The file's mtime is the deploy time, and it
only moves when the deployed shape actually moves.
