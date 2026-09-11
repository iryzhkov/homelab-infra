# Citadel foundation self-adoption

This desired-state adapter is exclusively for F04 adoption of t3-steward and
UpKeeper on the laptop controller. t3-steward remains workflow authority;
UpKeeper supplies immutable deployment intent, revision fences, effect records,
locks, observation, settlement, and compensation. This repository supplies the
exact Ansible inventory and playbook revision.

The inventory alias is laptop and is pinned to hostname omarchy-normandy with
a local Ansible connection. The playbook accepts only t3-steward or upkeeper.
Every invocation verifies the staged artifact plus the external rollback
executor, runbook, and any rollback bytes by SHA-256 and size.

Phase tags are upkeeper_check, upkeeper_install, upkeeper_activate,
upkeeper_run, upkeeper_observe, and upkeeper_rollback. Mutation tags require an
UpKeeper effect identity supplied after durable PREPARED and STARTED records.
Rollback is delegated to the frozen executor outside the managed install path;
the UpKeeper observer independently proves the resulting bytes and health.

## Prepared operation

Use a clean checkout at an exact committed revision and construct a
homelab-ansible-v1 BackendSpec with inventory/citadel-foundations.yml and
playbooks/citadel-foundations.yml. First use adopt-running for the currently
running t3-steward binary. Use bootstrap-absent for the first UpKeeper install.
Freeze target-host file sizes and SHA-256 values only after the desired-state
commit is approved.

No command in this document grants rollout authority. Before apply, an operator
must approve the exact homelab-infra and component commits, artifact identities,
maintenance window, recovery owner, and external rollback invocation. Check mode
and syntax validation do not restart a service or install an artifact.
