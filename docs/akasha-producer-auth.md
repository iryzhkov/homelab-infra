# Akasha producer authentication

Follow chaldea-fleet-config docs/akasha-producer-auth.md for rollout and checks.
Set the persistent per-host chaldea_token_helper_authentik_enabled flag in this
repository's inventory/host_vars/homelab.yml or normandy.yml; never use global mode
overrides. Run this playbook with both inventories and the reviewed
chaldea_fleet_config_checkout, its roles/library paths and qualified release pins.
Normandy removes the three retired UID 940 Mission Control reader grants before
sudoers regeneration. Homelab uses ProtectHome=tmpfs plus the read-only credential
bind: verify issuance inside the route service namespace. Rollback sets the host
flag false and removes that drop-in. Neither server gains job_finished grants.
