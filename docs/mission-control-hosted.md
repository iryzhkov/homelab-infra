# Canonical hosted Mission Control

The LAN canary is live at https://chaldeas.ryzhkov.dev through VIP192.168.20.3 and
both edge proxies192.168.20.33/.4. One agent-a status source is connected with zero
rows; PC threads/jobs/quota/roadmap are not connected. Full completed history and
remaining thread-transition/outage/token-semantics limits:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.

Invoke playbooks/mission-control.yml with literal --limit omarchy-normandy, the exact
clean reviewed mission_control_fleet_checkout/revision and
ANSIBLE_LIBRARY=ABS_F/library. The controller HEAD and clean-tree guards run first.
Pass the absolute checkout as an extra variable so Ansible can resolve the static
runtime import. Its public defaults are visible to earlier staging templates while
runtime installation still follows the stage-only guard. Inventory can override the
enabled flags and runtime defaults. The explicit fleet deployment packet is optional extra-vars and defaults off.

mission_control_activate defaults false. The composition inspects all selected owned
units before staging or runtime writes and refuses stage-only updates if any is active
or enabled. It stages web/ingress and reader once, installs the canonical runtime,
then runs target SYSTEM-mode systemd-analyze verify --man=no with all FOUR installed
files together: mission-control-reader.service, mission-control.service,
mission-control-ingress.socket and mission-control-ingress.service.
Only a successful gate permits any daemon reload, proxy stop, UFW or service action.

For warm updates, use one stage → verify → activate invocation with
mission_control_activate=true. The activation includes preserve registered config/unit
changed results. They restart changed services and leave an unchanged second real run
at changed=0. Never rerun main to activate and discard those results. Check mode defers
absent parents or identities and verification; existing web files show proposed diffs.
Predicted bytes/accounts are not qualified.
False flags do not roll back running services.

Templates and security constraints remain unchanged. Web NNP=yes/cache-only/
127.0.0.1:7392; reader UID940 NNP=no for the exact issuer sudo hop only,
issuer access0700/SQLite0600, private state0700/config0640, cache2750/file0640.
Ingress socket/proxyd binds192.168.70.234:7393 with exact two-edge peer/BPF controls.
Activation retains the existing two fixed UFW /32 rules and guards.
Traefik retains actual-peer LAN20/70/90/130 /24, no XFF ipStrategy and Cf-Ray catchall
302 to https://igor.ryzhkov.dev/404. DNS/wildcard TLS/auth remain unchanged.

Operator-managed UDM prerequisite: existing owned policy6ad564b11825b50cb1af9371,
192.168.20.33 and192.168.20.4 →192.168.70.234 TCP7393 only.
This repository does not converge UDM; its inventory still reports unconverged.
Do not widen shared port groups, manage generated rules or add UDM automation.

Homelab playbooks/akasha.yml selects qualified host024 canonically, retaining022 shared
trust and023 rollback only in its artifact-role invocation. Permissions and activation
remain explicit gates. Rollback requires matching old registry/config before023,
never restoration of an old access/antirollback database. Stop/disable owned MC units
and withdraw only the owned edge route explicitly. Root owns live convergence.
