# MCP playlist management through the protected operator

`scripts/emby_operator_socket.py` is a socket-activated root worker that lets
the HoloNet MCP service manage journal-owned Movie playlists and their Home
rows through the same protected operator the CLI uses. The `web_mcp_endpoint` role installs
it only when the HoloNet endpoint config carries a non-null
`playlist_operator`, and starts its socket only with the existing explicit
activation flag; see "Opt-in HoloNet playlist operator management" in
`docs/web-mcp-endpoints.md`. The adopted contract is
`jocasta:8047884faa50daa3df712477b1196fcd@2`.

## Operations and required actions

Every call needs `catalog.read`, `playlist.read` and `home.read`. `show` and
`reconcile` need nothing more. `preview` and `apply` of a list operation need
the extra actions below; the preview and the apply of one operation need the
same set. The table is `LIST_ACTIONS` in the worker, and the arguments are the
core `LIST_OPERATIONS` table that the shared entry also uses.

| List operation | Exact arguments besides `operation` and `list` | Extra actions | Core phases (possible writes) | Apply state |
| --- | --- | --- | --- | --- |
| `create-list` | `title`, `movies` | `playlist.manage`, `home.manage`, `object.share` | create, share, row, title, members | `created` |
| `set-members` | `movies` | `playlist.manage` | members (clear, add) | `members-set` |
| `set-title` | `title` | `home.manage` | title | `title-set` |
| `attach-row` | `title` | `home.manage` | row, title | `attached` |
| `detach-row` | none | `home.manage` | unrow | `detached` |
| `move-row` | `position` | `home.manage` | move | `moved` |
| `delete-list` | none | `playlist.manage`, `home.manage`, `object.delete` | unrow, delete | `deleted` |

`create-list` needs `object.share` because its share phase grants the
recipient read access to the new playlist (`POST /Items/Access`, `Read`). The
core skips that write when the recipient is the actor, but the action is still
required, so the ceiling does not depend on who the recipient is. No other
recipient and no other access level is ever granted. `delete-list` needs
`object.delete` for the owned-playlist delete (`POST /Items/Delete`), after
the core has removed the list's Home row. `list` for `create-list` is the new
list's ID; for every other operation it names an existing journal-owned list.

Init, migrate and downgrade stay on the protected CLI. No request selects a
config, journal, credential, path, URL, backend user, playlist ID or row ID.
`move-row` takes the final normal-view index, never a row ID or a native index.

## Shape of the path

```text
MCP service (UID/GID 940, verifies the JWT)
  -> /run/holonet-playlists/operator.sock AF_UNIX, root:holonet 0660
  -> emby_operator_socket.py (root, one process per connection, Accept=yes)
  -> emby_operator_entry.operator_session  same Journal, adapter, credential, Operator as the CLI
  -> existing per-recipient journal, under its existing nonblocking lock
```

The MCP service is the trusted JWT authenticator. The worker does not verify
tokens. It authenticates the service by `SO_PEERCRED` UID and GID and then
intersects the caller fields the service supplies with its own root-owned
ceiling: exact issuer, single subject, namespace, server, library and catalog
user, the fixed recipient-to-config map, and the list operations and actions
in source. A compromised MCP service can act only inside that ceiling;
it cannot read the admin credentials or reach other Emby routes. The separately opted-in importer inventory can only invoke its two fixed *arr GET routes; the instance-wide admin key stays inside the root worker.

The worker adds no listener beyond the socket unit, no spool, subprocess,
retry, durable queue, store or second journal. Importer reads use a bounded in-process daemon I/O thread so a slow HTTP stream cannot extend the overall read budget; this one-request worker exits after its response. The existing per-recipient journal lock
serializes MCP and manual CLI writes.

## Version 1 wire contract

Frozen fixtures: `docs/emby-operator-mcp-wire-v1.json`. The tests check the
implementation against every request, error, key and response fixture there,
so a Go client can be built from that file.

Framing:

- One request per connection: one compact UTF-8 JSON object with no leading or
  trailing whitespace, then one `\n`. At most 65536 bytes including the newline.
  Nothing may follow it.
- One response: one compact ASCII-escaped JSON object, then `\n`, at most 262144
  bytes including the newline. The worker then shuts down its write side and
  exits.
- The client must keep both directions open until it has read the response.
  Closing or half-closing (`CloseWrite`) the connection before then is
  cancellation.
