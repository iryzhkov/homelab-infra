# Scoped Alpine syslog receiver firewall

Apply this role before deploying Alloy's RFC3164 UDP listener. It uses only the
OBS-SYSLOG filter chain and one INPUT jump matching UDP1514. Chain refresh is
atomic through iptables-restore --noflush; existing policies and other ports are
preserved. Loopback and the declared guest's IPv4 source to its parent's LAN IP
are allowed, all other UDP1514 packets dropped. The listener must bind only to
the explicit parent IPv4 address, not wildcard IPv6. No global firewall flush.

Declare observability_syslog_guest with name and address. The two permitted
network paths are ct110 192.168.70.250 → proxmox-a 192.168.1.5 UDP1514, and
ct201 192.168.20.83 → proxmox-b 192.168.1.29 UDP1514. The persistent oneshot unit
runs before Alloy at boot. Stopping the unit leaves restrictions in place.

Acceptance requires receiver ingestion with guest host labels and dummy-secret
redaction. Confirm local guest logs continue, scoped counters count allowed
guest packets, and a non-guest packet is dropped before retiring any legacy
Promtail. This role does not change UniFi policies, guest services or Promtail.
