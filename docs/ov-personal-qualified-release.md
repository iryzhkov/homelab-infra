# OV personal qualified release

Source binding only. No personal inventory enablement, grants, subjects, secrets
or live changes belong to this batch. Current resource-only runtime continues.
Root owns final review, source CI, activation and native acceptance.

## Exact bindings

Backend selected product 7f3faf6982f81116d6626bc7091f87f786a309c7, tree
58f9919d1c8a4fd04762c3d9adab4c7b01573029. Closed local config ID
sha256:33ce088da598bbc1915ba488e63330f0f40d8417e5f2783d384df6eb0f8dd98b.
Native SHA256 8be7869a9f326ccd6cb67e093216f74296d1e98e93a73290918e6f33744ea1ad
is unchanged. Assembly job 332a11ac-2d59-4d07-a360-7beca5c019c2,
patch SHA256 94b1187f0938465c9d70e2c2e174200d97d647e5c888c29b5d5e02af3add1ab1,
custody SHA256 ee0ceab462c8fec0b966a3024343bf0d514ed121dfe41cf55fed35fce71cf648.
The existing provenance archive_sha256 field names this candidate patch bundle,
not a Docker save. All 20 custody entries and 915 product files were checked.
Backend PR6 merged with publication CI assets beyond the product tree; measured
image labels remain the selected product revision. Workflows are disabled,
so no hosted backend CI pass is claimed. Selected 56 native/legacy checks pass;
the additional lifecycle timeout and unrelated policy assertion remain retained.
This is not full-suite green.

HTTP accepted e094cb32a085e9ad3fc9976aba7824db830c8999 and publication
be4cda7c376986360158fb6488b808dec90d81c2 share tree
bcb8c0e5813138aa3fbb4b03fb90475ca09bbf8d. Closed source_revision uses the
measured publication label, never an arbitrary source allowlist expansion.
Hosted run https://github.com/iryzhkov/ov-mcp/actions/runs/41114422316 succeeded.
Config ID sha256:2dfb396ca804482ed29c087d3ab258312b75c57a25848da0c1c96636a875a89f.
image.tar SHA256 547951c96232546c3d7df42e7d03f3a0f59b754fa08eb8fc733469478d25f605,
176900096 bytes. All 11 source inputs, ten saved uncompressed diffIDs and full
config were verified. Compressed registry bytes are absent and independently
unclaimed; this does not block saved-image identity. No distribution digest is
invented. Resource contract 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319 and
existing resource profiles, namespace ceiling and distribution allowlist stay fixed.

Qualification receipts live under
/home/igor/.cache/web-mcp-caafd4a9/user-session-followup-20261030/:
backend-personal-assembly-result.md and backend-personal-assembly-evidence/,
http-personal-artifact-result.md and http-personal-image-evidence/,
infra-personal-contract-result.md, http-implementation-result.md and
native-session-create-result.md. Image-only target receipts are
/home/igor/.cache/web-mcp-caafd4a9/ov-personal-backend-stage-20261030/result.md
and /home/igor/.cache/web-mcp-caafd4a9/ov-personal-http-stage-20261030/result.md.
Both PASS for image-only staging, with no deployment or new runtime acceptance.

## Live hold and owning order

After root accepts final infra review and checks, preserve current account
identities, data, mounts, inference service and credentials. Use existing owning
backend Swarm and HTTP Compose paths, fixed daemon, stop-first procedures and
host restrictions. No new deployment framework or initializer is needed.

1. Backend owning CHECK, then authorized REAL, then identical zero-change repeat.
   Verify the exact selected image, product label, native identity, service and
   resource behavior before proceeding.
2. Update the separately protected HTTP package input to the exact OV_LOCAL_RELEASE
   object in filter_plugins/web_mcp.py and a fresh immutable release ID. Preserve
   existing protected credential and resource config. Add shared-owner-v1 only
   with personal_account=default, personal_user=default, the exact privately
   approved subject list and personal_backend_revision=
   7f3faf6982f81116d6626bc7091f87f786a309c7. Never publish subjects or grants.
3. HTTP owning CHECK, then authorized REAL stage/check-config and activation,
   then identical zero-change repeat. Use playbooks/ov-http.yml with the existing
   single-host standalone inventory and explicit lifecycle switches. Preserve
   stop-first ownership and guard policy. Check mode alone is not runtime proof.
   Verify HTTP personal enablement against the qualified backend before issuance.
4. Only after backend and HTTP personal enablement are verified, opt in to issuer
   personal grants through its existing isolated owner, with CHECK, REAL and
   zero-change repeat. Then select the separately qualified new fleet helper.
   Signed actions and requested scopes remain separate. Reduced grants with
   additional known requested scopes remain valid; scopes cannot add actions.
5. Complete native personal proof through Codex and Claude on both PC and laptop.
   Four-client proof, resource continuity and later stdio retirement remain
   root-owned requirements, not established by source tests or image-only loads.

Personal stays default-off until protected deployment inputs opt in. No subject,
grant, secret or HTTP personal enablement is selected in repository inventory.
Do not roll back to old images or old data after admitted writes, replay an
initializer, or replay an uncertain mutation. Retain state and repair forward.