- The whole request must arrive within 5 seconds of accept.
- A transport failure, timeout or missing response is an unknown outcome, never
  permission to resend automatically.

Request fields, all required, no others:

| Field | Type |
| --- | --- |
| `version` | integer `1` (not `true`, not `1.0`) |
| `caller` | object below; supplied by the verifier, never by tool arguments |
| `recipient` | lowercase 32-hex Emby user ID |
| `deadline_unix_ms` | integer epoch milliseconds, the request context deadline |
| `operation` | `show`, `reconcile`, `preview`, `apply`, `movie-metadata`, `series-seeds`, `recipient-series`, `series-catalog`, `janitor-protections` or `janitor-importers-inventory`; read operations have separate exact callers described below |
| `request` | object below |

`caller` has exactly `issuer`, `subject`, `namespace`, `server`, `library`,
`catalog_user` (strings), `actions` (unique strings, at most 32) and
`expires_at` (integer epoch seconds of the verified token). The service must
already have intersected the token's target users and actions with its own
policy; the worker checks the recipient against its map and the actions
against the table above.

`request` by operation:

```text
show       {}  or  {"list": LIST}
reconcile  {}
preview    {"operation": "create-list", "list": LIST, "title": TITLE, "movies": [MOVIE, ...]}
           {"operation": "set-members", "list": LIST, "movies": [MOVIE, ...]}
           {"operation": "set-title",   "list": LIST, "title": TITLE}
           {"operation": "attach-row",  "list": LIST, "title": TITLE}
           {"operation": "detach-row",  "list": LIST}
           {"operation": "move-row",    "list": LIST, "position": POSITION}
           {"operation": "delete-list", "list": LIST}
apply      the preview fields plus "key": LOGICAL_KEY and "intent_digest": HEX64
```

`LIST`, `MOVIE` and `LOGICAL_KEY` match `[A-Za-z0-9_-]{1,128}`. `movies` holds
0..25 unique Movie IDs in order, `TITLE` 1..256 printable characters, and
`POSITION` a JSON integer 0..99 (never a boolean, string or float). A missing,
extra or wrongly typed field, or an unknown list operation, is `refused` before
any policy file, config, credential or backend access.

Responses:

```text
{"version":1,"ok":true,"result":{...}}
{"version":1,"ok":false,"error":{"code":CODE,"message":TEXT[,"operation":OP][,"list":LIST]}}
```

| Code | Meaning |
| --- | --- |
| `refused` | Malformed frame or request, peer or caller outside policy, missing action, or a core refusal (stale digest, drift, reused key, uncertain pending write). Message is fixed safe text. |
| `busy` | The recipient journal lock is held by another run. Nothing was changed; retry later is safe. |
| `unfinished` | Another request is unfinished on this recipient. `operation` and `list` name it; its key is never shown. |
| `deadline` | The effective deadline passed or the caller disconnected. Use `show` before resubmitting the same key and request. |
| `unavailable` | Worker disabled, policy or recipient config unavailable or inconsistent, or an internal failure. |

Results: `preview` returns `{"request": {...}, "intent_digest": HEX64}`;
`apply` returns `{"state": STATE, "writes": N}` with the apply state from the
table above, or `replayed` for a completed key;
`reconcile` returns the core reconcile object. `show` is described below.

## Janitor importer inventory

Contract jocasta:265b119ea72710eb08d72ec118883eb6@2 adds a default-off read-only operation janitor-importers-inventory. Shared synthetic fixture docs/emby-janitor-importers-wire-v1.json is copied byte-for-byte to HoloNet testdata. The request contains only backend, exactly radarr or sonarr. No write, raw route, caller URL/path/key or media/monitoring/search mutation exists.

Root policy uses its independent optional top-level janitor_importers block with target_library, revision and backends. It does not require or enable janitor/history_users/protected_users. Backends maps only radarr/sonarr to private root config paths. No default key is rendered. The caller has exactly issuer, subject, namespace, server, target_library, actions, expires_at and policy_revision, action only janitor.importers.read. Namespace is actual managed-janitor and root issuer/subject/server/library/revision pins still intersect. The existing recipient field is a routing membership pin only; no recipient Emby config, credentials, history or journal is loaded.

