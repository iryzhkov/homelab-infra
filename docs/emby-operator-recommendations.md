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

Journal version 2 manages up to four owned lists for the same recipient and
library. Version 1 behavior is unchanged until an explicit, preview-bound,
journal-only migration. See "Multiple lists (journal version 2)" below.

Native playlist item Name rename is unsupported under the Emby 4.10.1.0 metadata
preservation contract. The generic item save can change stored runtime 0 to NULL;
the Series-only setter does not remedy Playlist hydration. A populated fixture
whose baseline was already NULL does not prove preservation for all playlists.
There is no item metadata POST, runtime normalization, payload workaround or
metadata editor in this operator. The playlist Name remains the immutable marker.

## Scope and custody

Use one persistent journal for the exact server, administrative creator, recipient
Igor ID and Movies library. A version 1 journal is also bound to the configured
recipe; a version 2 journal is recipient-scoped and holds one record per owned
list. Keep it through restarts and uncertain writes. Never reset it to bypass
refusal. It stores private IDs, Home snapshots, provenance, intent and completion
keys. No second store or reconciler exists.

The entry requires real and effective UID 0 and an explicitly staged root-owned
config and username/password credential file. Files must be single-link regular
0600 in root-owned 0700 immediate directories. Every ancestor is opened with
O_DIRECTORY and O_NOFOLLOW from `/` and must be root-owned without group/world
write. No environment, native cache, database, global key or broad helper loader
exists. Paths through a user-owned home directory are unsuitable for real staging.
No CLI accepts a credential, token, raw route or arbitrary deletion ID.
For the retained Igor root, `playbooks/emby-operator-credential.yml` is the
explicit opt-in producer of this credential file and the enable flag; see
`docs/emby-operator-credential-provisioning.md`. For the six pinned additional
viewer recipients, `playbooks/emby-operator-viewers.yml` creates one protected
config and empty journal directory per recipient that point at the same
credential file; see `docs/emby-operator-viewers.md`.

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
| recipe | Immutable bounded opaque recipe ID. In version 2 it names the legacy list, whose marker is unchanged. |
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
| show | Empty object, or no request file; optional `list` on version 2 only |
| display-title-preview | `title`: 1..256 characters, no control characters |
| set-display-title | `key`, `title`, `baseline_digest` from the preview for that exact title |
| migrate-preview, downgrade-preview | Empty object, or no request file |
| migrate, downgrade | `key`, `intent_digest` from the matching preview |
| init-preview | Empty object, or no request file |
| init | `key`, `intent_digest` from a fresh init-preview |
| list-preview | `operation`, `list` and exactly that operation's argument fields |
| create-list | `key`, `list`, `title`, `movies` (0..25), `intent_digest` |
| set-members | `key`, `list`, `movies` (0..25), `intent_digest` |
| set-title | `key`, `list`, `title`, `intent_digest` |
| detach-row, delete-list | `key`, `list`, `intent_digest` |
| attach-row | `key`, `list`, `title`, `intent_digest` |
| move-row | `key`, `list`, `position` (integer, not Boolean, 0 <= position < fresh Home row count), `intent_digest` |

The first seven operations keep their request shapes. Their version 2 meaning
is described under "Legacy aliases". List-scoped operations refuse on a version
1 journal with "Journal migration required".

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
the core holds its lock. It derives each marker from the exact scope and list ID
and validates journal scope before using provenance. The marker formula is
unchanged: `holonet-recommendations-` plus the first 32 hex digits of the digest
of server, actor, recipient, library and recipe, where recipe is the list ID. In
version 2 the adapter discovers the markers of journal list records, the list of
a durable pending create, and the legacy marker, all from the held journal.
Create preview and fresh execution independently verify the proposed derived marker
is absent before journaling a run, so a preexisting object cannot strand creation. Marker playlists of unknown list IDs are ignored. No method accepts an arbitrary owned-ID
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
| create_row | POST /Users/{Igor}/HomeSections, exact journaled core template with no Id and CustomName equal to the list marker |
| set_row_display_title | POST /Users/{Igor}/HomeSections, full fixed saved row including Id, changing CustomName and clearing only the exact legacy subtitle |
| delete_entries | POST /Playlists/{saved ID}/Items/Delete, EntryIds=comma-separated current occurrences |
| add_movies | POST /Playlists/{saved ID}/Items, Ids=validated Movies, userId=actor, SkipDuplicates=false |
| delete_row | POST /Users/{Igor}/HomeSections/Delete, ids=[exact saved row], preserving type |
| move_row | POST /Users/{Igor}/HomeSections/Move, ids=[exact saved row], preserving type, newIndex=edit-view index freshly translated from the journaled normal position (see "Row placement"); refused when the row is already there |
| delete_owned_playlist | POST /Items/Delete?Ids=exact saved Playlist, after saved row removal and no recipient Home reference |

