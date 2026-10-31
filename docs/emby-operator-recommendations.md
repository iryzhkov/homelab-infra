# Operator-applied Igor Movie recommendations

The delivered operator base provides one owned Movie playlist, its native Home
row, membership updates, removal and read-only reconciliation through a bounded
authenticated-admin adapter. This batch also adds bounded read-only show. WebMCP stays read-only. Igor accepted
managed playlist UX without strict playlist privacy. Real Igor item restrictions
and credential staging still require operator qualification.

`display-title-preview` and `set-display-title` manage the Home row display title
and remove the exact legacy `operator-provisioned` subtitle, even when the title
is unchanged. New rows have a blank subtitle; other custom subtitles are preserved.
Source tests, release reviews and actual runtime qualification are separate
evidence; a passing recording test does not prove production behavior.

Native playlist item Name rename is unsupported under the Emby 4.10.1.0 metadata
preservation contract. The generic item save can change stored runtime 0 to NULL;
the Series-only setter does not remedy Playlist hydration. A populated fixture
whose baseline was already NULL does not prove preservation for all playlists.
There is no item metadata POST, runtime normalization, payload workaround or
metadata editor in this operator. The playlist Name remains the immutable marker.

## Scope and custody

Use one persistent journal for the exact server, administrative creator, recipient
Igor ID, Movies library and recipe. Keep it through restarts and uncertain writes.
Never reset it to bypass refusal. It stores private IDs, Home snapshots, provenance,
intent and completion keys. No second store or reconciler exists.

The entry requires real and effective UID 0 and an explicitly staged root-owned
config and username/password credential file. Files must be single-link regular
0600 in root-owned 0700 immediate directories. Every ancestor is opened with
O_DIRECTORY and O_NOFOLLOW from `/` and must be root-owned without group/world
write. No environment, native cache, database, global key or broad helper loader
exists. Paths through a user-owned home directory are unsuitable for real staging.
No CLI accepts a credential, token, raw route or arbitrary deletion ID.

`Journal(directory, root_safe=True)` applies the ancestor contract to the journal
and holds its directory FD until close. Lock, load, save, rename, unlink and fsync
use that FD. Renaming or replacing ancestors cannot redirect I/O to a replacement
path. The actor trusts root administrators; this does not defend against a hostile
root modifying the held directory. Journal files and lock are owned 0600 regular
single-link files. Loads and saves require the existing exclusive process lock.
Save validates existing custody, writes an exclusive random temporary file, fsyncs,
atomically replaces JSON and fsyncs the held directory before mutation dispatch.
The default `root_safe=False` exists for unprivileged core tests. The real entry
always requests `root_safe=True`; it never creates or chmods custody directories.

## Entry and protected configuration

Invoke `python3 scripts/emby-operator-recommendations.py --config PATH OPERATION
[--request-file PATH]`. Config and request paths are safe to put in argv; contents
stay in protected files. Results include private scope and Movie IDs. Keep stdout
in operator custody. Errors omit upstream bodies, credentials and tracebacks.

Config has exactly these fields:

| Field | Meaning |
| --- | --- |
| enabled | Explicit boolean enable flag. False refuses before authentication. |
| origin | Pinned existing HTTPS origin, no path/query/fragment/userinfo, default HTTPS port only. Fixed API prefix `/emby`. |
| server, actor, recipient | Lowercase 32-hex exact IDs. Actor is the administrative creator. It may equal Igor for an operator managing its own homepage. In that case the adapter requires explicit CanEditItems=true and CanDelete=true plus journal creation provenance and skips sharing entirely, preserving management rights; a separate recipient still requires the explicit None/Read grant. |
| library | Actual Movies item ID, bounded opaque string. |
| library_guid | Independently pinned virtual-folder GUID, exact lowercase 32-hex returned representation. A different representation refuses locally for qualification. |
| recipe | Immutable bounded opaque recipe ID. |
| credential_file | Explicit absolute root-protected staging file. |
| journal_directory | Existing persistent protected directory. |

Credential JSON has only `username` and `password`, nonempty bounded strings.
Each invocation creates a new adapter and authenticates once via
POST `/Users/AuthenticateByName` with Username/Pw. User.Id, ServerId and explicit
Policy.IsAdministrator=true must match the pinned actor/server. Only the returned
AccessToken is used; it stays in private memory and is cleared on close. Python
strings do not guarantee physical zeroization. Staged credential custody and its
real administrative session remain separately unqualified. No real credential
was requested, read or tested by this author.

