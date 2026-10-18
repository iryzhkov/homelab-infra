# LAN hosted Mission Control

Source only. Web, reader, capture, runtime, ingress socket and LAN route defaults
are independent and false. Release version/SHA and dedicated reader UID remain
unselected. The proposal registry is empty and is not loaded by the Akasha play.

Invoke only playbooks/mission-control.yml with --limit omarchy-normandy.
Supply mission_control_fleet_checkout as the canonical fleet checkout and
mission_control_fleet_revision as its exact reviewed40hex commit. The controller
must have a clean Git checkout at that revision. Set ANSIBLE_LIBRARY to that
checkout's library directory. The play composes canonical runtime and reader once,
then the infra web role once. It never migrates Normandy into fleet site management.

The runtime lives at /opt/chaldea-mission-control/releases/VERSION and uses existing
/opt/chaldea/node/bin/node26.10. The helper0.4.0/validator26.12 and current user client
are untouched. Production hosted release pins must follow actual publication and
archive/import/assets qualification, not a fixture digest or a guessed0.4.3 pin.

Web listens only on127.0.0.1:7392 with siteOrigin https://chaldeas.ryzhkov.dev.
It reads /var/cache/mission-control/latest.json through mission-control-cache and
has no helper/sudo/token or T3 dependency. Reader owns the cache directory2750 and
atomically replaces files0640. Reader config/state remain private. Web/capture NNPyes;
dedicated reader NNPno requires exact approved issuer-command sudo confinement.

Ingress is a new restricted socket/proxyd pair on192.168.70.234:7393, permitting
only edge192.168.20.33 and192.168.20.4 and forwarding to loopback7392. FreeBindyes
supports boot ordering. Akasha1936 is untouched. Any missing UDM rule must be an
operator-approved narrow edge-to-NormandyTCP7393 rule, never shared-port-group widening.
This source does not establish that7393 is available or already permitted.

The optional Traefik file uses the existing actual-peer LAN ranges20/70/90/130 /24,
exact Host and absent Cf-Ray. It has no XFF ipStrategy, buffering middleware or public
router. Forged XFF cannot admit a forbidden peer. Cf-Ray misses this router and retains
the existing catchall302 to https://igor.ryzhkov.dev/404. Streaming flush is immediate;
the15s heartbeat fits existing proxy timeouts. TLS and LAN DNS wildcard already cover
the name; no DNS, Cloudflare Tunnel/Access or auth changes belong here. Embedding stays
omitted with CSP frame-ancestors none in runtime defaults.

Enable ingress only after source/artifact/grant qualification and one non-PC producer
demonstration. /healthz is liveness, not fresh data. Cached stale serving must continue
when source/Akasha connectivity is unavailable. No live probes occurred in this lane.
Rollback removes the LAN route, stops only MC services and restores prior pinned config
and release. Keep bounded cache and previous release14days.