The transport accepts 200 reads/auth, 200 or 204 mutations, and empty mutation
acknowledgments. A wrong response shape refuses on the relevant read. It disables
proxies, redirects and retries, verifies TLS normally and counts each dispatch
before attempt. Bounds are 512 requests, 120 seconds total, 5-second socket timeout,
4 MiB response, 16 KiB body, 8192-character URL. Streaming reads check the deadline;
a blocked read can finish up to one socket timeout later. Partial Content-Length,
nonfinite/duplicate-key JSON, transport errors and unexpected statuses refuse
without echoing body or credential data.

On a version 1 journal, or with no journal, show behaves as below and refuses a
`list` selector. On version 2 it returns the catalog described in the version 2
section. Version 1 show takes no playlist ID or other selector. It returns at
most one configured journal-owned playlist with exact ID, owner provenance, managed access and ordered
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
keys fit the journal. Completed keys are never evicted: a key's record is the
only proof it was used, and record age does not prove nondispatch. When 64 keys
exist, every new request refuses before any write, replays still work, and show
reports `keys_exhausted`. No pruning or refresh protocol exists. Reconcile reads only, including no journal JSON changes; it
reports clean/drift or observed-applied/unresolved pending intent. It never retries.
Resume only the same unfinished key/request in a fresh authenticated invocation.
Only exact postcondition readback lets the core continue remaining approved steps.
An unchanged precondition after uncertainty is never proof of nonexecution.
If the trusted adapter proves that a failure occurred before any mutation transport
attempt, it raises NotDispatched. The core durably clears only that pending step
and keeps the same unfinished request. A fresh invocation can resume after full
identity, visibility and snapshot revalidation. Once the mutation transport is
attempted, all failures remain uncertain, even if the response suggests no change.

Operator requests still reject duplicate Movie IDs within one list. Intentional
repeats are not delivered through this operator; default-off CLI source does not
change that. Independent sharing, placement of any row other than an owned
list's saved row, arbitrary object selection, native incremental membership and
intentional repeats remain unsupported. Version 2 detach-row and attach-row only
delete an owned row or append a new one at the end of Home; move-row only
changes the index of an attached owned row (see below). Existing apply/remove
continue their bounded owned-resource lifecycle.

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

## Multiple lists (journal version 2)

Version 2 keeps one journal, one lock, one unfinished `run`, one `pending` step,
one shared Home `baseline` and one `current` snapshot for every owned list of
the configured recipient and library. Lists therefore serialize under the
existing lock, and each completed step updates `current` for all lists in one
atomic save, so a sibling list never looks like drift. The journal holds:

```python
{"version": 2, "scope": {server, actor, recipient, library},
 "baseline": snapshot, "current": snapshot,
 "lists": {list_id: {"row": row_id_or_null, "display_title"?: str}},
 "pending": None or {"list", "operation", "args", "before"},
 "run": None or {"key", "request"}, "done": {key: request},
 "migrated_from"?: {"version": 1, "recipe", "journal_digest", "key"}}
```

The snapshot shape is unchanged; playlists are sorted by marker. With only the
legacy list the snapshot, and so show's `revision`, is identical to version 1.
A list record exists exactly while its playlist is in `current`. At most four
lists fit. That bound comes from the unchanged 512-request transport budget:
the recording transport test asserts that creating a fourth list for a separate
recipient with a title and 25 Movies stays below that fixed budget. Actual
multi-list runtime request counts remain a release qualification gate.
Raising the bound needs scoped reads or a transport review, not a larger budget.

Identity is the saved playlist ID, saved row ID, the journal creation provenance
and the immutable marker. Titles need not be unique across lists or against
manual rows, and a matching title never blocks management or proves ownership.
A title shaped like another list's marker refuses.

`list-preview` returns `intent_digest`, the digest of the version, scope, the
canonical request (operation, list and arguments) and the fresh snapshot. A
digest for one list, operation, title or Movie sequence cannot authorize any
other. Operations:

| Operation | Steps, each journaled before dispatch |
| --- | --- |
| create-list | create marker playlist, share Read if the recipient differs, create row titled with the marker, set the row title if it differs, add Movies if any |
| set-members | clear current occurrences if they differ, then add the full new sequence if nonempty |
| set-title | one same-row title save on an attached list; refuses when detached |
| detach-row | delete the owned row; playlist, membership and saved display title remain |
| attach-row | create a marker-titled row at the end of Home, then set its title |
| delete-list | delete the row if attached, then the playlist; the record is removed |
| move-row | move the attached owned row to its final 0-based `position` in the normalized Home list; no write when it is already there |