Protected request shapes:

| Operation | JSON fields |
| --- | --- |
| preview | `movies`: 1..25 unique Movie IDs |
| apply | `key`, `movies`, `baseline_digest` from a fresh preview |
| remove | `key`, `baseline_digest` from a fresh preview |
| reconcile | Empty object, or no request file |
| show | Empty object, or no request file; configured journal-owned set only |
| display-title-preview | `title`: 1..256 characters, no control characters |
| set-display-title | `key`, `title`, `baseline_digest` from the preview for that exact title |

## Identity, provenance and managed grant semantics

Every identity validation uses the same fresh authenticated session and rechecks
admin-only GET `/Users`, exact actor and recipient GET `/Users/{id}`, explicit
non-disabled policies, actor admin policy, pinned server, actual virtual-folder
item ID/GUID/movies type, and complete recipient Views containing the library.
Changing session binding, losing admin capability or scope drift refuses.
`query_movies` uses `/Users/{Igor}/Items` with Recursive=true,
IncludeItemTypes=Movie, ParentId=pinned library and the full requested IDs. It
requires complete exact unique results with Type=Movie and returns requested order.
Missing restricted Movies refuse the entire request. Direct known-ID Movie GET is
never a visibility fallback. Normalized LibraryId is an assertion backed by this
scoped query, not an invented DTO field.

The adapter receives the SAME Journal object as the core and reads it only while
the core holds its lock. It derives the marker from the exact scope and validates
journal scope before using provenance. No method accepts an arbitrary owned-ID
adoption instruction. Initially an existing exact marker refuses. Only an already
durable pending create with an empty playlist precondition may reconcile one
unique typed exact marker from the verified actor's complete playlist namespace.
Saved IDs bind subsequent operations, including read-only reconciliation and show.
The optional protected v1 journal field `display_title` is the expected Home row
CustomName. Playlist Name has a separate immutable expectation, the scope-derived
marker. The title defaults to the marker for legacy journals. Reads never insert
it, migrate snapshots, save the journal or adopt an object. The normalized playlist
shape and retained v1 snapshot digests stay unchanged. Lookup selects the saved
playlist ID and checks Name equals the marker in discovery and exact read. A
second exact-marker object refuses. Before creation and during pending create,
unique exact-marker discovery remains required. Only successful explicit removal
completion clears the row title expectation; subsequent creation uses the marker
for both playlist Name and row CustomName. Manually renamed, deleted or
substituted objects refuse, except exact disappearance after pending delete is a
candidate postcondition for the core to compare against full state.
There is no name-only adoption, including after journal loss.

`snapshot(recipient, marker)` returns only `home` and `playlists`. Home preserves
row order, typed IDs, nested values and unknown stable fields, omitting only
ParentItem and UserData enrichments. The exact Emby 4.10.1.0 Query expansion
with StudioIds, TagIds, GenreIds and CollectionTypes all empty arrays normalizes
to an empty object. Nonempty, missing or additional query fields are preserved
and remain part of drift checks. Playlist discovery reads the actor Items
namespace, recursively typed Playlist, StartIndex=0, Limit=129, refusing above 128,
truncation, duplicate IDs, non-Playlist DTOs and ambiguous markers. Exact item read
must match saved/discovered ID, Type=Playlist, immutable marker Name and CanEditItems=true.
For a separate recipient it also requires CanManageAccess=true; for the actor's
own homepage it instead requires CanDelete=true. These positive booleans prove
the capabilities actually needed for that lifecycle. They do not
prove exclusive ownership. Opaque IDs are URL-safe bounded ASCII strings.

Normalized playlist shape is unchanged:

```python
{"id": playlist_id, "marker": marker, "owner": actor_id,
 "access": [] or [Igor_id],
 "entries": [{"movie": movie_id, "entry": fresh_PlaylistItemId}, ...]}
```

Here `owner` means operation provenance from the protected creation journal and
its actor binding. It is not a backend assertion that ManageDelete is exclusive
ownership. `access` means only the managed Igor grant, not the complete effective
ACL. For the same actor and recipient, positive edit/delete capabilities plus
creation provenance normalize availability to `[Igor]`; the sharing-candidate API
can omit self and is not queried. No exclusive ownership or missing-field
permission is inferred. For a separate recipient, GET
`/Users/ItemAccess?ItemId=...` must return complete Items/TotalRecordCount,
unique exact user IDs and pinned ServerId. Igor must appear exactly once with
UserItemShareLevel string `None` or `Read`. Missing Igor, malformed or other Igor
ranks refuse. `Read` normalizes to `[Igor]`, `None` to `[]`. Other candidates/grants
are neither changed nor inferred private. Public state and CanMakePublic are not
confidentiality gates. The implementation makes no all-principals privacy claim.