Private backend config has version1, backend, root-asserted resource_id, inventory-derived origin, expected_version, app_config_file and app_config_owner_uid. Origin must be HTTPS with system CA, or loopback-only HTTP; no URL base, redirects, ambient proxies or caller configuration. Credentials are the existing instance-wide full-admin ApiKey in private config.xml, read in place with bounded directory-FD custody checks and strict XML, no copy/helper. Fixed two-route GET code provides read-only least privilege; the key itself is not read-only. App owner and private mode must be verified before activation. The current worker unit has an empty CapabilityBoundingSet and cannot read app-owned0600 XML without DAC override; choosing narrowly scoped readable custody or a capability change is an explicit native activation gate, not implemented here. No capability, group membership or key copy is changed. Unknown custody refuses; no relaxed secret-file path.

Only GET /api/v3/system/status and GET /api/v3/importlist run, within one remaining budget of at most ten seconds and no retry. Status appName and version must match the pinned app, not package/image suffixes. Nonempty UrlBase refuses. Response cap256KiB and32 lists, duplicate/malformed/truncated data refuses rather than empty. Provider selectOptions may exceed this cap, a known safe refusal. No raw provider settings or keys are returned.

Projection returns id, name, implementation and nullable enabled/automatic_add/search_on_add. Radarr maps enabled/enableAuto/searchOnAdd; Sonarr maps absent enabled to null and enableAutomaticAdd/searchForMissingEpisodes. Nonboolean source values remain null. Disabled Radarr plus enableAuto=true is not classified as active. resource_id is root-asserted; server/library are authority pins, and Sonarr reads confer no Series evidence grant. The public random snapshot ID is informational and cannot authorize a later change.

Future removal must re-read current lists and GET /api/v3/config/importlist, qualify listSyncLevel/clean-library effects, preserve records, prevent independent re-download and prove a safe inverse. None is claimed by this inventory. Deployment/installer/profile custody remains unqualified and default off; rendering an origin must reuse inventory addresses. Remove optional config before rollback to old parsers, preserve journals and issuer boundaries. Source vocabulary adds one action, total15 within the existing16-action cap, with no live grant changes.

## Janitor positive protection read

Contract jocasta:4b3d20ee617b7b0f07bffbe17251e428@2 adds only the private read operation janitor-protections. Its exact synthetic wire fixture is docs/emby-janitor-protections-wire-v1.json, copied byte-for-byte to the HoloNet client tests. Existing caller grammars and management namespace checks remain unchanged.

This operation has its own exact caller fields: issuer, subject, namespace, server, target_library, actions, expires_at, history_users and policy_revision. Namespace must be managed-janitor. Issuer, subject and server match the existing root caller pins. The actions are exactly janitor.read, history.read and userdata.read in that order. No catalog_user, catalog.read or signed base catalog claims are required. The request contains only one canonical decimal Movie target.

Presence of an optional root policy janitor block is the sole opt-in. Its exact fields are protected_users, target_library and revision. The cohort must equal the existing recipients and history_users; the scalar target library must equal the existing Movie library; revision is 40 through 64 lowercase hex characters. Validate the whole policy before deployment. Malformed blocks refuse the whole policy; no new block is rendered by default. Missing blocks refuse before recipient configuration, credentials or backend calls. The new caller cannot invoke preview/apply or any playlist operation.

The read reuses the existing peer, socket, deadline, root per-recipient config, credential custody, backend identity preflight and nonblocking journal lock. It never invokes the Operator or saves journal state. Its fixed recipient Items query enables UserData for this method alone, limits to one Movie in the configured library and requests Limit two. The response projects only exact scope and id/kind/protection facts. Boolean true in IsFavorite or Likes yields liked; every nonpositive or unsupported UserData shape yields unknown. A mismatching item, type or optional UserData ItemId refuses. No UserData.UserId is assumed. Empty visibility never proves not-liked. No dates, names, paths, played counters or raw UserData are returned.

The client must retain all incomplete-evidence flags, cap validity by caller expiry and one overall deadline, omit only busy/unavailable members, abort protocol/scope errors and never infer eligibility. Series is deferred until a separate signed Series contract. This slice cannot qualify six-month playback, two-month admission, real notice or removal. It adds no account, signed recipient grant, schedule or live configuration.

Roll out compatible worker source before enabling the optional policy and service evidence_source; verify the complete policy before replacement. For rollback, remove the optional block and service source setting before an old parser, retaining journals. Passing synthetic tests is source evidence, not live native qualification.

## Movie metadata read

