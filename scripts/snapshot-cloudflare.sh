#!/usr/bin/env bash
# Capture the Cloudflare state that exists only in the dashboard: the tunnel
# ingress rules, and the DNS records that point at them.
#
# The homelab's cloudflared connector is token-based, so its ingress rules are
# not on any disk we control. Without them a standby host can run every service
# and still serve nothing, because no hostname would route to it.
#
# Needs a read-only API token in the keyring:
#   secret-tool store --label='Cloudflare infra read token' \
#       service cloudflare-api account homelab-infra
# with permissions Account:Cloudflare Tunnel:Read, Zone:DNS:Read, Zone:Zone:Read.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
out="$repo_root/snapshot/cloudflare"
api="https://api.cloudflare.com/client/v4"

token="$(secret-tool lookup service cloudflare-api account homelab-infra)"
if [ -z "$token" ]; then
    echo "No Cloudflare token in the keyring; see the header of this script." >&2
    exit 1
fi

cf() { curl -sS -H "Authorization: Bearer $token" "$api/$1"; }

mkdir -p "$out"

cf "user/tokens/verify" | jq -e '.success' >/dev/null \
    || { echo "Token did not verify." >&2; exit 1; }

cf "zones" >"$out/zones.json"
zone_id="$(jq -r '.result[] | select(.name == "ryzhkov.dev") | .id' "$out/zones.json")"

# The account id comes from the zone rather than from GET /accounts, which
# returns an empty list unless the token also carries Account Settings:Read.
account_id="$(jq -r '.result[] | select(.name == "ryzhkov.dev") | .account.id' "$out/zones.json")"

cf "zones/$zone_id/dns_records?per_page=500" >"$out/dns_records.json"

cf "accounts/$account_id/cfd_tunnel?is_deleted=false" >"$out/tunnels.json"

# Ingress rules are per tunnel, and only exist for remotely-managed tunnels.
# A locally-managed one answers 404 here and keeps its rules in its own config
# file on the connector host instead.
jq -r '.result[].id' "$out/tunnels.json" | while read -r tunnel_id; do
    cf "accounts/$account_id/cfd_tunnel/$tunnel_id/configurations" \
        >"$out/tunnel-$tunnel_id-config.json"
done

echo "Cloudflare state written to $out"
