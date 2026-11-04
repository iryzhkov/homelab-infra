# Optional custody group read

Igor approved optional existing-group read support, initially Radarr only, following jocasta:1c9fd241f356a004e4cadc1e2140f52d@1. This source slice does not activate it.

The endpoint role defaults `web_mcp_custody_group_read` to false. A strict boolean true requires a HoloNet endpoint with an operator configured and adds only `SupplementaryGroups=988` to the existing root worker. The ordinary endpoint service is unchanged. Empty capability sets, ProtectSystem=strict and narrow journal write paths remain intact. It does not chmod or copy any XML file.

A root-owned Radarr importer descriptor may independently set `app_config_read_gid: 988`, only with owner UID1002. This permits a regular single-link0640 XML file with exact UID1002/GID988. Without this field, existing0400/0600 behavior is unchanged. Wrong groups, executable/world permissions, writable ancestors and symlink traversal still refuse. The field is not caller-selectable. Sonarr group opt-in is refused in this slice.

The existing app-owned XML contains an instance-admin API key. Fixed native GET routes enforce read-only behavior; the key itself is not read-only. Supplementary group access is broader than a single media file, and the root-owned policy and fixed mappings still limit application reads. No read-only Arr key, per-target kernel fence or immutable NFS snapshot is claimed.

Native activation must separately verify the real non-symlink path, owner/group, metadata and actual access in the hardened worker. `/srv/swarm` is an alias and must not be used as a credential path. App replacement may reset mode or identity, which must fail closed; do not add a repair loop. NFS supplementary-group/root-squash behavior is unqualified.

The canonical installer now accepts a paired runtime janitor binding and root custody metadata only on the exact custody client/worker build pair. It stages the fixed private Radarr descriptor, generates only importer/custody policy blocks, and creates a private janitor journal without modifying its records. Existing keys are never staged or copied. See docs/holonet-custody-installer.md. A published matching binary, signed read-action enrollment, exact live recycle mapping and bounded native acceptance remain successor work. No live policy, key permission, account/group membership, mount or capability is changed here. Pause/removal stay off. Rollback removes the role opt-in and descriptor field before an old reader, preserving journal records and original credentials.
