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
preparation effect and not global writer exclusion. No cleanup removes it on
failure. Add attempts with lost replies remain uncertain; an ACK survives a
readback/logout failure. Status requires matching hash/category/tag/exact path.
Add readback and later status also require actual per-torrent Custom/Stop and
unlimited ratio/time fields; missing fields or a global/removal fallback refuse
qualification, without an automatic settings repair or another add.
Each add explicitly disables the global incomplete path and supplies the same
operation directory as its per-download path. Readback/status refuse an external
download_path or enabled auto_tmm; final-savepath isolation alone is insufficient.
Installed-version behavior still needs isolated qualification before activation.
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
request branches remain separate. Defaults are false. The exact candidate consumer artifact is source22be0abe26ea5418021a47ace69e2f51e64db300,
SHA25628a4542455871b52e13155a4201d5de46a6bd1c8f7f916f7bc43a26c78860c33,
interface22be0abe26ea5418021a47ace69e2f51e64db300. It remains a candidate until
independent source review and CI pass; no live configuration selects it.

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
