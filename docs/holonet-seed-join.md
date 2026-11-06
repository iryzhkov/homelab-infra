# Stat-only seed/library hardlink pair

Source contract jocasta:c3070dfe196e7f67732749db4b279689@3. Source support only: no inventory enables it, the compatible Go build is pinned but release gates remain, and nothing here enrolls, renders, deploys or calls a live host. The shared wire fixture is [emby-janitor-seed-join-wire-v1.json](emby-janitor-seed-join-wire-v1.json); the CLI mirrors its bytes.

## Worker operation

`janitor-seed-join-stat` is a default-off root worker operation in `scripts/emby_operator_seed_join.py`. It is admitted only for the exact eight-field managed-janitor caller carrying exactly `janitor.seed_join.read`, the policy issuer, subject and server, the root block's library and revision, and a configured recipient. Torrent, importer and custody action tuples are refused for it, and its action is refused for those operations. The request is exactly `{arr_movie_id: 1..2^31-1, torrent_hash: 40 lowercase hex, file_index: 0..255}`; booleans are not integers.

The optional root `janitor_seed_join` block holds `target_library`, `revision`, `library_root` and `seed_root`. It requires the `janitor_torrents` block and a `janitor_importers` block naming `radarr`, with the same library and revision. `seed_root` is exactly `/mnt/shared/downloads/complete/movies` on both sides. `library_root` has host `/mnt/shared/media/movies` and app `/movies` or the host path. Library and seed prefixes are disjoint. When `janitor_custody` names Radarr, its first root must equal `library_root`. The validator does no filesystem I/O.

One call has a 25-second ceiling, capped by token expiry and the caller deadline, with no retry:

1. Three fixed Radarr GETs (`system/status`, `movie/{id}`, `moviefile?movieId={id}`) through the existing importer descriptor, existing XML key custody and ten-second importer budget. Exactly one file row beneath the movie path and the library app root qualifies.
2. The existing qBittorrent adapter, unchanged: at most five calls, its ten-second budget and content cutoff, and logout before it returns. Facts are used only after a confirmed logout, the client reports the torrent complete, the indexed file has progress 1, and its path lies beneath the seed root. A one-file torrent's path must equal `content_path`.
3. Each mapped host path is walked twice from `/` with `O_PATH`, no-follow, directory-FD opens and `fstat` only, with the guard before every open and at most two descriptors held. Mount IDs come from `/proc/self/fdinfo`. No media file is opened for I/O, read or hashed, and no network call happens during this phase.

Results are `linked`, `not_linked` or `unknown` with one closed reason (`source_unqualified`, `missing`, `denied`, `unsafe_path`, `changed`, `incomparable`, `unavailable`). `linked` needs the same device, mount and inode through both names, identical observed metadata, and a link count of at least two. Different inodes on the same device and mount give `not_linked`. Size equality is never evidence. Any drift between the two passes, or a size differing from Radarr or qBittorrent, gives `changed`. Symlinks, non-regular leaves and nested mounts give `unsafe_path`.

`linked` is an instantaneous same-host relation only. It does not mean continuing seeding, durable identity, snapshot atomicity or freed space; removing the library name while the torrent link exists frees nothing. `media_removal_authorized` is always false. Results, errors and logs carry no path, inode, device, link count, time, size, OS error text, cookie or credential.

Caller authorization failures are refused. Backend and filesystem failures return `unknown` with a closed reason. Root configuration failures return the fixed `Seed join unavailable` error, and the deadline or a closed caller returns the existing deadline error.

## Installer and issuer packaging

`filter_plugins/web_mcp.py` pins the new module and the changed socket module, which produces a new worker release digest for every operation. The release is inert without the policy block.

The endpoint pairs `janitor.seed_join_source=stat_pair_v1` with the endpoint input `seed_join.library_root`. The seed root is the fixed constant and is never an input. The key requires the torrent pair and the Radarr importer input, appends `janitor.seed_join.read` last to the endpoint action ceiling, and renders `policy.janitor_seed_join`. Seed-join keys require exactly Go source `ddd1a223b99ee65abec33cf91b01b0c78ae4f688`, worker/interface `c37b45fe27233fe36779fc0741cacefa51331425`, and static Linux/amd64 artifact SHA256 `98369bb77b1d56b4b2ae87709f0860e4123a1ad4777e121c3e0b89384a2505f6` (Chaldea build `b02e84fd-94d4-4491-bfa8-76370db21cc9`, Go1.27.0, CGO disabled). Older builds refuse key presence, including null. Exact pins establish compatibility; CI and artifact publication still gate deployment. This changes no running configuration or enrollment.

Strict boolean `holonet_catalog_seed_join_enabled` defaults false. It requires the torrent opt-in and the existing owned generation-1 writer, applies the fresh-baseline gate, adds nested `holonet_seed_join={library:"3",actions:["janitor.seed_join.read"]}` to the existing writer and appends the `holonet.seedjoin` application scope. It never creates or rotates a writer, and never changes the janitor or torrent grants. The exact request `holonet.read holonet.janitor holonet.seedjoin` emits one flat managed-janitor Movies grant with only that action and empty catalog, history and target users. Mixed, partial, four-scope and duplicate requests refuse. Existing two-scope janitor and three-scope torrent expressions stay byte-identical. No existing token gains the action or scope.

Rollback disables the Go seed-join source first, then removes the nested grant and the root block. Keep the owned `holonet.seedjoin` mapping on the application during inverse enrollment, as for the torrent grant.

## Activation order and open facts

Root integration owns these steps; this change performs none of them:

1. Worker release, inert without the policy block.
2. Compatible Go build, then the three pins above.
3. Endpoint config and root policy block.
4. Issuer enrollment of `holonet.seedjoin`. This is a separate decision for Igor, not implied by the approved filesystem scope.
5. A native client registration requesting the three-scope tuple; its fleet location is not verified.

Still unknown: whether the worker namespace sees the downloads prefix, whether the possibly root-squashed worker can search its ancestors, the current Radarr hardlink setting, and NFS file ID stability. `unknown/denied` or `unknown/missing` from a first canary is an informative answer, not a bug to route around.
