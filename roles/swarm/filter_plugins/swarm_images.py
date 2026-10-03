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
        return {"swarm_mirror_ref": swarm_mirror_ref}
