# Dedicated UniFi network metrics

This role deploys official Unpoller 5.5.1 as a native, unprivileged service.
The linux-amd64 archive is pinned to SHA-256
`7fe8849d0e3e22d514072bf720b26093cc18552d28b473a4b51a03358524ec9b`.
It uses only upstream configuration, with explicit metric namespace `unifi`.

The console identity `unpoller` was created local-only with no email, no group
membership, and no WiFi/VPN assignments. Its dedicated role is
`Homelab monitoring`, granting only:

    {"manifest_unique_key": "network.management", "formulas": ["readonly"]}

The console's manifest expands this to `view:controller:network` and read-only
Network site scopes. No user/console/Protect management permission is included
in the assigned role. UniFi OS also supplies baseline identity/console VIEW
scopes to local users: authenticated self reports 13 scopes, all view/read
(including identity-prefixed VIEW scopes), with no write/manage/operate scope.
Read-only GET probes returned 403 for Protect bootstrap, while the baseline
Users/admin list, Users search and custom-role list returned 200. Thus the role
is exactly Network-only; the console's inherent directory VIEW access remains.
Authenticated self reports exactly `network.management: [readonly]`, one role,
local-only true, no email, zero groups and zero assignments. No mutation probes
or extra privilege changes were performed.
Existing identities and roles were preserved. Account ID:
`b505b518-7167-4e9e-a055-67c954d8bdb0`; role ID:
`c3843d3e-d716-4c0f-9712-b9080e944e35`. The role does not create or change
controller accounts; it reads only the new encrypted
`secrets/observability-unifi.sops.yaml` credential. Existing administrator
credentials are never installed on the polling host.

The password was encrypted before account creation. Runtime password and native
configuration files are mode 0600 in a 0700 service-owned directory. Native
`file://` password loading keeps the value out of arguments and configuration.
Clients' identifying labels are hashed. Event logs, alarms, IDS, DPI and Protect
collection are disabled. No notification or alert settings are changed.

The console presents a self-signed certificate for `unifi.local`, without LAN IP
SANs. The role uses upstream `ssl_cert_paths` exact certificate fingerprint
validation. This is an explicit identity pin, rather than accepting arbitrary
self-signed peers. The native library performs its initial unauthenticated GET
with ordinary verification disabled, then enforces the pin on requests that carry
credentials. It does not add a global trust root or modify console certificates.

Reviewed controller certificate SHA-256:
`f4166e2c01e059c829e7fa11ee96e86b475fb1f490bfa8da9256ff0e9e38273c`.
Validity: 2025-05-17 through 2027-08-20. It was independently checked through
192.168.90.1 and from homelab through 192.168.1.1. A certificate replacement
requires inspecting the new identity and updating the public pin; a wrong pin
fails safely. Upstream pinning replaces CA/hostname validation and does not
independently enforce certificate expiry, so track certificate rotation before
that date.

Run `playbooks/observability-unifi.yml`; move the single
`observability_unifi_pollers` inventory membership to select a new service host.
The checksum-pinned amd64 native binary supports Debian and Arch hosts. No application
service is restarted. The collector binds only `127.0.0.1:9130`, limits CPU to
10%, memory to 192MiB, and permits outbound connections only to the configured
console IP and loopback. It refreshes its cached controller data every 60 seconds.

Alloy on the chosen polling host must separately scrape `127.0.0.1:9130` with
`job=unifi`, stable `host` and `instance` labels. The native role does not edit
Alloy configuration. Monitoring can move by applying this role and encrypted
credential on another host that can reach the console; the console account and
certificate pin remain unchanged.

Validated: account login; read-only self, sysinfo, health, device and client
endpoints; exact role permissions and unchanged prior identities; official binary
checksum and CLI; native metrics smoke test; mismatched-pin rejection; Ansible
syntax; TOML parsing; systemd unit parsing. No write-permission mutation probes
were used.

References:

- [Official release](https://github.com/unpoller/unpoller/releases/tag/v5.5.1)
- [Official controller login guidance](https://unpoller.com/docs/install/controllerlogin/)
- [Tagged configuration example](https://github.com/unpoller/unpoller/blob/v5.5.1/examples/up.conf.example)
- [Native certificate fingerprint implementation](https://github.com/unpoller/unifi/blob/v6.3.1/unifi.go)
