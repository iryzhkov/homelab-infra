# Infrastructure CI runner (dormant)

## Where CI runs now

`.github/workflows/check.yml` runs every job on GitHub-hosted `ubuntu-24.04`
runners. Nothing in the workflow targets a self-hosted label, so this role is
not on the CI path. Each job still runs inside the digest-pinned
`python:3.11-slim` container with its existing memory and CPU limits (512 MiB
and one CPU, or 2 GiB and two CPUs for the native OpenViking provider job),
well inside a hosted runner's capacity. Jobs run in parallel on separate
hosted machines, and a newer push to the same pull request cancels the
superseded run; pushes to `main` are never cancelled.

The workflow triggers on `push` to `main` and on `pull_request`, never on
`pull_request_target`. It uses no repository secrets, and the token is limited
to `contents: read`, so a pull request from a fork runs untrusted code with no
credentials. Every test step sets an explicit scratch `HOME` and `TMPDIR` and
asserts the value before writing. Paths such as `/home/job/...` or
`/home/igor/.cache/chaldea-scratch/...` are created inside the disposable job
container, which runs as root; they do not refer to any homelab machine.
Fixtures are synthetic, and the jobs reach only public package and release
URLs, never LAN hosts.

All packages (ShellCheck, Ansible, SDKs, and the checksum-pinned NATS, Traefik
and Node binaries) are installed inside the job container. A job that needs
the real Docker CLI mounts the runner's `/usr/bin/docker` read-only into its
container and runs it without a daemon socket. The hosted image ships that
binary, so the mount needs no change between runner types, but the binary must
still run against the container's Debian glibc.

## The dormant self-hosted role

`playbooks/ci-runner.yml` and this role still declare a single
repository-specific runner on agent-a. It is kept so CI can return to the
homelab, for example if hosted minutes or the repository's visibility change.
It is a dormant fallback, not a pool: there is one runner and no multi-slot
abstraction. Removing an existing registration or stopping its service is an
operator action outside this repository's CI change.

The official runner archive is SHA256-verified, and the runner may update
itself through GitHub's normal mechanism. The service has its own runner, HOME
and temporary directories, lower CPU/I/O weights than ordinary worker tasks,
and bounded service memory. It follows the existing runner account convention
(`igor`, with Docker access); separate HOME directories are not a security
sandbox against jobs running as that same user. Only reviewed code from a
private repository may run on it. Never attach a self-hosted runner to a
public repository, where pull requests from forks could run arbitrary code on
the host.

To reactivate it:

1. Deploy the files. This does not register or start a runner:

   ```sh
   ansible-playbook playbooks/ci-runner.yml
   ```

2. Register a fresh runner using the repository's short-lived registration
   token and the official `config.sh --unattended` flow, with URL
   `https://github.com/iryzhkov/homelab-infra`, name
   `agent-a-homelab-infra-1`, labels `homelab-infra,agent-a`, and work
   directory `_work`. Keep the token out of logs and source. Registration
   credentials stay in the private runner directory, not Git.
3. Reapply the play. It verifies the non-secret registration repository before
   enabling the service.
4. Change each job's `runs-on` in `check.yml` to
   `[self-hosted, Linux, X64, homelab-infra, agent-a]`.

To replace the host, deploy a fresh distribution on the replacement, register
it with the declared repository and labels and a unique runner name, confirm a
successful check, then remove the old registration and service. Do not copy
another repository's credentials or repurpose its active runner.
