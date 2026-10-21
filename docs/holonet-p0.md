# HoloNet P0 evidence and canary packet

This source slice is a prerequisite, not P0 acceptance or runtime activation.
Plan jocasta:3d249002623f32471f6b6bc617526f20@5 requires authenticated web and TV
rendering plus disposable-object audience/rollback proof before P1 activation
or P2 write-engine work.

## Verified starting point

Source base ebbde2778ea776acfaa6db43e24f9d0e4d723ca4, homelab-infra main.
The old playbook's full-list reconciliation deleted every HomeSections ID outside
the six-row recipe. This change requires explicit existing IDs and preserves all
other rows. It installs no service and enables no HoloNet writes.

On 2026-10-20 Igor explicitly approved a homelab-only read-only operator
preflight using the existing protected credential in place, at most 200 requests
over two hours, limited to Igor/TV catalog/history/home/version evidence.
The operation ran within that authority and is now closed. No credential,
configuration, media or profile mutation occurred. Detailed sanitized receipts
belong in the execution checkpoint's linked Jocasta research, not Git.

Installed Emby is 4.10.1.0, Playback Reporting 2.1.0.7 and TheIntroDB 1.1.0.0.
Both profiles expose the same six expected home-row kinds. Populated row-item
GETs return Movie, Series, Episode or CollectionFolder types. Both collection rows
are empty and TV favorites are empty. These are API observations; authenticated
web and physical-TV rendering remain unproved and need Igor.

The source helper accepted at 419b96c was executed in check mode through a
GET-only transport against both live profiles. A candidate explicit ownership
map was resolved privately for preview only. It returned changed0; full before/
after row contents and IDs were equal. No ownership adoption, apply, deployment
or runtime installation was performed.

Existing Emby credential custody remains the protected current Ombi database
used by scripts/emby-favorite-upgrades.py. The bounded probe reads only that
Emby setting, rather than opening unrelated arr credentials. The credential
remains in process memory on homelab, and response bodies are projected there
before sanitized output leaves the host. Public profile discovery can omit TV;
an exact-name operator user-list lookup resolves hidden profiles without
exporting any other profile's configuration. A prior sign-in image identifier
failed current-name verification and was discarded before home/history reads.

Installed library and media IDs are decimal strings. Home row IDs include
server-issued hex strings and the native literal resume; IDs cannot share one
hex-only grammar. The candidate contract now distinguishes these namespaces.

Native item UserData needs explicit Fields=UserDataPlayCount,UserDataLastPlayedDate.
The default projection returned zero counts and no dates even on played items.
Explicit fields produced populated values in the bounded library sample. Home-row
count/date aggregates remain explicitly unavailable until that projection is
qualified; presence of a default zero is insufficient. Neither a zero default nor
a missing observation proves unwatched/inactive status.

Playback Reporting has an existing retained event store, separate from item
state. A bounded read-only local SQLite sample and scoped native GET
user_usage_stats/HourlyReport both returned authorized evidence. HourlyReport
requires an explicit fixed filter=Movie,Episode: omitting it produced an all-zero
report despite retained events. Use a fixed user_id/date/window and qualified
media-type enum, never an agent-provided SQL/filter string. Collection start,
client completeness and liveness are still unqualified.

The plugin also has mutating GET routes for user management and backup import/
export. Those are forbidden, alongside custom SQL and every unregistered route.
HTTP GET alone is not a safe-read classification. The future service must use
a closed typed adapter. The existing collector's Jellystat task/log metrics
remain sync-job evidence, not a viewing-session ledger.

## Candidate service contract

vars/holonet-contract-v1.json is the candidate shared contract. Canonical resource
is https://media.mcp.ryzhkov.dev/mcp. Proposed issuer/provider spelling is an
offline design choice, not an enrolled provider. No mapping, helper or server
consumer is added in P0. Freeze its revision only after installed semantics and
Audit A. The document fixes names, grammar, bounds, time/audience form, scope/action
intersection and forbidden-route vectors for future issuer/helper/service tests.

The original Ansible operator preference path still writes owned user preferences
and the Movies ImportCollections option. Those operations are forbidden to the
HoloNet MCP adapter. This change does not turn that operator script into an
agent-accessible tool.

Official upstream API evidence, not installed qualification:

- https://dev.emby.media/doc/restapi/Playlists.html documents video playlists and
  PlaylistItemId entry IDs, including entry removal rather than media deletion.
- https://dev.emby.media/reference/RestAPI/UserLibraryService.html documents
  MakePrivate, MakePublic and Items/Access. MakePublic means all users, not a
  substitute for a selected audience.
- https://emby.media/support/articles/apps/common/User-Prefs-HomeScreen.html
  documents playlist/collection-backed rows on 4.10 and warns that newly added
  libraries may enter an existing section's scope.

## Ownership and order contract

An operator-reviewed emby_experience_owned_rows variable must name version 1,
the exact server_id and exactly the configured profiles. Each profile has the
exact user_id and rows mapping each recipe key to one already existing ID.
Values preserve the server's JSON type. No title-based adoption, row creation or
unknown-row deletion occurs. Missing/duplicate IDs or another server/user stop
before mutation across all profiles. Example synthetic shape:

```json
{
  "version": 1,
  "server_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "profiles": {
    "synthetic": {
      "user_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "rows": {"movie-spotlight": "server-issued-example"}
    }
  }
}
```