`movie-metadata` is the read-only worker operation behind the HoloNet
`playback_statistics` and `recipient_movies` tools (`mcp-holonet/PLAYBACK.md`).
It is off unless the current root policy opts in, and it never changes a user
policy, history, playlist, Home row, media item or the journal.

Request: exactly `{"ids": [ID, ...]}` with 1..25 unique IDs matching
`[A-Za-z0-9_-]{1,128}`. No path, URL, query, field or filter is accepted.

Caller: the existing fields plus `history_users`, 1..16 unique lowercase
32-hex IDs, and `actions` exactly
`["catalog.read", "playlist.read", "home.read", "history.read"]` in that order.
Every other operation keeps the original caller and refuses `history_users`
with `Invalid caller`.

Policy: optional top-level `history_users`, 1..16 unique IDs that are all
keys of `recipients`. Without it every metadata request is refused with
`History read not enabled by policy`; an existing policy therefore stays
default-off. The policy is read fresh on every call. A request is authorized
only when the pinned caller fields match, the recipient is in `recipients`,
the actions are exactly the fixed tuple, every caller history user is a
configured recipient, and the recipient is in both the caller's and the
policy's `history_users`. All of that, the deadline and the ID shape are
checked before the recipient config, the credential or any backend request.

Execution: the worker takes the recipient's existing nonblocking journal lock
(`busy` if held), authenticates the same admin session with the same guard
before every send, and then calls the adapter's `movie_metadata`. It does not
load, migrate, reconcile, initialize or write the journal and needs no
preview. The adapter re-verifies the admin identity and sends one fixed query:

```text
GET /emby/Users/<recipient>/Items?Recursive=true&IncludeItemTypes=Movie&ParentId=<library>
    &Ids=<ids>&Fields=People,Genres&StartIndex=0&Limit=26&EnableUserData=false
```

The recipient-scoped query proves type, library and current visibility.
Unlike `query_movies`, which requires every ID before a write, the read keeps
only the visible subset.

Result: exactly `{"movies": [...]}`, the visible requested Movies in request
order; missing, hidden, Episode and off-library IDs are omitted without
explanation. Each item has `Id`, `Type` (`Movie`), `LibraryId` (the configured
library), `Name`, and only when Emby supplies them `ProductionYear`, `Genres`
and `People` (`{Id, Name, Type}`). A missing or null field is unknown. User
data, paths, media sources, overviews, actor and other credits and any other
user's facts are dropped. Bounds follow the HoloNet catalog projection: names
1..512 UTF-8 bytes, year 1..9999, at most 16 genres, at most 512 raw credits,
credit IDs canonical decimal, credit names and genres at most 128 bytes.
Director and Composer share a budget of 16 and a 17th distinct one refuses;
Producer has its own budget of 16 and later valid Producer credits are
skipped, so at most 32 credits are returned. No other role or alias is mapped.
Any malformed known field, an unrequested or duplicate row, or a non-Movie row
refuses the whole read with no facts.

Bounds: the same 120-second and 512-request transport budget, signed expiry,
caller deadline and cancellation as management, and the unchanged 262144-byte
response cap (`Response too large` otherwise; a full page of 25 maximal Movies
fits). Frozen request, refusal and result fixtures are under `movie_metadata`
in the wire file.

## Series reads and typed Episode lists

Contract `jocasta:c43c23446da086e5157fbca8a5f9977b@4`; frozen fixtures under
`series` in the wire file. Everything here is off unless the root policy and
the recipient config both carry `series_libraries`.

Policy: optional `series_libraries`, `{library ID: {guid, name, category}}` with
1..2 entries, never the Movie `caller.library`. It is the dedicated Series
ceiling, separate from the Movie catalog library. The recipient config must hold
the identical entry for every library a call uses, or the call is `unavailable`
(`Recipient configuration does not match policy`) before credentials or network.

Caller: optional `series_libraries`, 1..2 unique canonical decimal IDs in Go
`sort.Strings` byte order (`["1132","615"]`, not numeric order), placed after
`expires_at` and `history_users`. Management callers (all of `show`,
`reconcile`, `preview`, `apply`) may carry it and must when the writer is opted
in; the field is omitted when the intersection is empty. The three Series reads
require it together with `history_users` and the fixed history action tuple.
`movie-metadata` refuses it (`Invalid caller`). A caller with the field needs a
policy with `series_libraries` (`Series libraries not enabled by policy`) and
every ID in it (`Series libraries outside management policy`).

