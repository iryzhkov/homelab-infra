# OV native mutation successor

This selection fixes two failures observed in ordinary Codex and Claude sessions.
The backend permits USER resources vectors-only reindex only after READ and MANAGE,
and checks every bounded effect URI under the original lease. HTTP preserves a
known write commit when verification is busy and gives move/reindex finite longer
deadlines capped by JWT expiry. There are no retries, new grants or credentials.

## Exact artifacts

Backend source 98b5466b666d9f5597443b896c5299471e3dc159, tree
07d712cc767a1c952b7eff3d2190dff0ed0e77c7, passed three native cases and six
source-identical authorization cases. Both independent reviews accepted it.
Inherited public fork workflows are disabled; empty CI status is not a pass.
Chaldea assembly a3a47428-0847-4b82-9a77-d928ea340ca3 succeeded. All 20 custody
entries were independently checked. The config ID is 93d5fb1320e942b4471a75e2e708fc48bf06ee08f04bafc4e7110c6dc525d980.
Native bytes, dependencies and base are unchanged. Assembly proves file custody,
not target runtime acceptance.

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
