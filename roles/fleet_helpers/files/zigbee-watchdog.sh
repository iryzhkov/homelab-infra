#!/usr/bin/env bash
# Recover the coordinator only after successfully inspecting its journal.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
exec 9>/run/zigbee-watchdog.lock
flock -n 9 || exit 0
state=/run/zigbee-watchdog.state
if ! output=$(/usr/sbin/pct exec 105 -- journalctl -u zigbee2mqtt --no-pager --since "2 minutes ago" 2>&1); then
    logger -t zigbee-watchdog "Cannot inspect Zigbee journal; no recovery attempted: $output"
    exit 1
fi
count=$(grep -c "SRSP.*dataRequest after 6000ms" <<<"$output" || true)
if [ "${1:-}" = --check ]; then
    echo "journal inspection succeeded; coordinator timeouts=$count"
    exit 0
fi
[ "$count" -ge 3 ] || exit 0
last=0
[ ! -r "$state" ] || read -r last <"$state"
[[ "$last" =~ ^[0-9]+$ ]] || last=0
now=$(date +%s)
[ "$((now - last))" -ge 300 ] || exit 0
logger -t zigbee-watchdog "Coordinator lockup ($count timeouts); restarting Zigbee2MQTT"
if /usr/sbin/pct exec 105 -- systemctl restart zigbee2mqtt; then
    echo "$now" >"$state"
    logger -t zigbee-watchdog "Zigbee2MQTT restart succeeded"
else
    logger -t zigbee-watchdog "Zigbee2MQTT restart failed"
    exit 1
fi
