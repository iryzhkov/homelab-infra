# MCP playlist management through the protected operator

`scripts/emby_operator_socket.py` is a socket-activated root worker that lets
the HoloNet MCP service manage existing journal-owned Movie playlists through
the same protected operator the CLI uses. The `web_mcp_endpoint` role installs
it only when the HoloNet endpoint config carries a non-null
`playlist_operator`, and starts its socket only with the existing explicit
activation flag; see "Opt-in HoloNet playlist operator management" in
`docs/web-mcp-endpoints.md`. The adopted contract is
`jocasta:8047884faa50daa3df712477b1196fcd@2`.

## What the first slice covers

| Worker operation | Core method | Actions required on every call |
| --- | --- | --- |
| `show` | `show` | `catalog.read`, `playlist.read`, `home.read` |
| `reconcile` | `reconcile` | same |
| `preview` of `set-members` or `set-title` | `list_preview` | same, plus `playlist.manage` (members) or `home.manage` (title) |
| `apply` of `set-members` or `set-title` | `set_members` or `set_title` | same as its preview |

Create, delete, row attach/detach/move, init, migrate and downgrade stay on the
protected CLI. No request selects a config, journal, credential, path, URL,
backend user, playlist ID or row ID, and no `object.delete` or `object.share`
operation exists in this slice. Membership replacement can take two writes
(clear, then add).

## Shape of the path

```text
MCP service (UID/GID 940, verifies the JWT)
  -> /run/holonet-playlists/operator.sock AF_UNIX, root:holonet 0660
  -> emby_operator_socket.py (root, one process per connection, Accept=yes)
  -> emby_operator_entry.operator_session  same Journal, adapter, credential, Operator as the CLI
  -> existing per-recipient journal, under its existing nonblocking lock
```

The MCP service is the trusted JWT authenticator. The worker does not verify
tokens. It authenticates the service by `SO_PEERCRED` UID and GID and then
intersects the caller fields the service supplies with its own root-owned
ceiling: exact issuer, single subject, namespace, server, library and catalog
user, the fixed recipient-to-config map, and the slice-one operations and
actions in source. A compromised MCP service can act only inside that ceiling;
it cannot read the admin credential or reach other Emby routes.

The worker adds no listener beyond the socket unit, no spool, subprocess,
retry, queue, store or second journal. The existing per-recipient journal lock
serializes MCP and manual CLI writes.

## Version 1 wire contract

Frozen fixtures: `docs/emby-operator-mcp-wire-v1.json`. The tests check the
implementation against every request, error, key and response fixture there,
so a Go client can be built from that file.

Framing:

- One request per connection: one compact UTF-8 JSON object with no leading or
  trailing whitespace, then one `\n`. At most 65536 bytes including the newline.
  Nothing may follow it.
- One response: one compact ASCII-escaped JSON object, then `\n`, at most 262144
  bytes including the newline. The worker then shuts down its write side and
  exits.
- The client must keep both directions open until it has read the response.
  Closing or half-closing (`CloseWrite`) the connection before then is
  cancellation.
- The whole request must arrive within 5 seconds of accept.
- A transport failure, timeout or missing response is an unknown outcome, never
  permission to resend automatically.

Request fields, all required, no others:

| Field | Type |
| --- | --- |
| `version` | integer `1` (not `true`, not `1.0`) |
| `caller` | object below; supplied by the verifier, never by tool arguments |
| `recipient` | lowercase 32-hex Emby user ID |
| `deadline_unix_ms` | integer epoch milliseconds, the request context deadline |
| `operation` | `show`, `reconcile`, `preview` or `apply` |
| `request` | object below |

`caller` has exactly `issuer`, `subject`, `namespace`, `server`, `library`,
`catalog_user` (strings), `actions` (unique strings, at most 32) and
`expires_at` (integer epoch seconds of the verified token). The service must
already have intersected the token's target users and actions with its own
policy; the worker checks the recipient against its map and the actions
against the table above.

`request` by operation:

```text
show       {}  or  {"list": LIST}
reconcile  {}
preview    {"operation": "set-members", "list": LIST, "movies": [MOVIE, ...]}   0..25 unique
           {"operation": "set-title",   "list": LIST, "title": TITLE}           1..256 printable
apply      the preview fields plus "key": LOGICAL_KEY and "intent_digest": HEX64
```

`LIST`, `MOVIE` and `LOGICAL_KEY` match `[A-Za-z0-9_-]{1,128}`.

Responses:

```text
{"version":1,"ok":true,"result":{...}}
{"version":1,"ok":false,"error":{"code":CODE,"message":TEXT[,"operation":OP][,"list":LIST]}}
```

| Code | Meaning |
| --- | --- |
| `refused` | Malformed frame or request, peer or caller outside policy, missing action, or a core refusal (stale digest, drift, reused key, uncertain pending write). Message is fixed safe text. |
| `busy` | The recipient journal lock is held by another run. Nothing was changed; retry later is safe. |
| `unfinished` | Another request is unfinished on this recipient. `operation` and `list` name it; its key is never shown. |
| `deadline` | The effective deadline passed or the caller disconnected. Use `show` before resubmitting the same key and request. |
| `unavailable` | Worker disabled, policy or recipient config unavailable or inconsistent, or an internal failure. |