Friendly row titles at creation use the qualified two-step path, so the marker
is briefly visible as the row title. Direct creation with a friendly title is
not qualified and not used. Membership is replacement only. Add, remove and
reorder are expressed as a new complete sequence of 0..25 unique Movies; the same
Movie may appear in different lists. An empty sequence clears membership and
keeps the playlist; it never deletes the list. No native incremental endpoint,
append, subset delete or entry move is claimed. A mid-update empty playlist can
still be briefly visible.

Every step uses the exact footprint check of version 1, indexed by the list's
marker: the operated playlist or row changes as specified and every sibling
playlist, entry, grant, row object, field and Home position is byte-identical.
Any other Home change, including a manual reorder, still refuses as drift.
After delete-list, the Home rows not owned by a remaining list must equal the
shared baseline exactly. Lost responses and NotDispatched recovery are the same
as version 1 for every step type: only exact after-state readback completes a
step, an unchanged state after an attempted POST stays unresolved, and
NotDispatched clears only the pending step while keeping the run. Show and
reconcile remain read-only; pending state names its list.

### Row placement (move-row)

`move-row` moves only the saved row of one named, attached list, found by its
journal creation provenance and exact typed row ID. It accepts no row ID,
recipient, raw body or title selector. `position` is the row's final 0-based
index in the normalized snapshot Home list, an integer (Boolean refuses) with
0 <= position < 100 at request validation. Preview and a new request, before a
run is journaled, also require the list to exist, a saved row, the fixed saved
row checks used by set-title (typed ID, SectionType=playlist, ParentId of the
saved playlist, CustomName equal to the expected display title, field whitelist,
optional UserId equal to the recipient) and position below the fresh Home row
count. The intent digest binds operation, list, position and the fresh
snapshot, so it cannot authorize another list, operation or position, and any
Home drift refuses.

`position` counts the normal Home view that the snapshot reads, which is not
the index space of the native Move call. Emby's edit view (GET
`/Users/{Igor}/HomeSections?isEditMode=true`) can list one generated section,
for example `latestmediablock`, where the normal view shows one or more rows
with other typed IDs, for example `latestmedia_3`. The normal position is
therefore never sent as `newIndex`. Instead the adapter translates it from a
fresh read of both views. The edit view is read through the same fixed
HomeSections route and the same bounded Home normalization (complete, at most
100 rows, unique typed IDs). The translation requires the following:

1. The saved row is present in both views and its normalized object is identical
   in both.
2. The rows whose typed IDs appear in both views have the same relative order in
   both views.
3. If the row is already at `position`, nothing is sent and the request
   completes with zero writes. This holds even when the next normal row has no
   edit-view counterpart.
4. Otherwise the saved row is removed from both views. If `position` is the end
   of the remaining normal rows, `newIndex` is the length of the remaining edit
   rows. Otherwise the normal row that will follow the moved row must have the
   same typed ID in the remaining edit rows, and its edit index is `newIndex`.

A following row that exists only in the normal view, such as one row of a
generated section, refuses ("Unsupported generated Home section at position").
The operator never guesses a native index and never splits a generated section.
Placement directly before such a row is unsupported. A saved row that differs
between the views, a mismatched shared order, duplicate IDs and an unmatched
following row all refuse.

The core runs this translation as a read-only preflight in preview and before a
run is journaled, so an untranslatable request leaves no run or pending state.
A row already at `position` completes with zero writes. Otherwise one step is
journaled with `{row, position}`; the journal never stores a native index. The
adapter re-reads the full snapshot, repeats the same checks, refuses a redundant
dispatch, recomputes the translation from a fresh edit view and sends one POST
`/Users/{Igor}/HomeSections/Move` with `ids=[saved row]` and the translated
`newIndex`. If the edit view changed between preflight and dispatch so that the
translation now refuses, nothing is sent; NotDispatched clears only the pending
step and the same key resumes.

The exact accepted readback compares the normal view only. It removes that
identical row object and inserts it at `position`; every other row object,
manual row, sibling list row and their relative order, every playlist, grant and
entry, the list records and the shared baseline stay unchanged. Absolute indices
of other rows shift by at most one. Because no-op moves are never journaled, an
unchanged Home after an attempted dispatch stays unresolved. Lost responses,
NotDispatched, replay and key reuse behave as for every other step. Delete-list
still restores the baseline after moves, since moves keep the relative order of
rows no list owns.

The general inverse is a move-row of the same row back to its prior index, which
show reports per list as `position`. That inverse refuses before journaling when
the row that would follow it is a generated-section row with no edit-view
counterpart. There is no native undo and no automatic rollback.

