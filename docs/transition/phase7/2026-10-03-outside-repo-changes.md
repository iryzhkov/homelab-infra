# Phase 7: changes made outside the repository on 2026-10-03

Executor E19, under Igor's authorisation of 2026-10-03 relayed by the lead: UniFi changes by
API for this transition, and a Home Assistant edit with a Core restart. Hosts: UDM Pro
192.168.90.1 (local Network API as the local admin `claude`, password from the laptop
keyring, never printed), public-api 192.168.20.33, public-api-b 192.168.20.4, `home-assistant`.

## 1. UniFi: ports 2283 and 1933 added to `traefik-homelab-ports`

### Before (11:11 PDT)

`GET /proxy/network/v2/api/site/default/firewall-policies`: 299 policies.
`GET /proxy/network/api/s/default/rest/firewallgroup`:

```
6a966cc55afbaf142ea88874 traefik-homelab-ports (port-group):
  3001 3151 3200 3500 3579 4080 4580 5006 5080 5090 5984 6767 7080 7100 7391 7474 7878
  8080 8091 8095 8096 8097 8099 8321 8686 8989 9009 9696 25600 4533
```

The group is used by `public-api -> homelab (Traefik backends)` (`6a966cc65afbaf142ea8887a`,
source group `edge-proxies`, destination 192.168.70.223 and 192.168.70.234).

Probes, `curl -s -o /dev/null -m 5 -w '%{http_code}' http://192.168.70.223:<port>/`, the same
from public-api and from public-api-b: 2283 `000`, curl exit 28 (timeout); 1933 `000`, exit 28.

### Call

The group as read, with `group_members += ["2283","1933"]`:

```
PUT /proxy/network/api/s/default/rest/firewallgroup/6a966cc55afbaf142ea88874
-> rc ok, members ... 25600 4533 2283 1933
```

Nothing else was changed or deleted.

### After

- Groups diff: only `6a966cc55afbaf142ea88874` changed, gaining 2283 and 1933. The other
  four groups, including `edge-proxies`, are unchanged.
- Policies diff (normalised by `_id`, `hits` and `last_hit` removed): 299 before and after,
  none added or removed, one changed: the system policy
  `public-api -> homelab (Traefik backends) (Return)`, whose `source.port` list gained
  `2283,1933` (the UDM regenerates it from the group). The user policy itself references the
  group by ID and is unchanged.
- UDM: `ipset list UBIOS_6a966cc55afbaf142ea88874` holds 1933 and 2283 among its 32 ports.
- Probes from both edge VMs: `192.168.70.223:2283` returns `000` with curl exit 7 (connection
  refused: nothing on homelab publishes 2283 now, so the UDM passes it and homelab refuses);
  `192.168.70.223:1933` returns 404 (OpenViking answers at `/`). No timeout on either.

Recorded answer: both edge VMs reach homelab on 2283 and 1933.

### Rollback

Read the group, then `PUT .../rest/firewallgroup/6a966cc55afbaf142ea88874` with
`group_members` minus `2283` and `1933`.

## 2. Home Assistant: Emby through `emby.ryzhkov.dev`

### Before

`/config/configuration.yaml` (owner 1001, 0644, 4451 bytes), lines 13-16:

```
media_player:
  - platform: emby
    host: 192.168.70.223
    api_key: !secret emby_api_key
```

`/config/packages/requests.yaml` (root, 0644, 32421 bytes): four `rest_command` URLs on
`http://192.168.70.223:8096/emby/...`: `emby_search` (GET), `emby_next_up` (GET),
`emby_sessions` (GET), `emby_play` (POST). No other file under `configuration.yaml` or
`packages/` names 192.168.70.223.

The Emby integration at HA 2026.9.4 (`homeassistant/components/emby/media_player.py` at tag
`2026.9.4`) accepts `host`, `port` and `ssl` (default false); without `port` it uses 8096, or
8920 when `ssl` is true. So HTTPS through Traefik needs `ssl: true` and `port: 443`
explicitly; `host` takes a host name, not a URL.

From the `home-assistant` host, `emby.ryzhkov.dev` resolves to 192.168.20.3 (the VIP), and
`https://emby.ryzhkov.dev/emby/System/Info/Public` returns 200 from "Igor's Media Server",
version 4.10.1.0.

### Edits (11:12 PDT)

```
cd /config
cp -p configuration.yaml configuration.yaml.bak-2026-10-03
cp -p packages/requests.yaml packages/requests.yaml.bak-2026-10-03
sed -i "15s/.*/    host: emby.ryzhkov.dev\n    port: 443\n    ssl: true/" configuration.yaml   # after checking lines 14-15
sed -i "s#http://192.168.70.223:8096/#https://emby.ryzhkov.dev/#" packages/requests.yaml
```

Result (diff against the backups):

```
-    host: 192.168.70.223
+    host: emby.ryzhkov.dev
+    port: 443
+    ssl: true
```

and in `requests.yaml` exactly the four URLs, each `http://192.168.70.223:8096/emby/...`
becoming `https://emby.ryzhkov.dev/emby/...`, path and query unchanged. Owners and modes kept.
`grep 192.168.70.223` on both files: nothing left.

`ha core check` at 11:12:55: `Command completed successfully.`

The read-only `emby_sessions` URL was exercised directly from the `home-assistant` host with
the same API key (read from `secrets.yaml` into a shell variable, never printed):
`GET https://emby.ryzhkov.dev/emby/Sessions` returned 200 with 3 sessions. The Core API is
not reachable from the SSH add-on (`/core/api/states` answers 401), so the rest_command
cannot be called through Home Assistant itself from here.

### Restart

Held until the lead's go (E20 was in a service-affecting sequence), received after E20
reported done.

```
ha core restart        # issued 11:33:08, returned 11:34:41, exit 0
```

`https://home.ryzhkov.dev/` through the VIP (`--resolve home.ryzhkov.dev:443:192.168.20.3`)
returned 200 at 11:34:41. Downtime: about 93 seconds, 11:33:08 to 11:34:41.

### After

- Logs (`ha core logs -n 400`, the window from the shutdown to 11:34:37): no line mentions
  `emby`, `rest_command`, `components.http`, `Invalid config` or `Setup failed`. The only
  ERROR is from shutdown, `pychromecast ... Bedroom TV(192.168.50.160):8009 Failed to
  connect`, unrelated. Warnings after start are the existing ones (custom integrations,
  `switch.presence_simulation_lights` missing, `E2 - Status LED reconcile` run limit).
- Emby integration: the Emby server's `GET /emby/Sessions` (through `emby.ryzhkov.dev`, API
  key from `secrets.yaml`, never printed) shows a `pyEmby` session from device
  `HomeAssistant`, last activity 18:34:36Z (11:34:36 PDT), that is, the integration
  connected over HTTPS through the edge right after the start. Remote end point is
  192.168.70.93, so Traefik's `X-Forwarded-For` reaches Emby.
- Entity states could not be read directly: the SSH add-on gets 401 from the Core API. The
  18 `media_player.emby_*` entities are in the registry and enabled; their availability is
  inferred from the live `pyEmby` connection, not observed.
- rest_command: no read-only command was called through Home Assistant (same API limit).
  The URL of the read-only `emby_sessions` command returns 200 with the same key, before and
  after the restart.

Recorded answer: Home Assistant now reaches Emby by name through the edge VIP over HTTPS on
443; nothing in it names homelab's address for Emby any more.

### Rollback

```
cd /config
cp -p configuration.yaml.bak-2026-10-03 configuration.yaml
cp -p packages/requests.yaml.bak-2026-10-03 packages/requests.yaml
ha core check && ha core restart
```
