# Staged acquisition source support

Contract jocasta:cf9f8403ccc2eedb927b3fe0e346bd18@3, independently accepted
in jocasta:1e64d99b957e5d3c03f5b35ffca46d63@1. Default off. No inventory
here enables acquisition, issues grants, creates credentials or downloads media.

The existing protected worker has five closed operations: inspect, combined
inspect/search, start, status and verify. The Go service owns bounded public
search observations and intent-before-effect records in its existing journal.
The worker stores no release URLs or acquisition state. Search handles bind
canonical target/indexer/GUID/title/size/hash tuples; start resolves the exact
same tuple afresh, requires its known v1 hash and explicit English AUDIO/dub
claim, and refuses ambiguity. Bare dual/multi, subtitles and Sonarr language
labels are insufficient. No Sonarr grab/import or library change is exposed.

Start adds uploaded torrent bytes once to the fixed holonet-stage category and
an exclusive operation directory under /mnt/shared/downloads/staging/holonet.
The directory is created empty without adopting any existing name. This is a
preparation effect and not global writer exclusion. With start selected, the unit
permits writes only under that fixed staging prefix and uses existing media group
988. The new directory retains worker ownership with group988 access for qBit;
no CAP_CHOWN or other root capability is added. No cleanup removes it on failure. Add attempts with lost replies remain uncertain; an ACK survives a
readback/logout failure. Status requires matching hash/category/tag/exact path.
Add readback and later status also require actual per-torrent MatchAny/Stop and
unlimited ratio/time fields; missing fields or a global/removal fallback refuse
qualification, without an automatic settings repair or another add.
Each add explicitly disables the global incomplete path and supplies the same
operation directory as its per-download path. Readback/status refuse an external
download_path or enabled auto_tmm; final-savepath isolation alone is insufficient.
An exact-image binary proof found that qBit5.3.0 treats invalid Custom as
Default. The closed request/readback therefore uses MatchAny with Stop and all
three explicit limits=-1. A scratch-only actual API probe verified these fields,
an empty per-download path despite a different global incomplete path, and a
duplicate HTTP409 with synthetic bytes preserved. Receipt and application-network
control limits: jocasta:d833c56105f5452a9c3801af8afa4faa@1, corresponding
contract correction jocasta:cf9f8403ccc2eedb927b3fe0e346bd18@4.
The frozen public wire remains its accepted version3 snapshot.
Actual NAS ownership, consumer aliases and concurrent-writer qualification remain.
Nothing automatically clears an uncertain or effective target reservation.

Verification reads only no-follow, single-link regular staged payloads whose
size/identity remains stable. A maintained /usr/bin/ffprobe receives at most
16MiB per file through pipe protocol, bounded time/output, with no shell or
network protocol. Only exact season/episode filename mappings and complete
expected coverage qualify. Unsupported filenames/containers or incomplete
headers refuse. Declared English audio-track metadata is not listening proof.
All original bytes and torrents remain untouched; import/replacement is separate.

## Package support and pending activation

The optional endpoint acquire input pairs a fixed Sonarr descriptor with an
existing guarded qBit credential. It never reads or copies the credential in
Ansible and never creates a new login. start_enabled/media_enabled are explicit
booleans and independent of purpose grants. The three acquisition scopes project
separate flat managed-janitor grants on the existing Anime writer; old grants and
request branches remain separate. Defaults are false. The consumer artifact is
the subject-coexistence build: source 3d4dcb35cab58a02aee7fa3dc7804d5a1790b7d0,
SHA256 189717cc9c16df3bf1842ece8f3312306ea55d526278d6c54424992a6348210d, interface
3d4dcb35cab58a02aee7fa3dc7804d5a1790b7d0. That source contains acquisition PR54 and
has the same tree as merged homelab-cli main 8350d751, which reproduces the same
bytes with `package.sh` and Go 1.25.0. The acquisition and coexistence gates
therefore accept one artifact. The earlier pre-coexistence candidate (source
4e6f8744, SHA256 8516cc4b) is no longer admissible because installing it would
drop configured subjects. No live configuration selects acquisition.

Under coexistence, purpose_grants is mandatory and admits only the catalog,
writer, importer, torrent and seed_join purposes, in both this validator and the
Go consumer. An acquisition token matches none of them and is refused at token
verification. Enabling acquisition alongside two subjects therefore needs a
separately reviewed purpose extension; this artifact pin does not provide one.

The Sonarr acquisition descriptor extends the existing importer shape only with
acquire_download_origins=[https://prowlarr.ryzhkov.dev]. The worker validates this
separately and strips it before the legacy importer validator. Optional group988
read custody applies only to this new Sonarr path with owner1002; legacy importer
validation remains unchanged. XML keys are read in place by the existing guarded
reader. qbit-stage.json reuses qbit-auth.json and has resource homelab-qbit-stage,
category holonet-stage and the fixed staging prefix. Existing Movies qBit inputs
remain unchanged. Existing root metadata guards cover both new descriptors.

Pending, not established by source/tests: actual installed qBit/Sonarr semantics,
complete Arr consumer/category/root/remote-alias/incomplete-path isolation,
share-limit override honored, duplicate/merge-tracker/concurrent writer behavior,
exclusive staging ownership/export permissions, private-indexer rights,
extractor sandbox qualification and actual English-audio release availability.
Check-then-add and sequential observations are not a production fence. Keep start
and media disabled until the consolidated reviewed packet establishes their gates.
No live access is requested by this source document.

Primary source checks: [qBittorrent release branch controller](https://github.com/qbittorrent/qBittorrent/blob/release-5.3.0/src/webui/api/torrentscontroller.cpp)
shows the multipart add fields and JSON result; this branch is not installed-image
provenance. [Sonarr 4.0.20.3014 language parser](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Parser/LanguageParser.cs)
does not establish audio-track identity from release language tokens.

Activation requires one explicit consolidated access approval after combined
source/synthetic review, CI and protected merge. Install compatible worker and
consumer first, then read configuration/grant, allowed/denied native checks, then
only the already-approved start/media flags. No repeated routine candidate
selection permission is needed. Disable the consumer feature before removing
producer/policy/grants. Retain the existing journal and unknown outcomes; never
remove fields to force an older binary to load them.

No cleanup hold, media deletion/move/purge, schedule, production Fence adapter,
GOAT replay, NAS console probe or NFS/power-loss claim follows from this work.