Occurrences come from GET `/Playlists/{id}/Items` in actor context, bounded to 25,
complete and ordered, with Type=Movie and unique PlaylistItemId. Occurrence remaps
are observable drift. Duplicate Movies remain visible rather than deduplicated.
All malformed/unqualified DTO shapes refuse at the relevant read; no global
unconditional false-ready gate remains.

## Mutation and recovery behavior

All management operations use the same core, Journal, run, pending and done state.
Show and reconcile are read-only; journal custody is unchanged. Each mutation requires
the existing durable pending intent, exact operation and arguments, journal scope, fresh admin
identity and a fresh complete snapshot equal to both pending.before and current.
Exact saved playlist ID and marker must match. No arbitrary delete/adopt interface
or raw route CLI exists. No mutation response supplies trusted state; the next
snapshot must satisfy the core's exact postcondition.

| Method | Fixed request |
| --- | --- |
| create_playlist | POST /Playlists?Name=marker&userId=actor, empty playlist |
| share_read | POST /Items/Access, UserIds=[Igor], ItemIds=[saved ID], ItemAccess="Read" |
| create_row | POST /Users/{Igor}/HomeSections, exact journaled core template with no Id |
| set_row_display_title | POST /Users/{Igor}/HomeSections, full fixed saved row including Id, changing CustomName and clearing only the exact legacy subtitle |
| delete_entries | POST /Playlists/{saved ID}/Items/Delete, EntryIds=comma-separated current occurrences |
| add_movies | POST /Playlists/{saved ID}/Items, Ids=validated Movies, userId=actor, SkipDuplicates=false |
| delete_row | POST /Users/{Igor}/HomeSections/Delete, ids=[exact saved row], preserving type |
| delete_owned_playlist | POST /Items/Delete?Ids=exact saved Playlist, after saved row removal and no recipient Home reference |

The transport accepts 200 reads/auth, 200 or 204 mutations, and empty mutation
acknowledgments. A wrong response shape refuses on the relevant read. It disables
proxies, redirects and retries, verifies TLS normally and counts each dispatch
before attempt. Bounds are 512 requests, 120 seconds total, 5-second socket timeout,
4 MiB response, 16 KiB body, 8192-character URL. Streaming reads check the deadline;
a blocked read can finish up to one socket timeout later. Partial Content-Length,
nonfinite/duplicate-key JSON, transport errors and unexpected statuses refuse
without echoing body or credential data.

Show takes no playlist ID or other selector. It returns at most one configured
journal-owned playlist with exact ID, owner provenance, managed access and ordered
Movie/occurrence IDs. It includes immutable scope/marker, verified actual `playlist_name`, separate
`playlist_expected_name`, expected row `display_title`,
saved row ID and row object, observed rows referencing the playlist, current
snapshot `revision` digest, `journal_digest`, pending operation, unfinished request
key, completion count and reconcile state. Output is detached and stays in
operator custody. Bounds remain one playlist, 25 occurrences, 100 Home rows and
the existing bounded transport. Truncation refuses rather than returning partial
success. With no journal and no owned objects, show returns an empty set without
creating a journal. Unexplained marker objects refuse. Manual state drift refuses;
pending state is returned only when the observed snapshot equals the complete
saved precondition or exact postcondition. It never retries, saves or adopts.
Reconcile retains its existing clean/drift/observed-applied/unresolved behavior.
Authentication still sends its required POST; show itself performs GETs only.

`display-title-preview` validates the same retained playlist and exact saved row,
then hashes scope, operation, fresh snapshot and intended title together. A title
preview cannot authorize another title or a membership operation. `set-display-title`
accepts no playlist ID, row ID, recipient selector or raw body. The saved row must
have its exact typed Id, SectionType=playlist and ParentId bound to the retained
playlist. An optional row UserId must equal the configured recipient. The existing
admin actor/recipient binding remains fixed, including an already configured
separate recipient. This operation introduces no other-user selection.

