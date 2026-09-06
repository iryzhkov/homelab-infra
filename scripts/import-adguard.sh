#!/usr/bin/env bash
# Import the AdGuard Home configuration out of the LXC containers.
#
#   scripts/import-adguard.sh
#
# The three AdGuard instances are LXCs, not Compose stacks, and none of them
# accepts SSH, so everything goes through `pct exec` on the Proxmox node that
# hosts the container.
#
# Only the origin (CT100) is managed by this repository. The other two are
# replicas that adguardhome-sync keeps in step with it, so pushing a
# configuration to them would fight the sync rather than help it. Their
# configurations are still copied here, with the password redacted, because a
# replica that has to be rebuilt from scratch needs something to start from.
#
# The admin password hash is the only secret in the file. It becomes
# secrets/adguard-origin.sops.yaml, and the template carries a placeholder.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config=/opt/AdGuardHome/AdGuardHome.yaml

origin_node=proxmox-a
origin_ct=100
replicas=("proxmox-a 107" "proxmox-b 201")

template="$repo_root/roles/adguard/templates/AdGuardHome.yaml.j2"
reference_dir="$repo_root/roles/adguard/files/reference"
secrets_file="$repo_root/secrets/adguard-origin.sops.yaml"

mkdir -p "$(dirname "$template")" "$reference_dir"
umask 077

echo "origin: $origin_node ct$origin_ct"
ssh "$origin_node" "pct exec $origin_ct -- cat $config" >/tmp/adguard-origin.yaml

# One line holds the bcrypt hash; the rest of the file is worth reading in a
# diff, so only that line is replaced.
sed -E 's|^(    password: ).*$|\1{{ adguard_admin_password_hash }}|' \
    /tmp/adguard-origin.yaml >"$template"

grep -q '{{ adguard_admin_password_hash }}' "$template" \
    || { echo "No password line found; refusing to write a template without one." >&2; exit 1; }

printf 'adguard_admin_password_hash: %s\n' \
    "$(sed -nE 's|^    password: (.*)$|\1|p' /tmp/adguard-origin.yaml)" >"$secrets_file"
sops -e -i "$secrets_file"
echo "  template $(wc -l <"$template") lines, hash -> $(basename "$secrets_file")"

for replica in "${replicas[@]}"; do
    set -- $replica
    node="$1" ct="$2"
    echo "replica: $node ct$ct (reference copy only)"
    ssh "$node" "pct exec $ct -- cat $config" \
        | sed -E 's|^(    password: ).*$|\1<redacted, see the instance itself>|' \
        >"$reference_dir/ct$ct.yaml"
done

rm -f /tmp/adguard-origin.yaml
echo "done"
