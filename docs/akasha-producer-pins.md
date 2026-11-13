# P3.3 producer runtime pins

Use the published releases from Chaldea main b5656fa. Both archives were downloaded
with gh release download and their SHA-256 compared to the published manifest.json.

| Release | Archive | SHA-256 |
| --- | --- | --- |
| client-v0.4.27 | chaldea-client.tar.gz | a6a1f5c47ebb60d0372a1d1dc135d5a86d2a3eb0f4f2ba33f565e555a978ec0d |
| host-v0.2.21 | chaldea-host.tar.gz | 090bd269f0369f71b746d800ed68ced14f6ed9dff313cb439dfecb2d556bb524 |

vars/akasha-runtime.yml selects host021; the shared runtime stays on host022.
Clock012 and explicit rollback retention remain unchanged. Check fresh installed
release/cache custody before any deployment; these pins do not qualify live GC.

The homelab route publisher is manually staged, not managed by an infra role.
Its existing unit uses /opt/chaldea-route/client/bin/chaldea; that symlink currently
selects /opt/chaldea-route/releases/client-v0.4.22. Stage the verified client-v0.4.27
archive at /opt/chaldea-route/releases/client-v0.4.27 through the same protected
installation procedure, preserve the existing release, then switch that client
symlink during the root's rollout. The host archive has no route command.
Do not point the route client at a host archive. Keep its existing Node pin,
service configuration and credentials. Verify route delivery inside its service
namespace before selecting Authentik mode.

Fleet-config's normal agent_tools_sources pin advances homelab-cli to
d2099ddf44f36762cdd5b09bd27d5a284d09cc41, including homelab and Normandy.
No runtime is installed and no authentication mode is enabled by this PR task.
