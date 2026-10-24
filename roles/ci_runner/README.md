# Retired private infrastructure CI runner

The public repository uses GitHub-hosted CI. This role is retained as a historical
implementation reference; `playbooks/ci-runner.yml` now stops and disables its
service. Do not register or start it for this public repository. See
[the hosted CI guide](../../docs/github-hosted-ci.md) for retirement and verification.

## Historical setup

`playbooks/ci-runner.yml` declares a dedicated repository-specific GitHub Actions
runner on agent-a. Existing UpKeeper and homelab-cli runners are untouched.
The official archive is SHA256-verified. The runner may update itself through
GitHub's normal runner update mechanism.

The service has its own runner, HOME and temporary directories, lower CPU/I/O
weights than ordinary worker tasks, and bounded service memory. It follows the
existing runner account convention (`igor`, with Docker access); separate HOME
directories are not a security sandbox against jobs running as that same user.
The private repository's reviewed code is the trust boundary.

Deployment creates files without registering or starting an unrelated runner:

```sh
ansible-playbook playbooks/ci-runner.yml
```

Register a fresh runner using the repository's short-lived registration token
and the official `config.sh --unattended` flow, with URL
`https://github.com/iryzhkov/homelab-infra`, name
`agent-a-homelab-infra-1`, labels `homelab-infra,agent-a`, and work directory
`_work`. Keep the token out of logs and source. Then reapply the play;
it verifies the non-secret registration repository before enabling the service.
Registration credentials stay in the private runner directory, not Git.

The workflow targets `[self-hosted, Linux, X64, homelab-infra, agent-a]`.
Its Python 3.11 container is immutable, limited to 512 MiB and one CPU.
ShellCheck is installed inside that disposable container; it does not install
packages or modify system Python on the agent host.

To replace the host, deploy a fresh distribution on the replacement, register
it with the declared repository/labels and a unique runner name, confirm a
successful check, then remove the old registration and service. Do not copy
another repository's credentials or repurpose its active runner.
