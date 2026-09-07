# The offsite bundle

Every copy of the things that matter most was inside one building. The SOPS age
key was on the laptop and on omarchy-pc. The rclone credentials that reach
Backblaze were on homelab. The database snapshots were on homelab and in a
bucket those credentials are needed to open. One fire takes all of it, and the
193 GB in Backblaze becomes unreadable.

The phone leaves the house every day, which makes it the only genuinely offsite
thing in the estate. The bundle is 11 MB. The phone has 70 GB free.

## What is in it

| Item | Why |
|---|---|
| `sops-age-key.txt.age` | unlocks every secret in this repository |
| `rclone.conf` | Backblaze credentials, to reach the rest of the backups |
| `homelab-backup-env` | the InfluxDB admin token the backup jobs use |
| `databases/` | newest snapshot of each small database, and the newest Authentik and Miniflux dumps |
| `homelab-infra.bundle` | a git bundle of this repository, so a rebuild does not depend on reaching GitHub with credentials that are themselves in here |
| `nebula-ca.tar.gz.age` | the Nebula certificate authority, current and rotated: without it no new mesh node can be issued a certificate |
| `RECOVERY.md` | the restore order, written for whoever or whatever is doing the rebuilding |

Deliberately not in it: anything large. Immich's library, the media, the Emby
metadata. This is the set that makes the rest recoverable, not a copy of the
rest.

## How it is protected

The bundle is encrypted to a **recovery age key** whose private half is on no
machine in this house. It belongs in Vaultwarden and on paper. A stolen phone
yields ciphertext.

The SOPS key gets a second layer: it is encrypted to the same recovery key **on
the laptop**, before it travels, so homelab — which builds the bundle — never
holds the plaintext key. That is why the bundle's manifest lists
`sops-age-key.txt.age` rather than `sops-age-key.txt`.

Recipient: `age1us6n6ky6yf6fz084hhw35rzd3xnsra5fazt9dgmc7lqsfhvwqupqpu74ju`.

## How it gets there

`homelab-offsite-bundle` on homelab, twice a day at 07:15 and 21:15. It stages
into `/mnt/backup/offsite` on the NAS, then copies to **every** target that
answers rather than the first one — they fail independently and for different
reasons:

| Target | Where | Keeps | Reachable |
|---|---|---|---|
| `oci` | the lighthouse VM in San Jose | 7 | always |
| `phone` | S25 Ultra, over the mesh | 7 | when awake and on the mesh |
| `travel-router` | GL-MT3000, over the mesh | 2 | when travelling and powered |

OCI is the dependable one: always online and genuinely elsewhere. The phone is
the one that leaves the house daily. The travel router keeps two rather than
seven because it has 126 MB of internal flash and its NAND is not meant for
this.

homelab reaches the phone as `phone`, an alias the `fleet_ssh` role writes into
its SSH config from the inventory. The key it uses is authorised on the phone
with `restrict,from="10.42.0.2"`, so it cannot be used from anywhere else and
cannot open a shell beyond the copy.

A single missed delivery is normal and silent: the phone is asleep, or off the
mesh. Three days without one is reported to the feed and pushed to the phone,
because at that point the only offsite copy of the keys is in a bucket those
keys are needed to open.

## Verified, not assumed

On 2026-09-06 the bundle was pulled back off the phone and taken apart:

- decrypted with the recovery key
- `sops-age-key.txt.age` decrypted to a key whose public half matches the
  recipient in `.sops.yaml`, so it does open this repository's secrets
- `homelab-infra.bundle` cloned to a working repository with all 25 commits
- an Actual database gunzipped and passed `PRAGMA integrity_check`
- `rclone.conf` contained the `[b2]` remote

Two failures found on the way, both now guarded: `age` was not installed on
homelab, and the script happily reported "staged 0 bytes" when encryption had
failed. It now checks for `age` up front and refuses to keep a bundle under
50 KB, which cannot possibly hold a key, credentials and a repository.

## The runbook inside

`RECOVERY.md` is generated with each bundle and lists its own contents. It is
written for someone — or an agent — starting from nothing: how to decrypt,
which order to restore in, and where the parts that are deliberately absent
live. Its restore order is SOPS key, then repository, then Backblaze access,
then the mesh if hosts are being rebuilt, then the services.

## What this does not replace

Backblaze. The bundle is small on purpose; it holds the keys to the kingdom,
not the kingdom. Restoring the photo library still means B2 and a long
afternoon.
