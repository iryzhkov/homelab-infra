# Retired private infrastructure CI runner

The public repository uses standard GitHub-hosted CI. The `ci_runner` role is
retained as a historical implementation reference and is no longer invoked by
`playbooks/ci-runner.yml`.

That playbook now stops and disables only
`actions-runner-homelab-infra-1.service` on agent-a. It preserves runner files,
other repositories' runners and application services. Remove the matching
GitHub runner registration before making the repository public.

Do not register or start this runner for a public repository. An untrusted pull
request can change its workflow labels to request a self-hosted runner.
Restoring self-hosted execution requires a separate private-repository decision.

See [the hosted CI guide](../../docs/github-hosted-ci.md) for retirement,
verification and recovery.
