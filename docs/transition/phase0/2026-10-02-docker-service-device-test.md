# Phase 0: Docker Swarm service device mapping test

Date: 2026-10-02, 12:56 to 12:58 PDT (UTC-07:00)
Host: omarchy-pc (`ssh gaming-pc`, igor@192.168.90.212), Docker Engine 29.7.2
Plan reference: phase 0 item 4, "Docker 29 device support for services"; decides phase 5 and the "GPU stacks cannot be Swarm services" premise.

**Verdict: Swarm services on Docker 29.7.2 cannot map host devices.** The CLI has no `--device` flag for services, `docker stack deploy` drops `devices:` with a warning, and the Engine API silently discards `Devices` and `DeviceRequests` (CDI) in a service spec. The only scheduler-level route, `--generic-resource`, reserves a counted resource but does not map a device into the task.

## Starting state

- `docker version`: client 29.7.2, API 1.55, go1.26.5. Server (via `sudo docker version`): 29.7.2, API 1.55. The user igor is not in the `docker` group, so every daemon command below ran with `sudo -n`.
- `systemctl is-enabled docker.service docker.socket`: `disabled` / `enabled`; `docker.service` was `inactive` (socket-activated). The first `sudo docker` call started the daemon through the socket.
- `sudo docker info --format '{{.Swarm.LocalNodeState}}'`: `inactive`. Not in any swarm, so the throwaway init was safe.
- Devices: `/dev/dri/renderD128` (226,128, `crw-rw-rw- root render`) and `/dev/dri/renderD129` exist alongside the NVIDIA nodes, so the test used renderD128 as briefed.
- Before the test: one image (`ghcr.io/home-assistant/home-assistant:2026.9.1`), no containers, networks `bridge`, `host`, `none` only. `alpine:3` was not present. CDI spec dirs `/etc/cdi` and `/var/run/cdi` do not exist.

## Throwaway swarm

`sudo docker swarm init --advertise-addr 127.0.0.1 --listen-addr 127.0.0.1:2377`

Output: `Swarm initialized: current node (t4mbsmgr5n1hho8a88d29tijw) is now a manager.` Join token not recorded.

## Check a: `docker service create --device`

`docker service create --help | grep -i -E 'device|generic|cdi|gpu'` returns only:

```
      --generic-resource list              User defined resources
```

No `--device` line.

`sudo docker service create --name devtest --device /dev/dri/renderD128 --restart-condition none alpine:3 ls -l /dev/dri`

```
unknown flag: --device
exit=125
```

Answer: rejected by the CLI; no service was created.

## Check c: `docker stack deploy` with `devices:`

Compose file `/tmp/devtest/stack.yml` on omarchy-pc (outside the repository):

```yaml
services:
  probe:
    image: alpine:3
    command: ["sh","-c","ls -l /dev/dri; sleep 3600"]
    devices:
      - /dev/dri/renderD128:/dev/dri/renderD128
    deploy:
      restart_policy:
        condition: none
```

`sudo docker stack deploy -c /tmp/devtest/stack.yml devtest`

```
Ignoring unsupported options: devices
Creating network devtest_default
Creating service devtest_probe
exit=0
```

The task reached Running. `sudo docker service logs --raw devtest_probe`: `ls: /dev/dri: No such file or directory`. `docker service inspect` shows a ContainerSpec with no device field.

Answer: `devices:` is ignored with a warning; the service deploys and runs without the device.

## Check d: generic resources and CDI

Generic resource: `sudo docker service create --name devtest-gr --generic-resource "gpu=1" --restart-condition none --detach alpine:3 ls -l /dev/dri` was accepted, and the task stayed `Pending` with `"no suitable node (insufficient resources on 1 node)"`, because the node advertises only `{"NanoCPUs":12000000000,"MemoryBytes":32754974720}`. Advertising a resource needs `node-generic-resources` in `daemon.json` (not changed here; hosts are not configured by hand). Even when advertised, a generic resource is only a scheduling count exposed to the task as a `DOCKER_RESOURCE_*` environment variable; it does not add a device node or cgroup permission. Answer: usable for placement, not for device access.

Raw API (bypassing the CLI): `POST /v1.55/services/create` with `ContainerSpec.Devices` = `[{PathOnHost: /dev/dri/renderD128, PathInContainer: /dev/dri/renderD128, CgroupPermissions: rwm}]` and `ContainerSpec.DeviceRequests` = `[{Driver: cdi, DeviceIDs: [nvidia.com/gpu=all]}]` returned `{"ID":"1potfrzuabpr3p8akw5gbuc9v","Warnings":null}`. The stored spec contained neither field (`{"Image":"alpine:3","Args":[...],"StopGracePeriod":10000000000,"DNSConfig":{},"Isolation":"default"}`), and the task logged `ls: /dev/dri: No such file or directory`. Answer: Engine 29.7.2 has no device or CDI field in the swarm service spec; unknown fields are dropped without a warning.

## Cleanup

- `sudo docker service rm devtest-api devtest-gr`, `sudo docker stack rm devtest` (removed `devtest_probe` and network `devtest_default`).
- `sudo docker swarm leave --force`: `Node left the swarm.`; `docker info` then reported `swarm=inactive`.
- `docker_gwbridge` was left behind by the init and removed with `sudo docker network rm docker_gwbridge`. No `ingress` network remained. Networks are back to `bridge`, `host`, `none`.
- `sudo docker rmi alpine:3` (pulled by this test). Image list is back to the single Home Assistant image; no containers remain.
- `/tmp/devtest` removed.
- `sudo systemctl stop docker.service` returned the daemon to its prior inactive, socket-activated state (`docker.socket` still active).

## Implications for later phases

- Phase 5 premise 4 and item 6 stand: Emby, Frigate and Immich ML stay Compose projects per host, chosen by one host variable in homelab-infra and moved by the drain runbook. Nothing in phase 5 changes.
- Phase 5 item 4 (conversion rules) must treat `devices:` as a hard lint error, not a dropped key, because `stack deploy` accepts the file and runs the service without the GPU, which would silently turn hardware transcoding off.
- Workarounds that exist (a privileged sidecar that runs `docker run --device` from inside a service, or bind-mounting `/dev/dri` with a generic-resource gate) were not tested and are not recommended; they reintroduce per-host state through a more fragile path than Compose.