The write copies the complete normalized fetched row with Id and changes only
CustomName, plus Subtitle from exactly `operator-provisioned` to an empty string.
Other subtitles remain unchanged. The fixed field whitelist is UserId, Name, CustomName, Id, SectionType,
Subtitle, ViewType, ImageType, DisplayMode, Monitor, ItemTypes, ExcludedFolders,
CardSizeOffset, ScrollDirection, ParentId, SortBy, SortOrder,
IncludeNextUpInResume and Query. Missing required identity/name fields or unknown
stable fields refuse. Existing ParentItem/UserData enrichment omission and exact
empty Query normalization remain unchanged. Unknown fields stay in reads and drift
comparisons; they cannot be dropped or forwarded by this write.

The exact accepted readback changes only the same row's CustomName and server-set
Name to the intended title, plus the exact old-label-to-blank transition only when
requested in the pending body. Historical pending bodies that retain the old label
must retain it in readback. Completing them does not dispatch a cleanup write;
use a fresh preview and key to remove the label afterward. Reads never rewrite
legacy journal bytes. All other row fields, typed IDs, ParentId, row order,
playlist identity, marker, grant and ordered membership must match. Playlist Name
remains the marker; playlist metadata is never sent. The row update is at most one
mutation dispatch, with no retry after uncertainty. Identical completed-key replay
performs zero management writes. Pre-send NotDispatched clears pending while
retaining run for the same key/request. An uncertain unchanged snapshot cannot
resume by dispatching again; only exact after-state readback permits completion.
Show accepts pending snapshots only at the exact before or after footprint.
Reconcile reports unresolved or observed-applied without changing journal bytes.
A successful completion records the row title in the existing journal. The lock
covers intent, dispatch, final snapshot and completion. A fresh invocation may
complete a lost-response write by readback with zero additional writes.

Preview validates current identity, Movies and snapshot, returning its digest.
Apply requires that digest for a new key; replay of a completed identical key makes
zero writes. A changed request under the same key refuses. At most 64 completion
keys fit the journal. Reconcile reads only, including no journal JSON changes; it
reports clean/drift or observed-applied/unresolved pending intent. It never retries.
Resume only the same unfinished key/request in a fresh authenticated invocation.
Only exact postcondition readback lets the core continue remaining approved steps.
An unchanged precondition after uncertainty is never proof of nonexecution.
If the trusted adapter proves that a failure occurred before any mutation transport
attempt, it raises NotDispatched. The core durably clears only that pending step
and keeps the same unfinished request. A fresh invocation can resume after full
identity, visibility and snapshot revalidation. Once the mutation transport is
attempted, all failures remain uncertain, even if the response suggests no change.

Operator requests still reject duplicate Movie IDs. Intentional repeats are not
delivered through this operator; default-off CLI source does not change that.
Independent sharing, Home create/move/delete lifecycle, arbitrary object selection
and intentional repeats remain unsupported. Existing apply/remove continue their
bounded owned-resource lifecycle.

Updates clear exact current occurrences and add the validated desired sequence;
the Home row stays stable. Removal deletes only the saved row and playlist and
restores the captured Home baseline exactly. No playback, UserData, library,
policy, other grant or media-file write exists. Mid-update empty membership can be
visible. Partial operations are not transactionally rolled back. Manual drift,
ambiguous discovery and unresolved writes require protected manual inspection.
Never reset the journal or blindly repeat a write to get past refusal.

Snapshot comparison is optimistic race detection. The known routes offer no
qualified conditional-write token. An external actor can race the final read and
dispatch. Protected single-operator use is required; there is no linearizability
claim.

## Qualification boundaries

The base source is bcc1d6b5f9c9ac994b6f69373b35ac2a078546a6. Existing
lifecycle and show evidence belongs to that base, not this new title source.
The row-write design follows qualified same-row save proof
`1c877946348798136b8050be378dce9a@2`. The supplied regular fixture receipt and
before/after evidence establish the fixed row update and server Name/CustomName
behavior. They do not establish a safe generic playlist metadata rename. Diagnosis
`c9c8e1f02d2ab09512842018f692171d@1` records that unsupported preservation path.

The recording transport checks use synthetic DTOs. They reject item metadata POSTs
and model the same-row write and uncertain-response behavior. Chaldea verification,
independent review, CI and runtime qualification remain separate requirements.
Protected credentials and real recipient restrictions require deployment evidence.
This document grants no new access.
