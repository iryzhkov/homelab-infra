# Resource cohort signer rotation

This is source-only P3.2b, implementing design
jocasta:06bbb27983506e80b49b9972f9aa77bc@2 and S1
jocasta:102f05bb838d3158574916e42c3698a4@1. Nothing enables routing,
installs a controller or enables a timer on merge. Run the following procedure
only under separate rollout authority. Household SSO keeps
8bbd6516-af33-4e64-a73b-caa39e692c29 permanently.

## Protected inputs and ownership

The only supported administrative controller is omarchy-pc. Keep the existing
SOPS administrative access and SSH access as igor to homelab. Root reads the
encrypted SOPS file in-process; an optional sops_age_key_file names the existing
protected age key, never a copy. The service uses the installed sops, curl,
timedatectl, SSH and feed commands. python-cryptography parses only public
certificates. SSH and feed run as igor, preserving existing access. Only public
JWKS bytes go through SSH. The remote public directory is
/srv/swarm/observability/jwks, owned by root, mounted read-only into the existing
observability nginx on homelab. Move the directory and publication destination
together with any future observability relocation.

Install with playbooks/authentik-signer.yml only after preparing a root-owned,
0600 JSON configuration outside the checkout. Set authentik_signer_install=true,
authentik_signer_config_source to its absolute path, and leave
authentik_signer_rotation_enabled=false. The play installs a disabled root timer
and /usr/local/sbin/authentik-signer. The persistent daily timer has a one-hour
random delay and no restart loop.

The public configuration contains:

- sops_file, the existing encrypted administrative file's absolute path;
  optional sops_age_key_file, its existing protected key-file path.
- edges, the two edge IP addresses. curl --resolve checks each independently
  while retaining auth.ryzhkov.dev SNI, certificate validation and Host.
- replicas, explicit HTTPS origins for every Authentik serving replica. Each must
  reach that replica independently, with valid TLS. A load balancer URL alone is
  insufficient. Keep Authentik's configured issuer unchanged.
- cache_ceiling, the verified installed maximum JWKS cache, at most 300 seconds.
- max_inflight, the verified upper bound on requests that can complete issuance
  after signer readback, between 0 and 60 seconds. Unbounded signer/process
  caching blocks activation.
- providers, exact protected ownership records, each with pk, slug, body and
  canary_audience. body contains every field in the controller's FIELDS allowlist,
  excluding signing_key. It includes grants, mapping IDs, flows, lifetimes,
  encryption, issuer, redirects and federation fields. Preserve the existing
  owner/receipt/mapping evidence; a name or a discovery response is not ownership.

The required original cohort is chaldea-jocasta/102,
jocasta-managed-interactive/106, jocasta-laptop-interactive/105,
ov-http-a63e197f/42, chaldea-holonet/57, shizune-mcp/112,
ha-http-a63e197f/48, homelab-observer/45 and chaldea-host-rotation/113.
Reconfirm these against the protected ownership packet. Add chaldea-akasha only
after its owning enrollment returns its exact PK. Every included provider must
have null encryption and access validity seconds=N with N <=300.

The same protected SOPS document supplies authentik_api_token and signer_canaries.
signer_canaries maps each provider slug to its existing private token-exchange
form, including grant_type and the necessary client/resource fields and
credentials. Only client_credentials or refresh_token canaries are supported.
For an interactive provider, arrange an existing usable private refresh grant;
do not add issuance grants to production providers to satisfy this controller.
Canaries must remain usable on every replica. A rotating refresh-token setup
needs its existing protected refresh mechanism; this controller does not persist
replacement user credentials. Missing or expired canary credentials block
rotation and trigger the retryable alert.

snapshot performs only administrative/public GETs and emits the public,
allowlisted provider bodies into a candidate configuration. It does not approve
ownership or change the protected configuration. Redirect its output to another
0600 file, review against the ownership packet, then install that reviewed file.
Never log complete API responses, SOPS plaintext, OAuth secrets, JWTs or private
keys.

## Coexistence and activation

1. Keep the timer and both edge routes disabled. Deploy the observability
   directory mount and nginx configuration using the existing stack procedure.
   Confirm ingestion is healthy and /signer-jwks.json returns 404 while no file
   exists. Nothing changes discovery, issuer URLs, verifier pins or binaries.
