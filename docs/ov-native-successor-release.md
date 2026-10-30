# OV native mutation successor

Historical resource-only release. The selected replacement and current live hold
are in [the personal qualified release](ov-personal-qualified-release.md).

This selection fixes failures observed in ordinary Codex and Claude sessions.
The backend permits USER resources vectors-only reindex only after READ and MANAGE,
and checks every bounded effect URI under the original lease. HTTP preserves a
known write commit when verification is busy and gives move/reindex finite longer
deadlines capped by JWT expiry. There are no retries, new grants or credentials.

## Exact artifacts

Backend source 7adf8b5fbcaa3293ef2a0b0ed44f864477401c39, tree
33213b6dc98651a58efad0190af9ab6bb4c6fa17, retains the USER reindex fix and
acknowledges qualified no_replace moves after committed transfer and durable
parent refresh dispatch. It no longer waits for descendant summary Work.
Those Work items remain tracked; later failures do not change an already
acknowledged commit into a synchronous PROCESSING_ERROR. The ACK exposes no
Task ID for polling and makes no revision-bound indexing claim.
Both final reviews accepted the source. Chaldea f2099491-2418-478a-98e7-7878e717f4e4
passed 73 TaskTracker and four real native move cases, plus an expected old-policy
red regression. Earlier full FSService fixture failures remain recorded separately;
this is not a full-suite green claim. Inherited public fork workflows are disabled;
empty CI status is not a pass.
Chaldea assembly ebcc2ead-a53c-402e-886d-b36ff40f6fa5 succeeded. All 20 custody
entries were independently checked. The config ID is
12c8b14fde92eec32a3cc851d630e98acfb885a0ae580e4b0b76316393661ae3.
Only FSService changes among the 915 runtime files. Native bytes, dependencies
and base remain unchanged. Assembly proves file custody, not target runtime acceptance.

HTTP source d5b1b104b5a39f15c9443c117bca9dd1c52eac68, tree
f21fd8c04725279482a50914a797c339174e34ae, passed 13 focused SDK and commit-state
regressions through Chaldea. Both independent reviews accepted it. The earlier
271-case run predates the final fixes. Public artifact run
https://github.com/iryzhkov/ov-mcp/actions/runs/40992612927 succeeded.
The config ID is 9fc44fb1bb2410e327fd450cd4a83a46d36ed851ca2724fb9a13f30edfcb1e31.
The existing full-resource contract remains 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319.

## Activation hold and recovery

Keep this batch unmerged until source PR CI, artifact custody and the infra
selection checks/reviews are reconciled. Before convergence, load the exact
artifacts on the existing owning daemon and verify their config IDs, source
labels and unchanged native/platform controls. Preserve the current config,
account creation identities, mounts, keys, inference service and client grants.
Use the existing owning stop-first backend and HTTP role paths. No cold cutover,
account initializer, queue drain or old-data restore belongs to this upgrade.

The original cutover has admitted writes. Do not roll back to the old image or
pre-cutover data. On failure retain the current identity and data, reconcile the
owned service state and repair forward. Unknown dispatched mutations must not be
replayed. Keep stdio OV until serial native acceptance covers default overwrite
with its observed-node CAS, file-deferred CAS/stale refusal, move, reindex,
archive publication and cleanup, followed by the retirement audit.

This source batch does not claim target load, runtime acceptance, four-client
mutation acceptance or stdio retirement. Those are root-owned next steps.
