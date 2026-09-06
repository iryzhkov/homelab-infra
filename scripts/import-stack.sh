#!/usr/bin/env bash
# Import one running Compose stack into this repository:
#
#   scripts/import-stack.sh <host> <stack> [remote_dir] [repo_name] [env_file]
#
# repo_name is the directory under stacks/ to write into; it defaults to the
# stack name and is given explicitly when one project name means different
# files on different hosts, as diun does.
#
# Copies the stack's docker-compose.yml into stacks/<stack>/ byte for byte, and
# its .env into secrets/<host>-<stack>.sops.yaml, encrypted. Byte fidelity is
# the point: if the repository's copy differs from what is running, the first
# playbook run recreates containers instead of adopting them.
#
# The .env is parsed and its values are hashed on both sides before and after
# encryption. An earlier import lost the padding of a base64 key to a careless
# split on "=", which sent a VPN container into a restart loop with no error in
# its logs, so the check earns its place.

set -euo pipefail

host="${1:?usage: import-stack.sh <host> <stack> [remote_dir] [repo_name]}"
stack="${2:?usage: import-stack.sh <host> <stack> [remote_dir] [repo_name]}"
repo_name="${4:-$stack}"
env_file="${5:-.env}"   # immich's Compose file reads stack.env instead

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
remote_dir="${3:-}"

if [ -z "$remote_dir" ]; then
    # Ask the running container where its project lives, rather than guessing
    # between ~/app and ~/apps.
    remote_dir=$(ssh "$host" "docker compose ls --format json" \
        | python3 -c "
import json, sys
for p in json.load(sys.stdin):
    if p['Name'] == '$stack':
        print(p['ConfigFiles'].split(',')[0].rsplit('/', 1)[0])
        break
")
fi
[ -n "$remote_dir" ] || { echo "Could not locate stack $stack on $host" >&2; exit 1; }

echo "$host:$remote_dir -> stacks/$repo_name"

mkdir -p "$repo_root/stacks/$repo_name"
ssh "$host" "cat '$remote_dir/docker-compose.yml'" \
    >"$repo_root/stacks/$repo_name/docker-compose.yml"

remote_sum=$(ssh "$host" "sha256sum '$remote_dir/docker-compose.yml'" | cut -d' ' -f1)
local_sum=$(sha256sum "$repo_root/stacks/$repo_name/docker-compose.yml" | cut -d' ' -f1)
if [ "$remote_sum" != "$local_sum" ]; then
    echo "Compose file differs after copy; refusing to continue." >&2
    exit 1
fi
echo "  docker-compose.yml ${local_sum:0:16} (identical)"

if ! ssh "$host" "test -f '$remote_dir/$env_file'"; then
    echo "  no $env_file"
    exit 0
fi

secrets_file="$repo_root/secrets/$host-$stack.sops.yaml"
umask 077

# json.dumps produces a valid YAML double-quoted scalar for any string, which
# keeps quotes, backslashes and "=" padding intact.
ssh "$host" "cat '$remote_dir/$env_file'" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.rstrip("\n")
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    print(f"{key.strip()}: {json.dumps(value)}")
' >"$secrets_file"

keys=$(grep -c ':' "$secrets_file" || true)
sops -e -i "$secrets_file"
echo "  $env_file -> secrets/$(basename "$secrets_file") ($keys keys, encrypted)"

# Round-trip: every value must survive encryption unchanged.
remote_hash=$(ssh "$host" "grep -v '^#' '$remote_dir/$env_file' | grep . | sort | sha256sum" | cut -d' ' -f1)
local_hash=$(sops -d --output-type json "$secrets_file" \
    | python3 -c '
import json, sys
# JSON rather than YAML: sops re-quotes values on decrypt, and a hand-rolled
# YAML reader mistook a single-quoted password for its own quotes.
for key, value in json.load(sys.stdin).items():
    print(f"{key}={value}")
' | sort | sha256sum | cut -d' ' -f1)

if [ "$remote_hash" != "$local_hash" ]; then
    echo "  WARNING: decrypted $env_file does not match the host's, check by hand" >&2
    exit 1
fi
echo "  round-trip verified"
