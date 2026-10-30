# Pure service-access planner (M1 source preparation)

This opt-in version-1 interface inspects explicit resolved JSON only. It does not
evaluate inventory/templates, collect controller placement, render configuration,
call services or apply changes. Existing roles are not wired to it.

Run: python3 scripts/plan-service-access.py INPUT.json [--baseline BEFORE.json].
Hypothetical moves use --move-set app --available auth dns. Exit0 means no model
diagnostics, not deploy readiness; exit2 means rejected input or unresolved policy.
Every result has deployment_ready:false. model_equivalent is only supported-model
comparison and is false with diagnostics or unmodelled routes.

## Version-1 input

The full synthetic example is sample() in scripts/test-service-access.py.
Required top-level fields are version:1, resolved:true, exact40-character
source_pin, placement, channels, backends, services and unmodelled_routes.

Placement carries state present/verified-none/unknown, origin, observed_at, entries
mapping backend ID to host, and SHA256 of compact sorted-key ASCII JSON entries.
Unknown never falls back to HOME. present chooses among explicit endpoints only.
Channels carry id/context(lan,nebula,public)/owner/classification:transport and
qualified:true and a nonempty qualification_ref receipt reference. Qualification
is supplied evidence, not measured enforcement.

Backends carry id/kind(compose,swarm,native,static)/owner/provenance, explicit
host/url endpoints, strategy load_balance/failover and health data. Swarm wins
over retained Compose while retaining both provenance references. Swarm load
balancing sorts endpoints; failover order is behavior and remains unchanged.

Services require access_class ordinary/whisper/ollama independent of their id,
explicit boolean admin, id/hostname/owner/backend_ref/application_auth, all three contexts,
publication/dependencies/dns_ref/hold_ref/custody_ref/routes. Application auth
mode none/native_login/native_oidc remains distinct from proxy_auth. Native
modes require owner/coverage references; OIDC also issuer/callback/secret refs.
Never supply secret values. These references are not authenticated by this tool.
A native second gate requires a nonempty text extra_gate_decision reference; forward_auth requires
provider_ref/callback_ref/middleware_ref. Every allowed forward-auth context
needs a supported route explicitly containing that middleware_ref and a callback
object whose ref matches callback_ref. No middleware or callback is generated.
Public no-native-auth apps require forward auth.

Contexts explicitly declare allowed/channel/proxy_auth. Publication approved
also needs owner and public channel. Headers/source CIDRs cannot select context.
Public Whisper/Ollama is rejected; administration publication is unsupported.
Whisper requires application_auth:none and allowed:true/proxy_auth:none for
both LAN and direct Nebula contexts, plus exact Trusted LAN70/90/192.168.1/24/120 prefixes,
nebula_peers:all-valid-direct and controls with ordinary_denied_paths including
/load plus owner_ref/backend_policy_ref. These are policy-model assertions,
not proof of per-process or packet enforcement.

Routes carry id/context/entrypoints/rule/priority/ordered middlewares/callback/
tls/provenance. Nonempty callbacks need path/priority, proxy_auth:none and a
resolved backend_ref. Duplicate router IDs and exact rule/priority collisions on
any shared entrypoint are diagnosed before merge, regardless of context/hostname labels. General overlapping-rule reasoning,
raw Traefik parsing and special-route import remain unsupported. TLS/health
objects are preserved/compared as explicit data, not runtime-validated.

## Comparison and deferred work

Routes sort deterministically; middleware and failover ordering is preserved.
Changes include context/listeners, exact rule text, callback/priority/TLS, backend
graph/health and provenance. Rules are not parsed/algebraically normalized;
differently spelled equivalent rules conservatively compare changed.
Nonempty unmodelled_routes retain owner/exclusion data and block equality and
planning readiness. No inventory adapter/live-origin import exists. Historical
17-origin/tunnel86/Emby production coverage is not certified.

One move checker uses the same service dependency graph: refs must resolve,
cycles diagnose, and selected services require dependencies in explicit available
or moving IDs. This is not transfer/fencing/schema compatibility/cloud readiness.
Unknown operational channels/owners/custody/holds/standby/recovery remain gates.
No UPS policy/domain/cloud host/live target is selected; no OV data replay.

## Isolated checks

Use Chaldea automatic host selection only. Set HOME to absolute task scratch
ending /.cache/service-access-tests/home; SERVICE_ACCESS_TEST_HOME equal HOME;
TMPDIR beneath it. Assert HOME before mkdir/write, PYTHONDONTWRITEBYTECODE=1.
Run python3 -B scripts/test-service-access.py. Synthetic objects/explicit temporary
JSON files only; no Ansible or host invocation.
