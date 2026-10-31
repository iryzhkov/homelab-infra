# Emby operator credential provisioning

`playbooks/emby-operator-credential.yml` is the maintained, explicitly opted-in
producer of the protected credential that the existing operator entry
`scripts/emby-operator-recommendations.py` already loads. It lets that entry run
unattended without a per-run password card. It adds no authentication loader,
token input, account, global key, service, timer, scheduler, API bridge, sudo
grant, automatic request executor or MCP writer, and it gives no agent or LLM
access to the credential. Nothing in `site.yml` selects it.

## Scope

The playbook acts only on host `homelab` and only on the retained Igor operator
root `/var/lib/holonet-operator/igor-pr116-retain-20261031-next`. Every value is
a literal in `scripts/emby-operator-credential.py`; no argument or variable
selects a host, path, identity, interpreter or command.

The root must already hold, directly inside it:

| Name | Required custody |
| --- | --- |
| `config.json` | root-owned regular single-link 0600 operator config |
| `credential.json` | absent, or root-owned regular single-link 0600 |
| `journal/` | root-owned 0700 directory, never opened or listed |

The root itself must be root-owned 0700, and every ancestor from `/` must be
root-owned without group or world write. Each component is opened with
`O_NOFOLLOW`. The config must have exactly the existing keys, with these pinned
values; only `enabled` may vary:

| Field | Pinned value |
| --- | --- |
| origin | `https://emby.ryzhkov.dev` |
| server | `3c86e6d7ff6c4a3296522cdf03d22bb0` |
| actor, recipient | `545014d1c9d1416b8e0b5a445d6ff9ec` (Igor managing his own Home) |
| library | `3` |
| library_guid | `c4ba438d95214fc2b7efba5bd9929b28` |
| recipe | `igor-movies-retain116` |
| credential_file | `<root>/credential.json` |
| journal_directory | `<root>/journal` |

If the host layout differs, for example a config in a subdirectory, the check
refuses before any secret is loaded. Change the constants in one reviewed source
change; never move host files to fit.

The `source/` directory and the journal are never opened, read, hashed, locked
or changed. Symlinks, hard links, wrong owners, wrong modes and leftover staging
files are refused, never repaired or adopted.

## Secret source

The credential is Igor's existing Emby login, kept encrypted at rest in the
dedicated file `secrets/emby-operator.sops.yaml`. The repository's `.sops.yaml`
creation rule already age-encrypts every `secrets/*.sops.yaml`. This source
change does not create the file. Its decrypted shape is exactly:

```yaml
username: igor
password: <Igor's current Emby password>
```

Create or edit it only with `sops secrets/emby-operator.sops.yaml`, so plaintext
never lands in the working tree. Any other key, any other username, an empty
password, a password over 1024 characters or one with control characters is
refused. The playbook loads it with `community.sops.load_vars` under `no_log`,
the same pattern as `playbooks/authentik-holonet-catalog.yml`, and only when an
action is applied outside check mode.

## Actions

The default action is `check`. It reads only the config and file metadata. It
never decrypts the secret, never reads standard input and never changes the
host. It reports `config_enabled`, whether the credential is present, and a
`credential_id` made of device, inode and change time. The id never depends on
the credential's contents.

```sh
ansible-playbook playbooks/emby-operator-credential.yml
```

A mutating action without `emby_operator_credential_apply=true` is a preview: it
runs the same metadata check plus that action's preconditions and reports
`would_change`. Ansible check mode always stays a preview.

Every run first executes the secret-free check for the selected action on
homelab. The secret is decrypted only after that check passes, then validated
again on the controller, and finally sent to the helper on standard input. It
never appears in argv, output, diffs, logs or backups. The helper prints only
the metadata above, and refusals carry no paths or credential data.

### Provision

```sh
ansible-playbook playbooks/emby-operator-credential.yml \
  -e emby_operator_credential_action=provision -e emby_operator_credential_apply=true
```

This requires `enabled: false` and an absent credential. The helper writes a new
0600 staging file with `O_EXCL`, fsyncs it, links it to `credential.json`
without replacing anything, removes the staging name and fsyncs the root. Only
then does it set `enabled` to `true`. That rewrite changes only the literal of
the enable flag, keeps every other config byte, and replaces the config
atomically. Immediately before the replacement it requires the config to be the
same inode with the same bytes it surveyed.

Any failure before the config replacement removes only the credential inode
this run created and leaves the operator disabled. A failure after the
replacement leaves the intended enabled state. Run `check` to confirm it.

### Rotate

Change the password in Emby, update the SOPS file, run `check`, review it, then:

```sh
ansible-playbook playbooks/emby-operator-credential.yml \
  -e emby_operator_credential_action=rotate \
  -e emby_operator_credential_expect=<credential_id from check> \
  -e emby_operator_credential_apply=true
```

Rotation requires the installed credential to still have the reviewed id. It
writes a new staging file, checks the id again and atomically renames the new
file over the old one. The enable state is preserved. No copy of the old
credential is kept. Between the Emby password change and rotation the operator
fails authentication and refuses, which is the safe direction.

### Retire

```sh
ansible-playbook playbooks/emby-operator-credential.yml \
  -e emby_operator_credential_action=retire \
  -e emby_operator_credential_expect=<credential_id from check, or absent> \
  -e emby_operator_credential_apply=true
```

Retirement first sets `enabled` to `false`, then unlinks `credential.json`
after checking the reviewed id again. If the unlink fails, the operator remains
disabled, and a fresh check and retire completes the job. Retiring an already
disabled root with no credential changes nothing. Retirement never touches the
journal, source or any other name.

Concurrent provisioning runs are excluded by a nonblocking lock on the held root
directory. The operator journal lock is not taken: the operator reads the
credential once per invocation, and each credential change is a single atomic
rename or link.

## Unattended invocation

After provisioning, unattended management uses the existing protected entry and
nothing else:

```sh
python3 <root>/source/emby-operator-recommendations.py --config <root>/config.json OPERATION [--request-file PATH]
```

It must run as root on homelab. The entry's custody, identity, journal and
mutation rules in `docs/emby-operator-recommendations.md` apply unchanged. This
change schedules nothing and exposes no new caller.

## Qualification boundaries

The synthetic tests in `scripts/test_emby_operator_credential.py` exercise the
helper's real filesystem behavior inside an isolated scratch HOME, using a
scratch walk anchor and the test user's uid in place of root. They also check
that the installed files satisfy the existing operator loaders and that the
playbook orders the secret-free check before decryption. They use no real
secret, host or HTTP. Installing a real credential, and the live authenticated
session that results, need separate deployment evidence.
