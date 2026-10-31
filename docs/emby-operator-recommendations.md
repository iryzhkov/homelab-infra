# Operator-applied Igor Movie recommendations

This source batch provides the core, a bounded authenticated-admin transport and
an explicit local operator entry. WebMCP stays read-only. Igor accepted managed
playlist UX without strict playlist privacy. The operator must separately validate
real Igor item restrictions. This batch does not stage credentials or qualify a
production deployment. Its tests are source for the parent's Chaldea run.

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
| server, actor, recipient | Lowercase 32-hex exact IDs. Actor is the administrative creator. It may equal Igor for an operator managing its own homepage. In that case the adapter requires the intrinsic ManageDelete rank and skips sharing entirely, preserving management rights; a separate recipient still requires the explicit None/Read grant. |
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
Saved IDs bind subsequent operations, including read-only reconciliation. Renamed,
deleted or substituted objects refuse, except exact disappearance after pending
delete is a candidate postcondition for the core to compare against full state.
There is no name-only adoption, including after journal loss.

`snapshot(recipient, marker)` returns only `home` and `playlists`. Home preserves
row order, typed IDs, nested values and unknown stable fields, omitting only
ParentItem and UserData enrichments. The exact Emby 4.10.1.0 Query expansion
with StudioIds, TagIds, GenreIds and CollectionTypes all empty arrays normalizes
to an empty object. Nonempty, missing or additional query fields are preserved
and remain part of drift checks. Playlist discovery reads the actor Items
namespace, recursively typed Playlist, StartIndex=0, Limit=129, refusing above 128,
truncation, duplicate IDs, non-Playlist DTOs and ambiguous markers. Exact item read
must match saved/discovered ID, Type=Playlist, marker Name, CanManageAccess=true
and CanEditItems=true. These booleans prove current actor capability. They do not
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
ACL. GET `/Users/ItemAccess?ItemId=...` must return complete Items/TotalRecordCount,
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

The core public interface and lifecycle state machine remain unchanged. The only
core implementation change is journal custody. Each mutation requires the existing
durable pending intent, exact operation and arguments, journal scope, fresh admin
identity and a fresh complete snapshot equal to both pending.before and current.
Exact saved playlist ID and marker must match. No arbitrary delete/adopt interface
or raw route CLI exists. No mutation response supplies trusted state; the next
snapshot must satisfy the core's exact postcondition.

| Method | Fixed request |
| --- | --- |
| create_playlist | POST /Playlists?Name=marker&userId=actor, empty playlist |
| share_read | POST /Items/Access, UserIds=[Igor], ItemIds=[saved ID], ItemAccess="Read" |
| create_row | POST /Users/{Igor}/HomeSections, exact journaled core template with no Id |
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

## Evidence and parent integration

Huyang reads covered infra workspace ws_9e18f1bbcea7d6aaaa45eca7a86ef900 at wsrev_158,
base 9205c73, imported core, original 13-test module and companion doc. Typed source
read in catalog-target-source/mcp-holonet workspace ws_db6fed9670dc229fbbc7a817b4c6c142
at wsrev_1 covered recommendation_backend.go discovery, exact capability and
occurrence DTOs. Supplied corrected probe and receipt support admin recipient
Home/Items routing and singleton lifecycle in synthetic Emby 4.10.1.0, job
bf1506f0-b220-40a7-bf85-feb84cede4ea. Raw response/ledger artifacts were lost; those
limits remain. Prior parent core13 plus existing21 pass jobfceca8f8 is prior evidence,
not a test result for this batch. Global key remains unsupported.

Parent imports the adapter, entry, core replacement and new adapter tests under
scripts/, and this replacement document under docs/, using guarded Huyang edits.
Keep the original 13 core tests unchanged; an identical reference copy is supplied.
Keep the parent's existing CI delta and read-only ownership tests. Bind one Journal
instance to both adapter and Operator exactly as the entry does. No other core
contract, fake method signature, CI or MCP change is required.

Parent runs the final coherent batch through Chaldea, never locally. In the job set
HOME exactly to `$PWD/.cache/holonet-operator-tests/home`, TMPDIR to `$HOME/tmp`,
assert those paths before creating them, and set PYTHONDONTWRITEBYTECODE=1. Run
`python3 scripts/test_emby_operator_recommendations.py` and
`python3 scripts/test_emby_operator_adapter.py`, plus the parent's existing checks.
The recording HTTP suite models synthetic DTOs and exact request bodies; it is not
an Emby qualification run. Parent owns final review, tests, import and activation.
No repository writes, tests, jobs, production, real credentials, children, commits
or PRs were performed by this artifact author.
