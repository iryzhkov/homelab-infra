"""Filters for mirroring the images Swarm stacks use into Forgejo's registry.

    {{ 'nginx:latest@sha256:abc...' | swarm_mirror_ref('git.ryzhkov.dev', 'igor') }}

returns

    source       the reference as the stack file writes it
    source_name  the same without its digest (what a node tags locally), or ''
                 when the stack names the image by digest only
    pinned       the digest the stack pins, or ''
    repo         the fully qualified repository, docker.io/library/nginx
    tag          the tag, or pinned-<12 hex> for a digest-only reference
    mirror_repo  git.ryzhkov.dev/igor/mirror/docker.io/library/nginx
    mirror       mirror_repo:tag
"""


from ansible.errors import AnsibleFilterError

# One accepted local backend artifact; the config ID commits to Config/RootFS.
OV_BACKEND_LOCAL_RELEASE = {
    "kind": "local-config",
    "reference": "sha256:1e3b1d3f2f56df027754ab9f090eb6b74c760c0978e95d0c9e313a09a2b0e963",
    "source_revision": "625cdec406678fcb176060fc5c331e0f07726108",
    "native_sha256": "8be7869a9f326ccd6cb67e093216f74296d1e98e93a73290918e6f33744ea1ad",
    "provenance": {
        "assembly_job": "617351b9-b830-4457-aee3-5cbb94ba9eb7",
        "archive_sha256": "46bdfc39ddce4d337c43f57e5668adcc96d0204869abf484c0324645ff86fafa",
        "custody_sha256": "e214e006bef6530409ce71d6ceb7e6bfd0d203196db3ba342a7552d66da1bfce"
    }
}
OV_BACKEND_NODE = "riag6q8hi2ntp6eyyhyk735xa"


def _require(ok, message):
    if not ok:
        raise AnsibleFilterError("swarm: " + message)


def ov_backend_release(value, stack, host, manager, image_override=None):
    if (stack.get("name") != "openviking"
            and stack.get("source", stack.get("name")) != "openviking"):
        return False
    if value is False:
        _require(image_override != OV_BACKEND_LOCAL_RELEASE["reference"],
                 "local backend ID requires its closed release selector")
        return False
    _require(value == OV_BACKEND_LOCAL_RELEASE, "unaccepted local backend release binding")
    _require(stack.get("name") == "openviking"
             and stack.get("source", "openviking") == "openviking"
             and stack.get("pin") == "homelab"
             and stack.get("prepare_tasks") == "swarm-prepare.yml",
             "local backend requires the unchanged owned stack and homelab pin")
    _require(host == manager == "homelab", "local backend deploy target must be homelab")
    _require(image_override in (None, OV_BACKEND_LOCAL_RELEASE["reference"]),
             "conflicting backend image override")
    return value


def ov_backend_local_image(image_list, info, pins, node):
    _require(isinstance(image_list, list) and len(image_list) == 1,
             "required local backend image is missing or ambiguous")
    image = image_list[0]
    _require(isinstance(image, dict), "invalid backend image inspection")
    config = image.get("Config") or {}
    labels = config.get("Labels") or {}
    swarm = info.get("Swarm") or {}
    _require(info.get("Driver") == "overlay2"
             and info.get("OSType") == "linux"
             and swarm.get("NodeID") == OV_BACKEND_NODE
             and swarm.get("LocalNodeState") == "active"
             and swarm.get("ControlAvailable") is True,
             "local daemon/store identity mismatch")
    _require(pins == [OV_BACKEND_NODE] and isinstance(node, list) and len(node) == 1,
             "local backend placement pin changed")
    n = node[0]
    _require(n.get("ID") == OV_BACKEND_NODE
             and n.get("Description", {}).get("Hostname") == "homelab"
             and n.get("Status", {}).get("State") == "ready"
             and n.get("Spec", {}).get("Availability") == "active"
             and n.get("Spec", {}).get("Labels", {}).get("host") == "homelab"
             and n.get("Spec", {}).get("Labels", {}).get("pin.openviking") == "true",
             "local backend node identity/placement mismatch")
    _require(image.get("Id") == OV_BACKEND_LOCAL_RELEASE["reference"]
             and image.get("Os") == "linux" and image.get("Architecture") == "amd64"
             and labels.get("org.opencontainers.image.revision") == OV_BACKEND_LOCAL_RELEASE["source_revision"]
             and labels.get("io.openviking.candidate.native-sha256") == OV_BACKEND_LOCAL_RELEASE["native_sha256"]
             and config.get("Entrypoint") == ["openviking-entrypoint"]
             and config.get("WorkingDir") == "/app/.openviking"
             and config.get("User") in (None, "") and config.get("Cmd") in (None, []),
             "local backend image identity/config mismatch")
    return True


def swarm_stack_deploy_argv(stack, local=False):
    args = ["docker"]
    if local:
        _require(stack == "openviking", "local image flags are backend-only")
        args += ["--host=unix:///var/run/docker.sock"]
    args += ["stack", "deploy", "--compose-file", "deployed.yml.pending"]
    args += ["--resolve-image", "never"] if local else ["--with-registry-auth"]
    return args + ["--prune", "--detach=false", stack]


def _split(image):
    name, _, digest = image.partition("@")
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        repo, tag = name.rsplit(":", 1)
    else:
        repo, tag = name, ""
    return name if tag else "", repo, tag, digest


def _qualify(repo):
    first = repo.split("/", 1)[0]
    if "/" in repo and ("." in first or ":" in first or first == "localhost"):
        return repo
    if "/" not in repo:
        return "docker.io/library/" + repo
    return "docker.io/" + repo


def swarm_mirror_ref(image, registry, owner):
    _require(image != OV_BACKEND_LOCAL_RELEASE["reference"],
             "local backend config ID must not enter the named-image mirror")
    tagged, repo, tag, digest = _split(image)
    # A stack that already names the mirror (repository@digest): nothing to
    # mirror, only to pre-pull, by that exact reference.
    if repo.startswith(f"{registry}/{owner}/mirror/"):
        return {
            "source": image,
            "source_name": "",
            "pinned": digest,
            "repo": repo,
            "tag": tag,
            "mirror_repo": repo,
            "mirror": image,
        }
    qualified = _qualify(repo)
    if not tag:
        tag = "pinned-" + digest.split(":", 1)[-1][:12] if digest else "latest"
    mirror_repo = f"{registry}/{owner}/mirror/{qualified}"
    return {
        "source": image,
        "source_name": tagged if tagged else ("" if digest else f"{repo}:latest"),
        "pinned": digest,
        "repo": qualified,
        "tag": tag,
        "mirror_repo": mirror_repo,
        "mirror": f"{mirror_repo}:{tag}",
    }


class FilterModule:
    def filters(self):
        return {"swarm_mirror_ref": swarm_mirror_ref,
                "ov_backend_release": ov_backend_release,
                "ov_backend_local_image": ov_backend_local_image,
                "swarm_stack_deploy_argv": swarm_stack_deploy_argv}
