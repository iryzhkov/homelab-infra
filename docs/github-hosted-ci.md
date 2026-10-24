# GitHub-hosted CI

The Check workflow runs every suite on a separate standard `ubuntu-24.04`
GitHub-hosted runner. Each job retains its checksum-pinned Python 3.11 container,
CPU and memory limits, scratch HOME checks, and complete test commands.
No deployment credentials or private-network access are required.
Superseded revisions of the same pull request cancel; main-branch runs remain
independent and are not cancelled by later pushes.

Standard GitHub-hosted execution is free for public repositories.
Private-repository execution uses the owner's included minutes and billing rules;
this workflow does not change spending limits or request larger runners.

## Retire the previous runner before publication

Remove the `agent-a-homelab-infra-1` registration from this repository's
Actions runners settings, then run:

```sh
ansible-playbook playbooks/ci-runner.yml --check --diff
ansible-playbook playbooks/ci-runner.yml
ansible-playbook playbooks/ci-runner.yml --check --diff
```

The playbook stops and disables only
`actions-runner-homelab-infra-1.service` on agent-a. It preserves runner files,
other repositories' runners, and all application services.
Verify that this repository has no self-hosted runner registrations before
making it public. An untrusted pull request can change its workflow labels;
changing the default workflow alone does not protect a registered homelab runner.

Confirm the repository history, discussions and retained Actions output are
appropriate for publication. Public visibility exposes that history and output
as well as the current source. SOPS ciphertext is not a plaintext credential,
but inventory addresses, hostnames and key identifiers are visible metadata.

## Verification and recovery

A successful migration shows every Check job assigned to a GitHub-hosted runner
and all checks passing on the exact reviewed commit. Keep the repository-specific
homelab runner disconnected while the repository is public. If a workflow fix is
needed, submit it against the hosted setup. Restoring self-hosted CI requires a
separate private-repository decision; making a published repository private again
cannot revoke copies already taken.
