# qBittorrent source packaging

Source support only. No inventory enables it. The endpoint, issuer and native consumer require separate explicit opt-ins and coordinated review.

The admitted client is source `06b7162d865b89c36385705c0583dab4b043e939`, interface `d07c64a5f291e7c0e47e5ebb9f25bf3fcb8f6eb2`, release `holonet-qbit-06b7162`, binary SHA256 `d282c09f241cf95df9c37baa2ab818d757e1ddc8bc0d0bd4fdd848f7dc91cef2`. Historical artifacts remain admissible without torrent keys. Even null torrent keys refuse old builds. Worker modules and their pins remain unchanged.

## Endpoint binding

Alongside the existing Janitor and custody inputs, explicitly pair `janitor.torrent_source=qbit_inventory_v1` and `janitor.torrent_resource_id=homelab-qbit-movies` with endpoint `torrents.descriptor`. The exact descriptor is:

```json
{
  "version": 1,
  "resource_id": "homelab-qbit-movies",
  "origin": "https://torrent.ryzhkov.dev",
  "expected_version": "v5.3.0",
  "credential_file": "/etc/holonet-playlists/qbit-auth.json",
  "categories": ["radarr-movies"],
  "payload_prefixes": ["/mnt/shared/downloads/complete/movies"]
}
```

This renders the fixed root0600 descriptor at `/etc/holonet-playlists/qbit.json` and independent root `janitor_torrents={target_library:"3",revision,config}`. Add only `janitor.torrents.read` to the endpoint action ceiling. Existing UID940, worker socket and sandbox remain in use. Custody, pause and recipient semantics do not change.

The existing protected root policy directory must already exist with mode0700. Preflight checks root ownership, every ancestor, regular single-link files and exact0600 without opening installed auth. Existing auth is preserved. Missing auth refuses unless strict boolean `web_mcp_install_qbit_auth=true` is explicit. Only that missing-file path loads the fixed existing `secrets/blackpearl-torrent.sops.yaml` privately and renders its `QBITTORRENT_USER/PASS` as exactly `username,password`. SOPS load and secret copy use no_log; copy diff is disabled. Auth and descriptor precede root policy installation. No browser, Arr DB or alternate secret path is supported.

## Existing writer projection

Strict boolean `holonet_catalog_torrents_enabled` defaults false. The isolated entrypoint adds optional nested `holonet_torrents={library:"3",actions:["janitor.torrents.read"]}` to the existing managed recommendation writer and adds the application scope `holonet.torrents`. Fresh public baseline and existing-owned account/token gates apply. Enrollment refuses missing ownership, a missing writer/token, or changed token expiry. It never creates or rotates a writer for this grant.

The exact request scopes `holonet.read holonet.janitor holonet.torrents` emit one flat managed-janitor Movies grant with only `janitor.torrents.read`, empty catalog/history/targets. All historical responses strip nested metadata. The old exact two-scope Janitor request still emits precisely `janitor.importers.read,janitor.custody.read`. Mixed, duplicate, partial or extra scopes refuse.

Rollback disables the Go torrent source first, then removes the nested issuer grant and root torrent block. Retain owned application mappings during inverse enrollment, as for the old Janitor mode, so the ownership manifest does not silently lose objects. Account identity, app password, generation and expiry stay unchanged.

## Pending activation evidence

Source acceptance does not qualify live credentials, TLS, trusted-IP/proxy bypass behavior, actual version/category/path, or network/storage topology. Root still owns independent integration review and the live auth/TLS/trusted-IP/path/topology packet. Application-reported paths never establish filesystem or seed safety. The existing full-admin password has no claimed server-side read-only restriction; the fixed worker methods supply the narrow read boundary. Login can evict another session and cancellation can leave this call's session until the configured timeout.
