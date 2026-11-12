# HoloNet subject coexistence

Source preparation only. Activation belongs to the parent M3 executor through
playbooks/web-mcp-endpoint.yml with the protected endpoint inventory.

Keep playlist_operator.subject and janitor.subject as the original writer subject.
Add subjects: [OLD_VERIFIED_SUBJECT, NEW_VERIFIED_LAPTOP_SUBJECT] identically to
both blocks. These are signed sub values, not assumed account usernames.
The root policy renderer copies this exact list to the worker's subjects field.
At most two writer generations are admitted; duplicates, empty sets and omission
of the original subject fail closed.

Dual admission requires purpose_grants, a map with these closed keys:

| Purpose | Exact scopes | Actions |
| --- | --- | --- |
| catalog | holonet.read | catalog.read |
| writer | holonet.read holonet.curate holonet.home | Existing seven writer actions plus history.read |
| importer | holonet.read holonet.janitor | janitor.importers.read, janitor.custody.read |
| torrent | holonet.read holonet.janitor holonet.torrents | janitor.torrents.read |
| seed_join | holonet.read holonet.janitor holonet.seedjoin | janitor.seed_join.read |

Each map value has subjects, scopes, namespace, libraries, catalog_users,
history_users, target_users and actions. All except namespace are string lists.
The verifier compares the complete signed tuple with exactly one configured
grant. Scope order is immaterial; duplicates and combined purposes refuse.
The issuer-side purpose selector is maintained by the host-identity builder.

Reconcile every tuple against installed protected policy before rendering.
Catalog admits up to five subjects: three current catalog identities plus
the verified new laptop and homelab subjects. It retains movies-catalog,
library 3, catalog user 54b7d36cef804d3c8478474553ed3a82 and empty history/targets.
Writer retains managed-recommendations, libraries 3/615/1132 and the existing
seven target/history users, with Series and playback opt-ins unchanged.
The three janitor purposes retain managed-janitor, library 3 and their separate
read actions, with no history/targets. Do not add acquisition/start/media or
series-acquisition grants. Importer pause and the base janitor/intent tuple
remain restricted to the original subject in the worker, even when their policy
blocks are enabled. Only the writer and three read purposes use dual admission.

Preserve the exact reviewed CLI commit through a merge commit rather than a
squash: the packaging source and checksum pins depend on its retrievability.

Admission does not alias journal ownership. Calls keep the actual signed
subject, so internal keys and pending operations stay distinct. Finish or
explicitly abandon old pending work and retain its receipts before switching
the caller. Inventory existing objects first; any needed owner migration must
be separately scoped and tested rather than transferring pending authority.

Release the Go binary at the exact source and tree in PR55 using Go 1.25.0 and
the module's package.sh. Retain provenance and SHA256SUMS. Bind the reviewed
artifact URL, source, contract and checksum in protected input, then run:

    ansible-playbook -i PROTECTED-SCOPE.yml playbooks/web-mcp-endpoint.yml -e @PROTECTED-ENDPOINT.yml

Review CHECK before REAL, require an identical repeat and exercise native
old/new positive and negative cases for every purpose before credential cutover.
Keep current issuer, configurable JWKS URL and signer. Remove old subjects only
after parent acceptance and the bounded rollback window.