2. Install the disabled controller as described above. Run
   sudo /usr/local/sbin/authentik-signer bootstrap. This journals one staging
   provider, one hidden-purpose application, a return False expression policy
   and its sole enabled, non-negated deny binding. The staging provider has
   grant_types=[], no encryption and no resource mappings. It cannot issue
   tokens to real subjects. Every returned ID is journaled before continuing;
   every declared body is read back. An uncertain bootstrap create stops for
   exact-ID operator reconciliation and is never retried or adopted by name.
   The same staging provider is reused every rotation. Preflight refuses changed
   grants, policy, binding, application linkage, duplicate names or foreign
   application/backchannel references.
3. Run sudo /usr/local/sbin/authentik-signer publish. It publishes the current
   original key only. It verifies the native single-sig JWK against the public
   certificate at bootstrap. Its public document contains only kid, kty, use,
   alg, n and e. Obtain that same document through homelab's existing nginx.
   A failed edge check is expected while routes are still disabled: publication
   remains in place, signing remains original, and a sanitized alert is queued.
4. Enable authentik_signer_routes_enabled=true for the first edge only in a
   protected inventory override and run playbooks/traefik.yml limited to that
   edge. Compare the exact JWKS bytes through that edge against the published
   document from each consumer network. Verify already-issued, still-valid
   tokens with the actual installed Jocasta, OV, HoloNet, Shizune, HA, Observer,
   fleet headers helpers and Akasha verifier when enrolled. Check both warm and
   cold caches, pinned issuer/audience, grant checks and expired-token refusal.
   Check household JWKS, discovery, token, authorization and logout paths still
   go to Authentik. POST and non-exact JWKS paths must not match the static route.
5. Repeat step 4 for the second edge. Keep both explicit IP/SNI probes and every
   consumer network in the evidence. Require identical bytes on both edges.
   Run publish again; require success. Do not switch until every installed
   consumer passes.
6. Run the disposable live canary below. Verify the protected replica origins,
   bounded in-flight issuance, clock synchronization, installed cache ceiling,
   root's existing SOPS access, homelab public-file delivery and igor's feed
   delivery. Then explicitly set authentik_signer_rotation_enabled=true and
   converge only the controller role. Inspect systemctl cat authentik-signer.timer
   and systemctl list-timers. Bootstrap starts the 30-day interval; no immediate
   production rotation is required.

Routes use exact Host, Path and GET/HEAD rules with priority 10000 on both edges.
They rewrite only the listed JWKS paths to nginx's exact /signer-jwks.json
location. nginx permits GET/HEAD, returns application/json and Cache-Control:
no-store, and retains the existing authenticated ingestion locations.

## Recovery and enrollment

generation.json under /var/lib/authentik-signer is root-owned 0600 with a 0700
parent. cohort.lock covers rotation, rollback and the supported enrollment
wrapper. Every transition uses file fsync, atomic replacement and directory
fsync. The root-owned 0644 /etc/authentik-signer-generation.json contains only
public generation/contract information for existing enrollment modules.
A stale public projection cannot authorize a wrapper while the canonical journal
has an unfinished rotation.

Rotation creates one RSA-4096 successor with a 90-day certificate in Authentik.
The successor's kid comes only from the grantless staging provider's native
JWKS and n/e must match the public certificate. Private-key endpoints are
refused. A unique journaled generation name and pre-create inventory reconcile
a lost response; an uncertain zero-match create stops without a second POST.
Native checks still demand one signing key; the strict edge validator accepts
only the journal's one or two public generations, with no private/unknown fields,
invalid encodings or duplicate kids.

Publication must match exact bytes on both edges before the real 600-second
monotonic sleep and again immediately before switching. Recovery restarts that
sleep. Each production PATCH contains only signing_key. Every depended-on
provider field is read back, so Authentik serializer defaults cannot silently
change grants or ownership. Private canary exchanges check RS256 signatures,
actual kid, pinned issuer/audience, <=300-second lifetime and <=60-second clock
skew at every configured replica.

Failure or crash during a partial switch restores old issuance using GET
reconciliation and keeps both public keys. Retirement waits a fresh real
600 seconds plus max_inflight after successful all-replica canaries, then
rechecks state and canaries. A later old signer or kid blocks retirement and
restores the previous signer. Publication failure never changes the old signer
before the switch. A post-switch network outage still cannot promise consumer
availability. Failure delivery uses feed post --source feed --severity fail.
The journal retains alert=true until delivery succeeds; each invocation retries.
Failures alert immediately, which also covers a failed rotation by day 45.
An offline controller delays rotation without expiring the active signer.

