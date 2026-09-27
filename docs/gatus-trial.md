# Gatus trial

Gatus runs beside Uptime Kuma on homelab. Ansible renders `stacks/gatus/templates/config.yaml.j2` from inventory and deploys it as a mounted configuration file. The LAN dashboard is `http://192.168.70.223:3002`.

The first three checks mirror Kuma's Home Assistant and Zigbee2MQTT HTTP checks (2xx), and its MQTT host ICMP ping. Gatus does not send alerts during the trial. Kuma remains the alert source, including its Discord route. The other active Kuma monitors are not yet covered.

Deploy with `ansible-playbook playbooks/site.yml -l homelab -e only=gatus`. Compare the three check results and failure history over a soak period before expanding coverage. Do not retire Kuma until all active monitors and notification behavior have been compared and an independent failure-domain check is retained.

To stop the trial without affecting Kuma, run `ssh homelab 'cd ~/app/gatus && docker compose down'`. The named volume retains trial history for a later restart.
