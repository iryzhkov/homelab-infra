# Akasha producer authentication

Use the reviewed chaldea-fleet-config docs/akasha-producer-auth.md packet for the full six-host inventory, release prerequisites, per-host checks and rollback. This repository owns the homelab and normandy default-off chaldea_token_helper_authentik_enabled flags and playbooks/akasha-producer-auth.yml wrapper.

The wrapper imports the fleet implementation from chaldea_fleet_config_checkout. Supply both inventories, that checkout's roles/library paths and Node checksum group vars, and qualified client release pins. Limit one server. Check with diff, apply, repeat, then verify persisted feed/toolfeedback events and homelab route delivery. homelab and Normandy have no job_finished producer grants; do not invent them. The homelab route service gets one read-only credential-directory bind only while selected. Roll back with the same playbook and the flag false, then verify PKI append/read. No server verifier or trust installer runs.
