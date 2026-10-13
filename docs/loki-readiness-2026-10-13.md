# Loki readiness after restart — 2026-10-13

The pinned Loki 3.7.8 image reproduces a delayed-first-probe readiness gate.
It does not require a second restart to pass that gate. The return playbook
now repeats the native HTTP readiness request rather than instructing an
operator to force-restart Loki merely because the first request says
"waiting for 15s after being ready".

## Exact mechanism

In the pinned version's vendored dskit Lifecycler.CheckReady, the readiness
timer starts only when a call finds healthy ring state. It is initialized in
CheckReady, not at process startup. A first readiness request can therefore
start the 15-second counter after many minutes of uptime. The text reports the
configured duration, not a countdown. Once the timer expires and ring checks
still pass, readiness latches true. A ring-check failure resets the timer and
returns that separate failure reason.

Primary source for the exact version:
https://github.com/grafana/loki/blob/v3.7.8/vendor/github.com/grafana/dskit/ring/lifecycler.go#L234-L257

The ingester delegates its readiness check to that lifecycler:
https://github.com/grafana/loki/blob/v3.7.8/pkg/ingester/ingester.go#L1423-L1427

WAL replay also precedes readiness:
https://grafana.com/docs/loki/latest/operations/storage/wal/

## Isolated reproduction

A single scratch container on homelab used the existing pinned image
git.ryzhkov.dev/igor/mirror/docker.io/grafana/loki@sha256:e664166bca56031661b40a0a0b1c77dfeffa4b03053cc887836dd4f51cb212ab.
Build info reported version 3.7.8, revision 09e6ce2f.

It had the repository's existing Loki configuration, a new empty scratch data
directory, network=none, no published ports, local log driver, 512 MiB memory
limit and 0.5 CPU. It had no live data/config mounts and did not send logs to
live Loki. Separate short-lived helper containers shared only its isolated
network namespace to make HTTP requests. They reused the already pinned
VictoriaMetrics image's native wget/nc; no custom probe binary or derived
image was built.

| Test | Observed result |
| --- | --- |
| Cold start at 17:00:41Z; no earlier readiness probes | First /ready at 17:01:18Z returned 503 waiting for 15s, despite more than 36 seconds uptime |
| Repeat without restart or config change | /ready at 17:01:56Z returned 200 ready |
| Graceful scratch stop and start | Initial probe reported services Starting, then a healthy-ring probe at 17:03:57Z started the 15s gate; a later repeat at 17:04:30Z returned 200 |
| Historical clean marker | The acknowledged marker from before the clean restart remained queryable |
| Kill only the scratch container with SIGKILL; start it again | First delayed healthy-ring probe at 17:05:15Z returned the same 503 gate; repeat at 17:06:17Z returned 200 without another restart |
| WAL recovery after unclean stop | Both acknowledged clean and unclean marker records remained queryable |

The graceful scratch stop completed in under one second. This test does not
justify changing the live stop grace period; larger production WAL/checkpoints
need their own evidence. No memory OOM occurred in the scratch reproduction.

The live Loki container remained running throughout, with its original
2026-10-13T09:11:10Z start time and /ready HTTP 200 before and after the tests.
No household service was restarted or moved. Scratch containers and storage
were removed after recording the results.

## What this proves and what it does not

The 2026-10-03 return record says the 15-second message was still observed
17 minutes after return, but it contains no timestamped sequence establishing
repeated healthy probes more than 15 seconds apart. The isolated test confirms
a mechanism that can produce that observation without a persistent fault.
It cannot retrospectively prove that this was the only cause of that incident.

The prior runbook's stale-instance explanation is unsupported for this exact
message: ring health has already passed when CheckReady reports the duration
gate. Moreover, the declared KV store is process-local in-memory state.
A fresh Loki process does not restore an old ring entry from its persisted WAL.

The distinct original phase 5 error, self-dialling the unreachable Swarm
ingress IP on port 9095, was a real advertised-address problem. Existing
common.instance_addr=127.0.0.1 already fixes it for the current single binary.
Do not conflate that timeout with the probe-driven readiness timer.

## Return acceptance

playbooks/host-return.yml requests homelab's /ready every five seconds, with
24 retries and a five-second request timeout, before refreshing snapshots and
clearing the maintenance marker. It does not force another Loki restart.
If readiness fails to converge, the return fails and preserves that marker.
Collect the actual response sequence, ring states, module states, WAL replay
timings and sanitized errors to investigate the remaining cause.

Readiness and actual ingestion are separate acceptance checks: verify fresh
log delivery and a fixed historical marker/query. A first 503 duration gate
alone does not establish that log entries were dropped.

The current image is shell-less: sh is unavailable and it does not contain a
verified native wget/curl healthcheck path. Therefore this fix uses native
Ansible URI requests and does not add a misleading container healthcheck.
Logging movement guards remain unchanged until legacy clients and Influx
writer dependencies are retired.