Results: `preview` returns `{"request": {...}, "intent_digest": HEX64}`;
`apply` returns `{"state": "members-set" | "title-set" | "replayed", "writes": N}`;
`reconcile` returns the core reconcile object. `show` is described below.

## Deadline and cancellation

The worker's deadline is `min(caller.expires_at, deadline_unix_ms, now + 120 s)`
and is never renewed. A request whose deadline has already passed stops before
any file or backend access. The adapter calls the worker's guard before every
backend send, including authentication and reads. The guard stops when the
deadline has passed, the peer closed or half-closed, or extra bytes arrived. It
also caps each send's socket timeout to the remaining time, and the response
read loop refuses after that time.

- A stop before a mutation is sent is the existing `NotDispatched` path: the
  core clears only the pending step and keeps the run, so the identical key
  and request can resume it.
- A stop after a mutation was sent keeps the pending step. Only exact readback
  through a later resubmission of the identical request completes it; `show`
  and `reconcile` never write.

Residual bound: a socket operation that has already started can block for up to
its own timeout, which was capped to the remaining time when it started. The
service unit's `RuntimeMaxSec` is the hard backstop. Killing the worker is
crash-safe for the journal, which saves intent before every send.

## Idempotency

Only `apply` uses a key. The internal key is

```text
"mcp-" + sha256_hex(UTF-8 of compact JSON [issuer, subject, namespace, server, library, recipient, logical_key])
```

with `,` and `:` separators and no ASCII escaping. Operation and list are
deliberately excluded: the core's exact request equality refuses a reused key
with a different request (`Idempotency key reused`) or a different request
while one is unfinished (`unfinished`). The `mcp-` prefix keeps MCP keys apart
from manual CLI keys. A completed key replays with zero writes, after fresh
authorization on every call. A different logical key cannot take over,
expire or reset an unfinished run.

Protected manual recovery of an MCP run uses the CLI with the derived internal
key and the original request from private audit evidence. That is an
exceptional root path, not an MCP operation.

## Show

`show` returns the core version 2 show result, with two changes that do not
touch the journal or the CLI's output:

- `run` is `null` or `{"operation", "list", "args"}` of the unfinished request,
  never its key. The worker reads it from the same journal under the lock and
  requires its digest to equal the shown `journal_digest`.
- `capacity_warning` is true once `done_count >= done_limit - 16`.

`migrated_from` loses its key if present. A recipient without a version 2
journal is refused with `Journal migration required`; initialize it with the
protected CLI first.

`done_limit` stays 64 with no eviction, and replay keeps working when the limit
is reached. At `keys_exhausted` every new request refuses and needs protected
maintenance. Plan root retirement before then, while enough keys remain to
delete every owned list through its exact inverse. Confirm a clean run and
pending state and the restored baseline, disable management, and archive the
whole journal unchanged. A replacement journal is a separately reviewed
provisioning epoch, never a reset of the live one.

## Deployed layout

`roles/web_mcp_endpoint` renders everything below from the validated endpoint
config; `filter_plugins/web_mcp.py` pins the values.

Installed source: `emby_operator_socket.py`, `emby_operator_entry.py`,
`emby_operator_adapter.py` and `emby_operator_recommendations.py`, byte for byte
as pinned, in the root-owned `0555` release directory
`/opt/holonet-playlists/releases/<release>`, files `0444`. The release name is the
SHA256 of the sorted `sha256sum`-style manifest of the four pins. The existing
protected CLI and its `source/` directory under the Igor operator root are not
touched; the worker carries its own copy of the same core bytes. A CLI installed
from this source imports `emby_operator_entry.py`, so it needs that file too.

Policy file, fixed path `/etc/holonet-playlists/policy.json`, root-owned `0600`
in a root-owned `0700` directory whose ancestors are root-owned and not group
or world writable. It is read with the same no-follow `private_read` as the
operator config:

```json
{
  "version": 1,
  "enabled": true,
  "peer": {"uid": 940, "gid": 940},
  "caller": {
    "issuer": "<exact issuer of the separate writer identity>",
    "subject": "<exact single writer subject>",
    "namespace": "managed-recommendations",
    "server": "3c86e6d7ff6c4a3296522cdf03d22bb0",
    "library": "3",
    "catalog_user": "54b7d36cef804d3c8478474553ed3a82"
  },
  "recipients": {
    "545014d1c9d1416b8e0b5a445d6ff9ec": "/var/lib/holonet-operator/igor-pr116-retain-20261031-next/config.json",
    "db2f3bfb1f84438ebcde94f228183011": "/var/lib/holonet-operator/viewers-20261101/kostya/config.json",
    "313e39bb4d8c4b92b81d4b170c3f1b8e": "/var/lib/holonet-operator/viewers-20261101/lisa/config.json",
    "c06c7d2c3e224d8ba2e466efa5bcb97b": "/var/lib/holonet-operator/viewers-20261101/oleg/config.json",
    "22703968ab0b48eebb4faf7c1d16262c": "/var/lib/holonet-operator/viewers-20261101/tanya/config.json",
    "e05efa68303043b893b0f5c2e200ccd7": "/var/lib/holonet-operator/viewers-20261101/tetiana/config.json",
    "b8f4f6de49b443f1948cf0118679e863": "/var/lib/holonet-operator/viewers-20261101/tv/config.json"
  }
}
```