Run sudo /usr/local/sbin/authentik-signer rollback for manual rollback, using
the same journal, lock, publication and expiry rules. The previous key must
still be retained. A completed retirement cannot roll back to a deleted key.
Do not clear a pending journal, edit UUIDs to bypass drift, or retry an uncertain
create. Retain receipts and reconcile the exact API-returned objects first.
Only journal-owned predecessor keys with no used_by references can be deleted.
The household original is never deleted, even if marked owned in error.

Run supported enrollment from the existing checkout through:

```sh
sudo /usr/local/sbin/authentik-signer enrollment ansible-playbook playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS"
```

The wrapper holds cohort.lock for the whole invocation and runs Ansible as igor,
preserving existing receipt ownership and protected SOPS access. The managed
interactive enrollment uses the same wrapper. An unfinished rotation refuses
enrollment. Approved stale declaration pins resolve to the current signer,
and exact-PK provider receipt bodies are rebased only for that field. All other
ownership, mapping, role, grant and revocation checks remain. New provider
adoption by name and foreign signing UUIDs remain refused.

Legacy machine-identity and Jocasta operator blueprint writers are frozen after
enrollment on omarchy-pc and fail before API mutation. Do not run administrative
writers on another controller or reapply frozen stored blueprints. Stored
blueprint ownership content hashes remain unchanged and continue to anchor
the original resource/mapping ownership; live provider signer evidence now
comes from the generation record. No broad arbitrary-key exception was added.

When a reviewed owning enrollment adds Akasha or intentionally changes a
depended-on provider field, retain its receipt and protected baseline, install
the reviewed updated configuration, set authentik_signer_akasha_enrolled=true
on both edges only after its exact-PK enrollment, and run
sudo /usr/local/sbin/authentik-signer approve-contracts. This explicit
rebaseline requires idle state, the exact existing provider PKs, no removals,
current signing, complete live field equality, safe lifetimes, staging isolation,
both edges and all replica canaries. It never repairs live drift. Ordinary
stale inventory cannot approve itself.

## Live canary, performed later by root

Use disposable real Authentik providers and private subjects, with every actual
verifier implementation from the design inventory. Warm caches immediately
before publication. Publish the successor, wait the real 600 seconds, and
continuously issue and verify across the switch. Verify still-valid old tokens
with warm and cold caches, refuse expired tokens, and retire only after at least
600 seconds after the last old issuance, including bounded in-flight requests.
Exercise every replica. Prove RSA-4096 acceptance in each installed verifier.
Inject signer/API/publication failures and crashes at journal boundaries.
Require old issuance to remain or be restored, overlap to remain, and the feed
alert to arrive. Fail feed delivery once and prove persisted retry on the next
invocation. Reapply a stale enrollment packet and verify current signer
convergence without household/foreign key changes. Never crash the production
enrollment writer. Record public IDs, timings, statuses and public JWKS digests
only. Keep all tokens, credentials and private keys out of receipts and logs.

The offline suite tests journal recovery, duplicate-create refusal, strict JWK
validation, publication failure, real-wait scheduling, partial rollback, stale
issuance, feed retry, stale enrollment, household/foreign key preservation,
fsync boundaries and route scope. It also exercises RSA-4096 using
cryptography/PyJWT 2.15.0, Node jose 6.2.12 and Go crypto/rsa. These libraries
have no upper RSA-3072 cap; actual installed-verifier/network acceptance is the
live gate above, not a claim made by the offline tests.

## Design deviations

RSA-4096 replaces RSA-3072 with Igor's explicit approval. Authentik 2026.8.5's
[generator](https://github.com/goauthentik/authentik/blob/version/2026.8.5/authentik/crypto/builder.py)
hardcodes 4096 bits. Its [keypair API](https://github.com/goauthentik/authentik/blob/version/2026.8.5/authentik/crypto/api.py)
does not return kid. The single receipt-owned grantless staging provider and
deny-all application obtain that public kid without reading or hashing private
keys, ORM operations or an admin shell. That provider is reused, never created
per rotation.

Recovery uses a fresh conservative retirement sleep as well as a fresh
publication sleep; a wall-clock jump cannot shorten overlap. A root-owned public
projection and locked wrapper let existing igor-owned receipts share the root
controller lock without transferring receipt ownership. These checks and the
staging lifecycle exceed the design's original 450..650-line estimate.