The example is incomplete for the real six-row recipe and intentionally cannot
authorize a real run. Keep the actual ownership file private, outside Git/job
snapshots. Review adoption against exact row IDs, full existing contents and
protected before-state. Duplicate legacy IDs require explicit operator repair;
this helper refuses them.

Ansible owns each baseline row's recipe fields and baseline relative order.
Unspecified baseline fields are retained. It restores baseline order among the
current baseline slots while preserving unknown rows' final slots, contents and
relative order. Unknown rows may move transiently while baseline rows move.
The generated-row slot is immediately after the last baseline row, before any
following manual rows. Only future HoloNet code may insert its own registered
IDs there. Ansible preserves that row as unowned. Neither owner may adopt the
other's row or enforce a competing whole-list recipe.

Fresh reads guard each home write and unexpected readback stops the run. Emby
provides no demonstrated compare-and-swap transaction here. A writer can still
race between preflight and dispatch. Serialize operators for qualification and
do not claim atomic rollback or protection from all concurrent writers.
Baseline recipe-field edits are Ansible drift; unknown-row edits are preserved.
HoloNet-owned edits will require its separate conflict/journal contract at P2/P3.

Reverse rollback must use the ownership-aware helper. Running an older full-list
playbook after adding generated/manual rows would delete them. Do not roll back
the helper while mixed ownership remains active. Restore only exact owned row
fields/order from a fresh private canary snapshot after checking for intervening
changes; preserve all unknown rows. No generic item delete belongs in rollback.

## Next client action and reduced canary packet

The approved read-only preflight is complete. It does not authorize any action
below that changes objects, credentials, policy or configuration.

First, Igor signs into the existing web client for Igor/TV and observes the six
baseline rows. Record browser/app version and sanitized visual evidence.
On a physical TV, record device/app version and observe the TV profile's same
baseline, including empty favorites/collections states. Browser TV display mode
is not physical-TV proof. No password, PIN or access token should be sent to an
agent. The user drives login in the shared browser or provides observations.

After that baseline, prepare one separately approved native-home canary:

1. Explicitly agree whether the existing TV profile may be the real canary
   owner/target. This avoids creating new accounts, but use of a real profile
   requires its own agreement. Name one allowed and one denied non-admin
   audience context before isolation tests; an administrator is not a meaningful
   denied observer. No other profile history is needed.
2. Capture the exact installed native-client bodies and select at most four
   existing visible members, one movie, two episodes and an intentional repeat.
   Keep the IDs and complete before-state in the private action manifest.
   Do not ask for write approval until that manifest and scoped restoration
   steps are concrete. No private-history-derived selection is needed.
3. The first mutation window contains only one private disposable Video playlist
   and one playlist-backed home row on the agreed profile. Use a unique owned
   namespace/title, the agreed slot after the final baseline row, and retain
   returned playlist-entry/row IDs privately. Pause competing home playbooks.
   Do not create a collection, account, recurring job or service in this first
   UI-viability window.
4. Prove member order/repeats, owner visibility and denied-user isolation, then
   observe the disposable row in both real clients. Run the ownership-aware
   preview and require changed0 with unknown rows preserved. Remove the row
   and playlist using qualified object-specific native operations, never a
   generic media-delete endpoint. Restore only canary-owned changes after a
   fresh conflict check; verify original IDs/content/order and changed0.
   If object-only removal or restoration cannot be proved, stop before creation.

This is a smaller first canary, not a reduction of P0/P2 acceptance. Selected-user
sharing versus edit rights, restricted-library behavior, collection grouping,
manual conflicts and full scoped rollback still need qualification before
dependent write-engine work. Keep collections shared within accessible libraries
unless installed isolation proves stronger. Prepare the next bounded action
only after the first UI premise survives.

No live apply, disposable object, profile/credential change or source merge is
authorized by the read-only approval. The old runtime full-list reconciler must
not run over mixed ownership. The new source helper's check mode is qualified
against today's six rows; apply/coexistence/reverse rollback remain canary gates.

## Synthetic verification

scripts/test_emby_home_ownership.py executes the actual playbook helper against
a recording backend. It covers all baseline permutations interleaved with manual
and generated rows, duplicate titles, owned-content drift, repeat changed0,
preview no writes, invalid/missing/cross-user ownership, duplicate/missing IDs,
later-profile preflight and concurrent manual edits. The fake refuses all routes
outside the expected operator calls. Future HoloNet adapters still need the
contract's complete forbidden-dispatch vectors with an overpowered backend key.

Chaldea maps this isolated checkout under /home/igor/.cache/holonet-executor to
/work inside the job. Use /work/.cache/holonet-executor/p0-tests/home and its tmp
subdirectory there, assert the mapping and writability, and disable bytecode
outside HOME. The first scratch attempt fell back after mkdir failed and is not
accepted verification; only the corrected asserted run counts.

scripts/test_holonet_p0_readonly.py separately covers fixed GET dispatch, exact
history-filter binding, redirect refusal, request/time/response bounds, hidden
profile resolution and output privacy. Its fixtures contain no real user data.
The one-shot probe is an operator evidence tool with a fixed expired-after-window
deadline, not a deployed MCP service or reusable authority grant.

Live read-only receipts establish only their named reads and projections. Neither
synthetic checks nor API success establish a native-client render, least-privilege
backend credential, denied-user isolation, live writes or complete history.