The installer writes it from the endpoint config: `caller.issuer` and
`caller.server` are the top-level `issuer` and `server_id`, the other caller
fields come from `playlist_operator`, and `recipients` holds only the selected
IDs, each mapped through the fixed seven-entry table above
(`OPERATOR_RECIPIENTS`). Exactly these fields;
1 to 16 recipients; lowercase 32-hex IDs; absolute, distinct config paths. Each
selected config must be enabled and match its map key as `recipient`, plus the
policy `server` and `library`, or the request is `unavailable`. The policy's
`enabled` is the worker kill switch.

Socket parent: `/run/holonet-playlists`, `root:holonet 0750`, declared by
`/etc/tmpfiles.d/holonet-playlists.conf` so it is recreated at every boot.
`DirectoryMode=` alone would create it `root:root`, which the service cannot
search, so the socket unit asserts the directory exists instead. No unit uses
`RuntimeDirectory=`, which would remove the parent when a worker exits.

Socket unit `holonet-playlists-worker.socket`:
`ListenStream=/run/holonet-playlists/operator.sock`, `Accept=yes`,
`SocketUser=root`, `SocketGroup=holonet` (GID 940), `SocketMode=0660`,
`MaxConnections=4`, `RemoveOnStop=yes`, and no network listener.

Service template requirements (`holonet-playlists-worker@.service`):

- `User=root`, `ExecStart=/usr/bin/python3 -E -s -B <source>/emby_operator_socket.py`
  with no arguments. Do not use `-I`, which drops the script directory from the
  import path. The worker takes fd 3 from `LISTEN_FDS`/`LISTEN_PID`.
- `RuntimeMaxSec=150` (above the 120-second ceiling plus read and write
  bounds), `TasksMax=8`, `MemoryMax=128M`, `LimitNOFILE=64`, `UMask=0077`.
- `NoNewPrivileges=yes`, `CapabilityBoundingSet=` empty, `AmbientCapabilities=`
  empty, `ProtectSystem=strict`, `ReadWritePaths=` exactly the seven journal
  directories, `ProtectHome=yes`, `PrivateTmp=yes`, `PrivateDevices=yes`,
  `ProtectKernelTunables=yes`, `ProtectKernelModules=yes`,
  `ProtectKernelLogs=yes`, `ProtectControlGroups=yes`, `ProtectClock=yes`,
  `ProtectHostname=yes`, `RestrictSUIDSGID=yes`, `RestrictRealtime=yes`,
  `RestrictNamespaces=yes`, `LockPersonality=yes`,
  `MemoryDenyWriteExecute=yes`, `SystemCallArchitectures=native`,
  `SystemCallFilter=@system-service`. `ReadWritePaths=` names the selected
  recipients' existing `journal/` directories, so they must exist first.
- `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`: the inherited socket is
  AF_UNIX and the adapter needs HTTPS to the pinned Emby origin. Network
  egress limits (for example `IPAddressAllow=`) depend on the actual origin
  path and are not set.
- Credential, config, policy and source stay read-only through
  `ProtectSystem=strict`; only the journal directories are writable.

The existing `holonet.service` keeps `NoNewPrivileges`, empty capabilities and
read-only system paths. It needs only access to the socket through its GID.

Rollback: remove `playlist_operator` from the endpoint config and re-run the
role, which stops and disables the socket. Release, policy, configs, journals,
pending state and the credential stay in place; never delete a journal or the
shared credential. In an emergency, stopping the socket unit or setting the
policy `enabled` to false has the same effect until the next role run.

## Tests and boundaries

```sh
export HOME="${PWD%/}/.cache/holonet-operator-tests/home" TMPDIR="$HOME/tmp" PYTHONDONTWRITEBYTECODE=1
mkdir -p "$TMPDIR"
python3 scripts/test_emby_operator_socket.py
```

The tests connect over a real abstract-namespace AF_UNIX socket and check the
real `SO_PEERCRED` of the test process, real framing and every frozen fixture.
They drive the real shared entry, core and adapter against the loopback
recording model from `test_emby_operator_adapter.py`, with synthetic IDs only.
The test user stands in for root, and the policy and config loaders are
replaced, so root file custody, the systemd units, the hardened service's
socket connectivity, the Go client and live Emby behavior are not exercised.
None of this is runtime evidence; the contract's composed proof, maintained
install, pre-activation audit and native acceptance remain required.
