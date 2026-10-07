# Remote NAS mount

The ANZ NAS is reached over the UniFi mesh, with its address declared once as
`anz-nas` in inventory. Homelab and normandy mount `anz_business` at
`/mnt/anz_business` and `anz` at `/mnt/anz`, using the same account.
The dedicated playbook changes only these mount clients.

## Enroll and deploy

From an interactive terminal on the Ansible controller with its keyring unlocked:

```sh
python3 scripts/enroll-smb-secret.py --server 192.168.55.55 --share anz_business --username igor --gui
ansible-playbook playbooks/remote-nas.yml --check --diff
ansible-playbook playbooks/remote-nas.yml
```

Enrollment opens a hidden password prompt for username `igor` on gaming-pc.
Omit `--gui` for a terminal prompt; use `--domain` if the account needs a domain. They
are stored together as JSON in Secret Service with attributes
`service=smb-mount server=192.168.55.55 share=anz_business`.
No password is passed in command-line arguments or committed to the repository.
Ansible reads this controller entry with secret-handling tasks marked
`no_log`, including during check mode.

Each client installs `cifs-utils` and encrypts its credential using
`systemd-creds --with-key=host`. Only ciphertext is persisted in
`/etc/credstore.encrypted/anz-business` and `/etc/credstore.encrypted/anz`,
root-owned and mode 0600. Systemd
decrypts it into its credential directory and prepares a root-only credential
file under `/run/anz-business` or `/run/anz` when the mount is requested. Managed hosts need
neither a desktop session nor an unlocked user keyring after reboot.

Host-key encryption allows unattended boot; root on that host can decrypt it.
It does not protect against compromise of the host or a backup containing both
the ciphertext and the host key under `/var/lib/systemd`. Enroll again and
redeploy when rebuilding a host rather than copying its ciphertext alone.

## Behavior and verification

The automount is enabled at boot, but the VPN and NAS need not be available to
boot. Access triggers a mount with a 30-second mount timeout. After ten minutes
idle, systemd can unmount it; the next access mounts it again. An SMB connection
interrupted during I/O can still wait for kernel/network timeouts.

SMB negotiates its default modern dialect. Local access is restricted to UID
and GID 1000 (the primary user on both clients), plus root; server share
permissions still apply. The unmounted underlying directory is inaccessible
to ordinary users. Nothing changes existing NFS mounts or application binds.

After deployment, verify without listing business filenames:

```sh
ssh homelab 'timeout 35 stat /mnt/anz_business >/dev/null && findmnt -t cifs /mnt/anz_business'
ssh normandy 'timeout 35 stat /mnt/anz_business >/dev/null && findmnt -t cifs /mnt/anz_business'
ansible-playbook playbooks/remote-nas.yml --check
```

For password rotation, rerun enrollment and deployment. Changed credentials
stop the existing mount and credential service, and the next access uses the
new account. Schedule rotation when the share is idle; active use can prevent
unmounting. Changed unit settings stop the affected mount/automount, reload systemd, and
restart the automount so the next access uses those settings.

## Remove

On each client, stop and disable `mnt-anz_business.automount`, stop
`mnt-anz_business.mount` and `anz-business-credentials.service`, remove those
three unit files and `/etc/credstore.encrypted/anz-business`, and run
`systemctl daemon-reload`. Delete the matching Secret Service entry on the
controller if it is no longer needed. Repeat unit and ciphertext removal for
`mnt-anz.automount`, `mnt-anz.mount`, `anz-credentials.service` and
`/etc/credstore.encrypted/anz`.

## Verified 2026-10-05

The SMB account `igor` authenticated to both shares from homelab and normandy.
Directory reads and a temporary file write/read/delete passed as each host's
UID 1000. Both automount units were enabled for boot. The temporary verification
files were removed; business filenames and contents were not printed.
