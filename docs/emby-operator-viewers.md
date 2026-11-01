# Emby operator configuration for additional viewers

`playbooks/emby-operator-viewers.yml` runs `scripts/emby-operator-viewers.py` on
homelab to create the protected per-recipient configuration that lets the
existing operator entry manage owned Movie playlists and Home rows for six more
Emby users. It adds no principal, credential, grant, store, service, timer or
MCP writer. WebMCP stays read-only. Nothing in `site.yml` selects the playbook.

## Fixed cohort

| Name | Emby user ID |
| --- | --- |
| kostya | db2f3bfb1f84438ebcde94f228183011 |
| lisa | 313e39bb4d8c4b92b81d4b170c3f1b8e |
| oleg | c06c7d2c3e224d8ba2e466efa5bcb97b |
| tanya | 22703968ab0b48eebb4faf7c1d16262c |
| tetiana | e05efa68303043b893b0f5c2e200ccd7 |
| tv | b8f4f6de49b443f1948cf0118679e863 |

Igor (545014d1c9d1416b8e0b5a445d6ff9ec) keeps the retained root
`/var/lib/holonet-operator/igor-pr116-retain-20261031-next` unchanged. The
`holonet-catalog-movies` service account (54b7d36cef804d3c8478474553ed3a82) is
excluded and keeps its existing reader authority. The helper refuses to run if
the cohort contains either ID, a duplicate or a malformed ID. Changing the
cohort is a source change.

## Layout

Everything lives below `/var/lib/holonet-operator/viewers-20261101`:

```text
viewers-20261101/            root 0700
  <name>/                    root 0700
    config.json              root 0600
    journal/                 root 0700, empty until the operator's init
```

Each config has the same fields as the Igor config. Origin, server, actor
(Igor, the administrative creator), library `3` and library GUID equal the
Igor config's pinned values. `recipient` is the cohort ID, `recipe` is
`<name>-movies` and `journal_directory` is the recipient's own journal
directory. `credential_file` is the existing Igor operator credential path;
the helper writes it only as a string. It never opens, stats, copies, hashes,
rotates or removes the credential, and never opens anything inside a journal
directory. Credential rotation therefore applies to every recipient at once,
and retiring the credential makes every recipient refuse at authentication.

## Actions

```sh
ansible-playbook playbooks/emby-operator-viewers.yml                       # check
ansible-playbook playbooks/emby-operator-viewers.yml -e emby_operator_viewers_action=provision
ansible-playbook playbooks/emby-operator-viewers.yml -e emby_operator_viewers_action=provision \
  -e '{"emby_operator_viewers_apply": true}'
```

`emby_operator_viewers_recipient=<name>` limits an action to one cohort member.
Without `--apply` every action is a metadata-only preview that reports each
recipient's directory, journal and config state and whether the action would
change anything.

- `provision` creates only missing names, in order: root, recipient directory,
  journal directory and config, which is created enabled. A config is written
  to a staging name, fsynced and linked into place, so an existing config is
  never replaced. Existing names must already be safe and exactly pinned. An
  existing journal directory is kept as it is, and an existing disabled config
  stays disabled. A repeated run changes nothing.
- `enable` and `disable` change only the enable literal of existing configs,
  through the same stage-and-rename guard as the Igor credential helper.

Every run first checks the whole cohort. Any unsafe owner, mode, link, symlink,
staging residue or config drift in any member refuses the run before a change;
nothing is repaired. Concurrent mutating runs are excluded by a nonblocking lock
on the held `/var/lib/holonet-operator` directory.

## Recipient rollout

Use the existing entry with the recipient config:

```sh
python3 <igor root>/source/emby-operator-recommendations.py \
  --config /var/lib/holonet-operator/viewers-20261101/<name>/config.json OPERATION [--request-file PATH]
```

First run `init-preview`, then `init` with a new key and the returned
`intent_digest`. This writes the recipient's empty version 2 journal with zero
Emby writes; see "Initialization" in `docs/emby-operator-recommendations.md`.
List management then uses the existing list-scoped operations. A separate
recipient gets the managed playlist through the existing explicit None/Read
grant.

## Qualification boundaries

`scripts/test_emby_operator_viewers.py` runs the helper's real filesystem
behavior below a scratch anchor in an isolated HOME, with the test user's uid
in place of root, and drives init through the real core and adapter over the
loopback recording model with synthetic IDs. It is not runtime evidence. Real
recipient restrictions, the live Read grant, and each recipient's create,
share, row, move and inverse need separate deployment evidence.