Runtime qualification: an isolated Emby 4.10.1.0 fixture
(`5ce7adf4d2c8f535560a2094388aded1@1`, job a100bf88-08c5-4dd9-a7a9-278fb20b24f1)
moved one saved row to native indices 0, 3, 1, 7 and 5. Each final edit-view
index equaled `newIndex`, counted after the row's removal; each view kept every
other object and relative order, and the same-ID inverse restored both views.
That fixture had one generated section and equal view lengths, so it does not
distinguish normal from edit indices. The translation above is therefore a
source rule, checked by recording tests, not a native-qualified mapping. Homes
with several libraries or hidden or grouped sections are not qualified. This
operator's composed runtime behavior, production Home composition, the
production Emby version, restart persistence and web or TV rendering are also
unqualified. Runtime use stays gated on final exact-source review, tests and CI.

The version 2 show catalog contains `version`, recipient `scope`, `legacy_list`,
`lists` (each with list ID, marker, legacy flag, expected display title, observed
playlist, the saved and observed row, and `position`, the observed index of the
saved row or null), `list_count`, `list_limit`, `revision`,
`journal_digest`, `pending`, `run`, `done_count`, `done_limit`, `keys_exhausted`,
`migrated_from` and reconcile state. `{"list": id}` narrows `lists` to one list
and refuses an unknown ID.

### Legacy aliases

On a version 2 journal the old operations act only on the list whose ID is the
configured recipe, and each is exactly one list-scoped operation with its own
digest. `preview` returns the set-members digest and `apply` is set-members; it
does not create a missing list or row. `display-title-preview` and
`set-display-title` are set-title. `remove` is delete-list and needs a
`list-preview` delete-list digest; a membership preview digest refuses. Version 1
digests never authorize version 2 requests. Completed version 1 keys keep their
original requests, so reusing one on version 2 refuses as a reused key.

### Migration and inverse

Reads never migrate. `migrate-preview` returns `intent_digest` over the
operation, configured scope including recipe, the version 1 journal digest and
the fresh snapshot. `migrate` requires that digest, version 1, no pending step,
no run, no drift, at most 64 done keys, known fields only, and either both a
playlist and saved row or neither. It writes version 2 in one save and makes no
Emby write. The legacy record keeps the exact saved row ID and optional display
title; baseline, current and every completed key are copied unchanged, and
`migrated_from` records the version 1 digest and migration key. Replaying the
same key returns `already-migrated` with no save. With no journal, the first
create-list starts a version 2 journal directly; old operations still start
version 1. `init` starts an empty version 2 journal without any Emby write.

### Initialization

`init-preview` and `init` create an empty version 2 journal for a recipient
that has none, so the Home baseline is captured before any list exists and
`show` reports version 2. Both hold the journal lock, verify the admin session
and recipient identity, and read the full Home and owned-playlist snapshot
through the same admin recipient-context GETs as every other operation.
`init-preview` returns `intent_digest` over the operation, recipient scope, an
absent journal and the fresh snapshot, plus that snapshot's `revision`.

`init` requires that digest and an absent journal, which it rechecks just
before its single save. It refuses when the configured legacy marker playlist
exists or any Home row has a marker-shaped Name or CustomName; nothing is
adopted. Marker playlists of unknown list IDs stay unowned, and a later
create-list for such an ID refuses. The saved journal has the fresh snapshot as
both baseline and current, no lists, no pending step or run, and the init key
as its only completed request. There is no Emby write.

Replaying the same key on a clean journal returns `already-initialized` with no
save. Any other existing journal refuses with "Journal already exists": version
1, version 2 started by create-list or migrate, the retained Igor journal, or a
different init key. An existing journal is never reset or replaced. The init key
stays reserved, so reusing it for a list operation refuses as a reused key.
Code without init reads an initialized journal as an ordinary version 2 journal.

`downgrade-preview` and `downgrade` are the inverse. They require version 2, a
clean state, and either no lists or only the configured recipe's list while it
is attached. They rebuild the version 1 fields from the same baseline, current,
row, display title and every completed key; only `migrated_from` metadata is
dropped. Migrating and then downgrading reproduces the original version 1 bytes.
Extra or detached lists refuse; delete or attach them first. Version 2 keys stay
in place and refuse as reused keys under version 1.

Code at or before 7180f726c368d9e5aae7a9209a42825127e4c1a9 refuses a version 2
journal as foreign and touches no Emby object. Downgrade first, then redeploy the
old code.

## Qualification boundaries

The base source is bcc1d6b5f9c9ac994b6f69373b35ac2a078546a6. Existing
lifecycle and show evidence belongs to that base, not this new title source.
The version 2 source is based on 7180f726c368d9e5aae7a9209a42825127e4c1a9. Its
recording tests use synthetic IDs and are not runtime evidence; real request
counts and latency at two or more lists remain unmeasured.
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
