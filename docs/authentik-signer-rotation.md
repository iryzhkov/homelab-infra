# Resource cohort signer rotation

Source-only P3.2b, per [the adopted design](jocasta:06bbb27983506e80b49b9972f9aa77bc@2)
and [S1 receipt](jocasta:102f05bb838d3158574916e42c3698a4@1).
Nothing installs a controller, enables routes or starts a timer on merge.
Household SSO keeps 8bbd6516-af33-4e64-a73b-caa39e692c29 permanently.

## Protected inputs

Use omarchy-pc as the sole administrative controller, with its protected inventory
entry and existing SOPS access. Root reads SOPS in-process; sops_age_key_file
points to the existing age file, never a copy. SSH and feed run as igor.
Only public JWKS bytes travel to homelab through existing SSH and sudo -n.
The root-owned /srv/swarm/observability/jwks directory is mounted read-only into
the existing nginx. Move it and the publication destination together if relocated.

Prepare root-owned 0600 /etc/authentik-signer.json outside the checkout:
- sops_file and optional sops_age_key_file are existing absolute paths.
- edges lists both edge IPs; curl --resolve preserves auth.ryzhkov.dev SNI/Host.
- replicas lists exactly one direct HTTPS Authentik server origin with valid TLS.
- cache_ceiling is the verified installed maximum, at most 300 seconds.
- max_inflight is the verified issuance completion bound, between 0 and 60 seconds.
  Unbounded signer caching blocks activation.
- providers contains exact owned pk, slug, canary_audience and body records.
  body has every controller FIELDS member, excluding signing_key. Retain the
  protected owner/receipt/mapping evidence; discovery or names are not ownership.

Original cohort: chaldea-jocasta/102, jocasta-managed-interactive/106,
jocasta-laptop-interactive/105, ov-http-a63e197f/42, chaldea-holonet/57,
shizune-mcp/112, ha-http-a63e197f/48, homelab-observer/45,
chaldea-host-rotation/113. Reconfirm against protected ownership evidence.
Add chaldea-akasha only after its owning enrollment returns an exact PK.
Every provider needs null encryption and access validity seconds=N, N <=300.

The protected SOPS document supplies authentik_api_token and signer_canaries.
Canary forms must use client_credentials, exactly for providers enabling that
grant. Refresh-token forms are refused in preflight: Authentik can rotate and
revoke them on use. Providers without client_credentials use exact signing_key
API readback plus max_inflight on the single Authentik server; no token exchange
is attempted. Do not add production issuance grants to satisfy the controller.
Expired/missing canary credentials block rotation and queue a sanitized alert.

snapshot uses GETs to emit allowlisted public provider bodies into a candidate
configuration. Redirect to a 0600 file, review against ownership evidence and
install the reviewed file. It does not approve ownership.
Never log complete API responses, SOPS plaintext, credentials, JWTs or private keys.

## Coexistence and enablement

1. Keep the timer and both edge routes off. Deploy the observability directory
   mount and nginx configuration using the existing stack procedure. Confirm
   ingestion remains healthy and /signer-jwks.json is 404 before publication.
2. Run playbooks/authentik-signer.yml with the protected omarchy-pc inventory,
   authentik_signer_install=true and authentik_signer_config_source pointing to
   the protected JSON. Leave authentik_signer_rotation_enabled=false.
   Run sudo /usr/local/sbin/authentik-signer bootstrap.
   Bootstrap freezes the machine-identity and Jocasta operator blueprint writers
   on omarchy-pc. Never run those writers from another controller.
   Disable stored cohort blueprint instances, or retain this explicit warning:
   manually applying a frozen instance reverts its providers to the original key.
   Bootstrap journals one reused grantless staging provider, its application,
   deny policy and binding. Uncertain creates require exact-ID reconciliation.
3. Run sudo /usr/local/sbin/authentik-signer publish to publish the original key
   only. An edge check can fail until routes are enabled; signing stays original.
4. Set authentik_signer_routes_enabled=true on one edge at a time and apply
   playbooks/traefik.yml limited to that edge. From every consumer network,
   compare exact public bytes and verify existing tokens with warm/cold caches
   in every installed verifier. Require issuer/audience/grant checks and expired
   token refusal. Check household JWKS, discovery, token, authorization and logout
   still reach Authentik. Non-exact paths and POST must not match the static route.
   Repeat for the second edge and require identical bytes through both.
5. Before enabling rotation, prove publication, SSH, sudo -n and feed from the
   systemd unit's own context. With generation.json in idle and the timer off,
   create only this temporary runtime override:

```sh
sudo install -d /run/systemd/system/authentik-signer.service.d
sudo tee /run/systemd/system/authentik-signer.service.d/coexistence.conf >/dev/null <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/local/sbin/authentik-signer publish
ExecStartPost=/usr/bin/env runuser -u igor -- feed post "Signer coexistence probe" --source feed
EOF
sudo systemctl daemon-reload
sudo systemctl start authentik-signer.service
# Require successful publication, exact bytes on both edges and the feed entry.
sudo rm /run/systemd/system/authentik-signer.service.d/coexistence.conf
sudo systemctl daemon-reload
sudo systemctl start authentik-signer.service
# Require the normal idle rotation check to succeed without a signer change.
```

6. Complete the design's disposable live canary with every installed verifier,
   including RSA-4096, real 600-second waits, failure injection and feed retry.
   Then set authentik_signer_rotation_enabled=true and converge only this role.
   Inspect systemctl cat authentik-signer.timer and systemctl list-timers.
   Bootstrap starts the 30-day interval. No live canary was performed by this PR.

## Operations and rollback

The design owns recovery, overlap, retirement, failure and live-canary details.
The controller uses a root-owned fsynced journal and common lock. Failed feed
delivery stays journaled for retry. Failures while still in switch leave it queued
for retry; partial switches restore old signing while retaining overlap.
Manual rollback uses sudo /usr/local/sbin/authentik-signer rollback.
It refuses prune, delete and idle before changing signers: once public-key
removal starts, even an unfinished retirement cannot roll back to that key.
Never clear pending state or reconstruct ownership to bypass an uncertain create.

Use sudo /usr/local/sbin/authentik-signer enrollment ansible-playbook
playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" from the checkout.
The wrapper preserves igor-owned receipts while holding the root controller lock.
Use the same wrapper for managed-interactive enrollment.
For reviewed field changes or Akasha, install the updated protected configuration,
retain owning receipts and run sudo /usr/local/sbin/authentik-signer approve-contracts.
This checks live fields, current signer and staging isolation; it does not check
both edges. Verify both explicitly with publish before enabling/resuming rotation.
Set authentik_signer_akasha_enrolled=true on both edges only after its enrollment.

RSA-4096 and the reused deny-all staging provider are approved deviations: the
Authentik generator hardcodes 4096 and its keypair API omits kid. Public n/e must
match the certificate; uncertain successors need at least 60 days remaining.
The offline suite retains PyJWT RSA-4096 verification; the design's live canary
is the acceptance gate for every installed verifier.
