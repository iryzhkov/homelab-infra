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

Public GET /emby/System/Info/Public on homelab returned Emby 4.10.1.0 on
2026-10-20. The public web app reports the same version. The available browser
profile reaches sign-in, so authenticated rendering is unproved. No authenticated
API sample, plugin version, TV device/app version or history cohort has been
qualified. Existing protected Emby access is sourced by scripts/emby-favorite-upgrades.py
from /srv/swarm/ombi/config/OmbiSettings.db. The ordinary SSH account cannot
traverse /srv/swarm/ombi. No escalation or credential export was attempted.
Historical /home/iryzhkov/app copies are not evidence of current credential custody.

The existing collector reads Jellystat api/getTaskSettings and logs/getLogs.
These prove sync-job results, not individual viewing sessions. ActivityMonitor
freshness is explicitly unknown. Native item UserData can support played/count/
last-played/resume summaries after separate user authorization; it must not be
described as a complete event ledger. The host-drain activity database query is a
recent-activity heuristic, not a recommendation history contract.

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

## Exact bounded operator packet, pending authority

No command below is authorization. Parent/user coordination must settle the
profile names, client/device versions, audience and private evidence handling.

1. Authorize one homelab-only operator window, capped at two hours and 200 API
   requests, with no service rollout, Authentik enrollment or scheduled task.
   Pause competing home playbook runs. Do not disable playback or change media.
2. Operator privately captures server/plugin versions, exact Igor/TV baseline
   row IDs and contents, and the six recipe-key ownership mappings. Keep full
   user settings, PINs, tokens and snapshots out of chat and job snapshots.
   Supply the private ownership variable file only to this scoped playbook.
   Approve which existing read-only credential path the operator may use.
3. Observe the six baseline rows in an authenticated real web app and named
   physical TV app. Record device/app/server versions and sanitized images.
   Emulated TV browser mode is not physical TV acceptance. Empty rows need
   explicit empty-state observation rather than a fabricated populated row.
4. Proposed disposable non-admin owner is "HoloNet P0 canary"; proposed allowed
   reader is the existing TV profile; proposed denied reader is a separate
   "HoloNet P0 denied" disposable profile. New profiles/credentials/policy changes
   require explicit approval. If existing profiles are chosen instead, name them
   and grant that exact use. Do not create profiles under current authority.
5. Operator prepares the exact installed-version request bodies and target IDs
   from the native client, then reviews a private bounded mutation manifest.
   Create at most one private Video playlist, one comparison collection and one
   row on the disposable owner. At most five existing allowed items, including
   one movie, two related episodes and one intentional repeat. Use the prefix
   holonet-p0-20261020 and retain returned object/entry/row IDs privately.
   No watch/favorite/rating, user-policy, media, artwork or library-option changes.
   If proving selected sharing needs policy/user changes, stop for a revised
   packet rather than broadening to global public.
6. Prove mixed item support, entry order/repeats, rename/reorder/remove-by-entry,
   private versus shared visibility and edit rights from owner/allowed/denied
   contexts. Query a denied-library item without disclosing its title/artwork.
   Treat collections as library-shared until stronger isolation is demonstrated.
   Never put private-history-derived selections/explanations in this collection.
7. Capture exact HomeSections fields for a playlist/collection-backed row from
   the installed native client. Append only the named disposable row in the
   agreed generated slot. Observe it in both agreed real clients. Run the new
   playbook preview, apply only if authorized and reviewed, then preview again.
   Require changed0 and unchanged manual/generated rows. Keep each request and
   response effect in the private canary journal; retained public fixtures must
   be synthetic equivalents, not redacted full user records.
8. Inject one manual-row/order conflict and prove stop/preservation. Undo only
   the canary-owned change after a fresh comparison. Remove row and disposable
   objects through their specific qualified endpoints. Generic DELETE /Items
   is forbidden. If object-only deletion cannot be distinguished from media
   deletion, stop and ask the operator to remove the object in the native UI.
9. Restore baseline-owned fields/order, preserve unrelated changes and verify
   no canary objects remain, all original IDs/membership/settings survive, and
   ownership-aware playbook apply/preview plus the host's existing drift checker
   return no change. Do not run the old full-list reconciler as a rollback.
   Resume any paused operator process. Produce sanitized per-client and
   per-capability pass/fail receipts. Stop if audience isolation or scoped
   restoration cannot be proved.

Before the operator window, a read-only paginated sample is separately useful:
one user and allowed library, Limit 25, at most two pages, and no broad crawl.
Record counts, response bytes, elapsed time, continuation behavior and evidence
coverage only. No title or user history needs to leave the host. History action
cohort/window and minimum credential reach remain unqualified, not inferred
from a powerful existing key.

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

No live mutation, deployment, enrollment, TV observation, authenticated API
permission proof or full history proof is claimed by these synthetic checks.