Typed management: `preview` and `apply` of `create-list` and `set-members` may
carry `items` instead of `movies`, each item exactly `{episode_id, series_id,
library_id}` (create 1..25, set 0..25, unique Episodes and Series). They need
`series_libraries` (`Series libraries required`) covering every item library
(`Item library outside Series grant`). A recipient journal with typed state
refuses every call whose current grant does not cover it, including Movie-only
`show` and `reconcile` (`Typed list state needs current Series grants`). Every typed snapshot rechecks the live library identity, including empty lists,
and proves observed Episode membership and the durable typed request through
recipient-scoped Episode and Series reads, before dispatch and on readback. All typed lists share one Episode read and one Series read per
library per snapshot, at most four reads, without repeating that snapshot's
identity check. Each list still has at most 25 members. This applies to show, reconcile, sibling Movie requests and row or
title operations, including their final dispatch guard. Hidden, reparented or
out-of-library Episodes refuse before facts or writes. Core
rules are in
[emby-operator-recommendations.md](emby-operator-recommendations.md#typed-episode-lists).
Show adds `media_kind: "Episode"` to a typed list and gives its
`playlist.entries` as `{episode_id, series_id, library_id, entry}`; a pending
typed membership step is named `add-episodes`, and `run.args` may hold `items`.

Reads, under the recipient's nonblocking journal lock without loading it:

```text
series-seeds      {"episode_ids": [ID, ...]}              -> {"series": [...]}
recipient-series  {"ids": [SERIES_ID, ...]}               -> {"series": [...]}
series-catalog    {"library_id": ID, "limit": 1..10, ["offset": 0..1000],
                   ["person_id": ID, "person_role": "Director"|"Producer"|"Composer"]}
                                                          -> {"series": [...], "next_offset": N|null}
```

IDs are 1..25 unique canonical decimal strings. A Series has `Id`, `Type`
(`Series`), `Name`, `LibraryId`, `Category` (the configured category of that
exact library), optional `ProductionYear`, `Genres`, `People` bounded as in
`movie-metadata`; seeds add `EpisodeIds`, the others an optional
`Representative` `{Id, Type: "Episode", SeriesId, SeriesName, ParentIndexNumber,
IndexNumber}`. Seeds resolve each Episode through a recipient-scoped Episode
query inside each granted library and its `SeriesId`; invisible or unparented
Episodes are omitted, an Episode in two libraries or a Series name contradiction
refuses. The Representative is the least (season, episode, ID) positive-indexed
non-virtual Episode in one sorted page of 25, proven inside the Series library;
none is guessed beyond that page and no watched state is read. `series-catalog`
returns `next_offset` `offset + limit` while more rows exist and that value is
at most 1000, else null; null at the bound is not a claim of exhaustion. With a
person filter each Series must carry that exact credit after projection, or the
read refuses. The live behavior of `SortBy`, `ParentIndexNumber`, `IndexNumber`,
`PersonIds` and `PersonTypes` is unqualified until root acceptance.

Bounds: the unchanged 120-second and 512-request budget, deadline and
cancellation; a seeds read uses at most 9 requests, a `recipient-series` read at
most 34 and a catalog page at most 17.

## Deadline and cancellation

The worker's deadline is `min(caller.expires_at, deadline_unix_ms, now + 120 s)`
and is never renewed. A request whose deadline has already passed stops before
any file or backend access. The adapter calls the worker's guard before every
backend send, including authentication and reads. The guard stops when the
deadline has passed, the peer closed or half-closed, or extra bytes arrived. It
also caps each send's socket timeout to the remaining time, and the response
read loop refuses after that time.

- A stop before a mutation is sent is the existing `NotDispatched` path: the
  core clears only the pending step and keeps the run, so the identical key
  and request can resume it.
- A stop after a mutation was sent keeps the pending step. Only exact readback
  through a later resubmission of the identical request completes it; `show`
  and `reconcile` never write.

Residual bound: a socket operation that has already started can block for up to
its own timeout, which was capped to the remaining time when it started. The
service unit's `RuntimeMaxSec` is the hard backstop. Killing the worker is
crash-safe for the journal, which saves intent before every send.

## Idempotency

Only `apply` uses a key. The internal key is

```text
"mcp-" + sha256_hex(UTF-8 of compact JSON [issuer, subject, namespace, server, library, recipient, logical_key])
```

with `,` and `:` separators and no ASCII escaping. Operation and list are
deliberately excluded: the core's exact request equality refuses a reused key
with a different request (`Idempotency key reused`) or a different request
while one is unfinished (`unfinished`). The `mcp-` prefix keeps MCP keys apart
from manual CLI keys. A completed key replays with zero writes, after fresh
authorization on every call. A different logical key cannot take over,
expire or reset an unfinished run.

Protected manual recovery of an MCP run uses the CLI with the derived internal
key and the original request from private audit evidence. That is an
exceptional root path, not an MCP operation.

A completed key replays as `replayed` with zero writes for every operation.
Resuming an unfinished run binds the key and the exact request; the
`intent_digest` is checked when the run is first recorded, so a resubmission
carries the original digest unchanged.

## Show

`show` returns the core version 2 show result, with two changes that do not
touch the journal or the CLI's output:

- `run` is `null` or `{"operation", "list", "args"}` of the unfinished request,
  never its key. The worker reads it from the same journal under the lock and
  requires its digest to equal the shown `journal_digest`.
- `capacity_warning` is true once `done_count >= done_limit - 16`. That means
  the next new request first retires the active completed keys into the
  replay archive. It needs no operator action.

`migrated_from` loses its key if present. A recipient without a version 2
journal is refused with `Journal migration required`; initialize it with the
protected CLI first.

`done_limit` stays 64 active completed keys. Before a new run is admitted with
48 or more active keys, the core moves them into the journal's replay archive
under the same lock, with no backend send. Archived keys replay forever with zero
writes, and a changed request under an archived key refuses. Nothing is pruned.
The rules, crash safety and integrity checks are in
[emby-operator-recommendations.md](emby-operator-recommendations.md#replay-archive).
Show adds `archived_count` and `completed_count` (`done_count +
archived_count`). A scheduled caller that derives keys from show uses
`completed_count`, which only grows, never `done_count`, which drops at each
retirement. A missing, unsafe or miscounted archive refuses show, preview and
new requests with nothing changed. It needs protected manual inspection, never
a new journal.

Rollback: older worker modules refuse a journal that has a `replay_archive`
manifest as `Foreign journal`, for show, replay and new requests alike, with no
writes. Rolling the modules back therefore stops management of each recipient
that has retired keys until the new modules return. It never lets an archived
key look new. Never delete the archive directory or its records, and never
downgrade such a journal to version 1; the core refuses that downgrade.

## Examples

MCP tool arguments, as the HoloNet client sends them (`playlists_preview`
returns the `intent_digest` for the identical `playlists_apply`):

```json
{"recipient": "<recipient>", "operation": "create-list", "list": "canary-20261101", "title": "Canary", "movies": ["<movie>"]}
{"recipient": "<recipient>", "operation": "move-row", "list": "canary-20261101", "position": 0,
 "key": "canary-move", "intent_digest": "<digest from preview>"}
{"recipient": "<recipient>", "operation": "delete-list", "list": "canary-20261101",
 "key": "canary-delete", "intent_digest": "<digest from preview>"}
```

A temporary list exercises every new operation and leaves the recipient's
Home and playlists as they were: `create-list`, `detach-row`, `attach-row`,
`move-row`, then `delete-list`. Each step is a fresh preview and an apply with
its own logical key, so it consumes five completed keys.

## Rollout compatibility

Wire version 1 is unchanged; the new operations, arguments and apply states
are additive and the fixture keeps every earlier valid case.

- New worker with the first-slice MCP build: compatible. That build sends only
  `set-members` and `set-title` with the same shapes and refuses every other
  operation before the socket.
- First-slice worker with a full-lifecycle MCP build: the old worker refuses
  the new operations with `Unsupported list operation` before any backend
  access. Nothing is written, but the call fails.
- Deploy the worker modules first, then the MCP build. Never run a worker that
  can complete a new operation behind a client that cannot read its apply
  state: such a client reports an unknown outcome for a completed write.
- The replay archive adds `archived_count` and `completed_count` to the show
  result; `done_count`, `done_limit`, `keys_exhausted` and `capacity_warning`
  keep their names, types and bounds. A client that refuses unknown show fields
  must be updated to accept both before a worker with the archive is activated.
- `movie-metadata` is additive. An older worker refuses it with
  `Invalid caller` before any backend access, so the HoloNet playback tools
  fail closed. A new worker with a policy lacking `history_users` refuses it
  too. Enabling it needs the worker modules, a policy with `history_users`
  rendered by the installer, and a writer grant with `history.read`.
- Series reads, typed items and the caller `series_libraries` field are
  additive. An older worker refuses the field (`Invalid caller`) and the new
  operations (`Unsupported operation`) before any backend access. A new worker
  without `series_libraries` in its policy keeps the old behavior and refuses
  them. Deploy the worker first, then the Series config and policy, then the
  richer writer grant. Once a recipient journal holds a typed list, roll back
  only to a Series-capable worker (Series grants may be removed): older modules
  refuse that journal entirely, and a Movie-only caller refuses it too, both
  without writes.

## Remaining limits

`done_limit` stays 64 active keys per recipient, with older keys kept in the
replay archive and never deleted, `MAX_LISTS` stays 4, a call stays bounded by
120 seconds and 512 backend requests, and the Home snapshot by 100 rows. The
archive grows by one small file per completed key; the filesystem is its only
bound. A worst-case `create-list` (fourth list, separate
recipient, 25 Movies) fits one call in the recorded synthetic model; a slow
backend can still stop it at the deadline, and the identical request then
resumes it. Recommendation history, scheduling, global sharing, Series IDs as
members and duplicate Movies stay out of scope; Episodes enter only as typed
items. This contract makes no claim about Emby
access control beyond the one managed recipient grant.

## Deployed layout

`roles/web_mcp_endpoint` renders everything below from the validated endpoint
config; `filter_plugins/web_mcp.py` pins the values.

Installed source: `emby_operator_socket.py`, `emby_operator_entry.py`,
`emby_operator_adapter.py` and `emby_operator_recommendations.py`, byte for byte
as pinned, in the root-owned `0555` release directory
`/opt/holonet-playlists/releases/<release>`, files `0444`. The release name is the
SHA256 of the sorted `sha256sum`-style manifest of the four pins. The existing
protected CLI and its `source/` directory under the Igor operator root are not
touched; the worker carries its own copy of the same core bytes. A CLI installed
from this source imports `emby_operator_entry.py`, so it needs that file too.

Policy file, fixed path `/etc/holonet-playlists/policy.json`, root-owned `0600`
in a root-owned `0700` directory whose ancestors are root-owned and not group
or world writable. It is read with the same no-follow `private_read` as the
operator config:

```json
{
  "version": 1,
  "enabled": true,
  "peer": {"uid": 940, "gid": 940},
  "caller": {
    "issuer": "<exact issuer of the separate writer identity>",
    "subject": "<exact single writer subject>",
    "namespace": "managed-recommendations",
    "server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
    "library": "3",
    "catalog_user": "54b7d36cef804d3c8478474553ed3a82"
  },
  "recipients": {
    "545014d1c9d1416b8e0b5a445d6ff9ec": "/var/lib/holonet-operator/igor-pr116-retain-20261031-next/config.json",
    "db2f3bfb1f84438ebcde94f228183011": "/var/lib/holonet-operator/viewers-20261101/kostya/config.json",
    "313e39bb4d8c4b92b81d4b170c3f1b8e": "/var/lib/holonet-operator/viewers-20261101/lisa/config.json",
    "c06c7d2c3e224d8ba2e466efa5bcb97b": "/var/lib/holonet-operator/viewers-20261101/oleg/config.json",
    "22703968ab0b48eebb4faf7c1d16262c": "/var/lib/holonet-operator/viewers-20261101/tanya/config.json",
    "e05efa68303043b893b0f5c2e200ccd7": "/var/lib/holonet-operator/viewers-20261101/tetiana/config.json",
    "b8f4f6de49b443f1948cf0118679e863": "/var/lib/holonet-operator/viewers-20261101/tv/config.json"
  }
}
```

The installer writes it from the endpoint config: `caller.issuer` and
`caller.server` are the top-level `issuer` and `server_id`, the other caller
fields come from `playlist_operator`, and `recipients` holds only the selected
IDs, each mapped through the fixed seven-entry table above
(`OPERATOR_RECIPIENTS`). Exactly these fields;
1 to 16 recipients; lowercase 32-hex IDs; absolute, distinct config paths. Each
selected config must be enabled and match its map key as `recipient`, plus the
policy `server` and `library`, or the request is `unavailable`. The policy's
`enabled` is the worker kill switch. The optional `history_users` list (see
[Movie metadata read](#movie-metadata-read)) is rendered by the installer only
when playback is configured, using the explicit server history ceiling.
Without playback it is omitted, so metadata reads remain disabled.

Socket parent: `/run/holonet-playlists`, `root:holonet 0750`, declared by
`/etc/tmpfiles.d/holonet-playlists.conf` so it is recreated at every boot.
`DirectoryMode=` alone would create it `root:root`, which the service cannot
search, so the socket unit asserts the directory exists instead. No unit uses
`RuntimeDirectory=`, which would remove the parent when a worker exits.

Socket unit `holonet-playlists-worker.socket`:
`ListenStream=/run/holonet-playlists/operator.sock`, `Accept=yes`,
`SocketUser=root`, `SocketGroup=holonet` (GID 940), `SocketMode=0660`,
`MaxConnections=4`, `RemoveOnStop=yes`, and no network listener.

Service template requirements (`holonet-playlists-worker@.service`):

- `User=root`, `ExecStart=/usr/bin/python3 -E -s -B <source>/emby_operator_socket.py`
  with no arguments. Do not use `-I`, which drops the script directory from the
  import path. The worker takes fd 3 from `LISTEN_FDS`/`LISTEN_PID`.
- `RuntimeMaxSec=150` (above the 120-second ceiling plus read and write
  bounds), `TasksMax=8`, `MemoryMax=128M`, `LimitNOFILE=64`, `UMask=0077`.
- `NoNewPrivileges=yes`, `CapabilityBoundingSet=` empty, `AmbientCapabilities=`
  empty, `ProtectSystem=strict`, `ReadWritePaths=` exactly the seven journal
  directories, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`,
  `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`,
  `ProtectKernelLogs=yes`, `ProtectControlGroups=yes`, `ProtectClock=yes`,
  `ProtectHostname=yes`, `RestrictSUIDSGID=yes`, `RestrictRealtime=yes`,
  `RestrictNamespaces=yes`, `LockPersonality=yes`,
  `MemoryDenyWriteExecute=yes`, `SystemCallArchitectures=native`,
  `SystemCallFilter=@system-service`. `ReadWritePaths=` names the selected
  recipients' existing `journal/` directories, so they must exist first.
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`: the inherited socket is
  AF_UNIX and the adapter needs HTTPS to the pinned Emby origin. Network
  egress limits (for example `IPAddressAllow=`) depend on the actual origin
  path and are not set.
- Credential, config, policy and source stay read-only through
  `ProtectSystem=strict`; only the journal directories are writable.

The existing `holonet.service` keeps `NoNewPrivileges`, empty capabilities and
read-only system paths. It needs only access to the socket through its GID.

Rollback: remove `playlist_operator` from the endpoint config and re-run the
role, which stops and disables the socket. Release, policy, configs, journals,
pending state and the credential stay in place; never delete a journal or the
shared credential. In an emergency, stopping the socket unit or setting the
policy `enabled` to false has the same effect until the next role run.

## Tests and boundaries

```sh
export HOME="${PWD%/}/.cache/holonet-operator-tests/home" TMPDIR="$HOME/tmp" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR"
python3 scripts/test_emby_operator_socket.py
```

The tests connect over a real abstract-namespace AF_UNIX socket and check the
real `SO_PEERCRED` of the test process, real framing and every frozen fixture.
They run the full create, detach, attach, move and delete lifecycle with replay,
a lost create response resumed only by the identical request, a stop before a
playlist delete resumed by the same request, every missing list action refused
before any backend, and the worst-case fourth-list create within the unchanged
request budget. For `movie-metadata` they check the frozen fixtures, the
visible subset in request order with only allowlisted metadata, unchanged
journal custody bytes and no write, refusals before config, credential or
backend, a held lock as `busy`, cancellation and deadline before each later
send, the request, time and response bounds, and the credit budgets.
They drive the real shared entry, core and adapter against the loopback
recording model from `test_emby_operator_adapter.py`, with synthetic IDs only.
The test user stands in for root, and the policy and config loaders are
replaced, so root file custody, the systemd units, the hardened service's
socket connectivity, the Go client and live Emby behavior are not exercised.
None of this is runtime evidence; the contract's composed proof, maintained
install, pre-activation audit and native acceptance remain required.
