#!/usr/bin/env bash
# Snapshot the current, hand-managed state of every homelab host into snapshot/.
#
# This is deliberately read-only: it never writes to a host, and it never
# touches container data. It exists so that the Ansible roles in this
# repository can be written against what is actually running, and so that the
# reconstruction is reproducible when a host changes underneath us.
#
# Secrets (.env, stack.env, acme.json) are handled separately by
# scripts/collect-secrets.sh, so that this snapshot stays safe to read.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$repo_root/snapshot"

# Config files worth capturing. Anything larger than this is data, not config.
# The size is given in bytes, because GNU find rounds `-size -1M` up to a whole
# block and would then match almost nothing.
max_size="1000000c"

log() { printf '%s\n' "$*" >&2; }

# fetch_tree HOST REMOTE_DIR MAXDEPTH
# Copies configuration files under REMOTE_DIR into snapshot/HOST/, preserving
# the path relative to the remote home directory.
fetch_tree() {
    local host="$1" dir="$2" depth="$3"
    ssh -o ConnectTimeout=8 "$host" "
        set -u
        cd \"\$HOME\" || exit 0
        rel=\$(printf '%s' '$dir' | sed \"s|^\$HOME/||; s|^~/||\")
        [ -d \"\$rel\" ] || exit 0
        find \"\$rel\" -maxdepth $depth -type f -size -$max_size \\
            \\( -name '*compose*.y*ml' -o -name 'Dockerfile*' -o -name '*.sh' \\
               -o -name 'traefik.y*ml' -o -name '*.service' -o -name '*.timer' \\) \\
            -not -path '*/node_modules/*' -not -path '*/.git/*' \\
            -print0 2>/dev/null | tar --null -czf - -T - 2>/dev/null
    " | tar -xzf - -C "$out/$host" 2>/dev/null
}


# fetch_dir HOST REMOTE_DIR
# Copies a whole directory whose contents are configuration rather than data,
# such as Traefik's file provider. Files that look like secrets are skipped:
# scripts/collect-secrets.sh handles those.
fetch_dir() {
    local host="$1" dir="$2"
    mkdir -p "$out/$host"
    ssh -o ConnectTimeout=8 "$host" "
        set -u
        cd \"\$HOME\" || exit 0
        rel=\$(printf '%s' '$dir' | sed \"s|^\$HOME/||; s|^~/||\")
        [ -d \"\$rel\" ] || exit 0
        find \"\$rel\" -type f -size -$max_size \\
            -not -name '.env' -not -name '*.env' -not -name 'acme.json' \\
            -not -name '*.key' -not -name '*.pem' \\
            -print0 2>/dev/null | tar --null -czf - -T - 2>/dev/null
    " | tar -xzf - -C "$out/$host" 2>/dev/null
}

# fetch_cmd HOST OUTFILE COMMAND
# Records the output of a read-only command as part of the snapshot.
fetch_cmd() {
    local host="$1" file="$2" cmd="$3"
    mkdir -p "$(dirname "$out/$host/$file")"
    ssh -o ConnectTimeout=8 "$host" "$cmd" >"$out/$host/$file" 2>/dev/null
}

snapshot_docker_host() {
    local host="$1"
    shift
    log "== $host"
    mkdir -p "$out/$host"
    for dir in "$@"; do
        fetch_tree "$host" "$dir" 3
    done
    fetch_cmd "$host" "facts/containers.txt" \
        'docker ps -a --format "{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"'
    fetch_cmd "$host" "facts/compose-projects.txt" \
        'docker ps -a --format "{{.Names}}" | while read -r c; do printf "%s\t%s\t%s\n" "$c" "$(docker inspect -f "{{index .Config.Labels \"com.docker.compose.project\"}}" "$c")" "$(docker inspect -f "{{index .Config.Labels \"com.docker.compose.project.working_dir\"}}" "$c")"; done'
    fetch_cmd "$host" "facts/volumes.txt" 'docker volume ls --format "{{.Name}}\t{{.Driver}}"'
    fetch_cmd "$host" "facts/networks.txt" 'docker network ls --format "{{.Name}}\t{{.Driver}}"'
    fetch_cmd "$host" "facts/crontab.txt" 'crontab -l'
    fetch_cmd "$host" "facts/systemd-enabled.txt" \
        'systemctl list-unit-files --state=enabled --no-pager --no-legend 2>/dev/null'
    fetch_cmd "$host" "facts/systemd-user-enabled.txt" \
        'systemctl --user list-unit-files --state=enabled --no-pager --no-legend 2>/dev/null'
    fetch_cmd "$host" "facts/os.txt" \
        '. /etc/os-release; printf "%s\n" "$PRETTY_NAME"; uname -srm; docker --version; docker compose version'
    fetch_cmd "$host" "facts/packages.txt" \
        'command -v dpkg >/dev/null && dpkg-query -W -f "${Package}\t${Version}\n" || pacman -Q 2>/dev/null'
    fetch_cmd "$host" "facts/mounts.txt" 'findmnt -rn -t nfs,nfs4,ext4,xfs -o TARGET,SOURCE,FSTYPE,OPTIONS'
    fetch_portainer_stacks "$host"
}

# fetch_portainer_stacks HOST
# Portainer was removed in the 2026-08-30 cleanup, but stacks it created still
# run from compose files inside the portainer_data volume, owned by root and
# invisible to the ordinary sweep. Black Pearl's gluetun/qbittorrent stack is
# the only one left, and it is exactly the kind of stack that would not come
# back after a rebuild.
fetch_portainer_stacks() {
    local host="$1" vol=/var/lib/docker/volumes/portainer_data/_data/compose
    local ids
    ids=$(ssh -o ConnectTimeout=8 "$host" "sudo -n ls '$vol' 2>/dev/null")
    [ -n "$ids" ] || return 0
    local id
    for id in $ids; do
        mkdir -p "$out/$host/portainer-stack-$id"
        ssh -o ConnectTimeout=8 "$host" \
            "sudo -n cat '$vol/$id'/docker-compose.yml 2>/dev/null" \
            >"$out/$host/portainer-stack-$id/docker-compose.yml"
    done
}

snapshot_proxmox() {
    local host="$1"
    log "== $host"
    mkdir -p "$out/$host/guests"
    fetch_cmd "$host" "facts/guests.txt" 'qm list; echo; pct list'
    fetch_cmd "$host" "facts/storage.cfg" 'cat /etc/pve/storage.cfg'
    fetch_cmd "$host" "facts/version.txt" 'pveversion -v'
    fetch_cmd "$host" "facts/jobs.txt" 'cat /etc/pve/jobs.cfg 2>/dev/null'
    fetch_cmd "$host" "facts/network.txt" 'cat /etc/network/interfaces'
    # /etc/pve is pmxcfs, a FUSE filesystem whose files tar skips, so the guest
    # configs are concatenated rather than archived.
    fetch_cmd "$host" "guests/all.conf" \
        'for f in /etc/pve/qemu-server/*.conf /etc/pve/lxc/*.conf; do [ -e "$f" ] || continue; printf "### %s\n" "$f"; cat "$f"; printf "\n"; done'
}

mkdir -p "$out"

snapshot_docker_host homelab   '~/app' '~/apps' '~/cv-site'
snapshot_docker_host public-api '~/apps'
snapshot_docker_host normandy  '~/app'
snapshot_docker_host blackpearl '~/apps' '~/app'
snapshot_proxmox proxmox-a
snapshot_proxmox proxmox-b

# Directories whose entire contents are configuration, captured in full.
fetch_dir public-api '~/apps/traefik/file_provider'
fetch_dir homelab    '~/app/traefik'

log ""
log "Snapshot written to $out ($(find "$out" -type f | wc -l) files)"
