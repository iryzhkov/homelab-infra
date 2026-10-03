# Travel router: ask both edge VMs for mesh DNS

For Igor, by hand. Agents do not change the travel router. Written 2026-10-02 after the phase
4 cutover.

Since 2026-10-02 both edge VMs answer DNS on the mesh, each with its own address for
`*.ryzhkov.dev`: public-api at 10.42.0.3 and public-api-b at 10.42.0.10. The travel router
still sends every home name to public-api only. While public-api is down, its clients lose all
home names.

On the travel router (GL-MT3000), reached on its LAN address 192.168.90.158 when it is home, in
`/etc/dnsmasq.d/ryzhkov.conf`:

1. Remove `address=/ryzhkov.dev/10.42.0.3` and the existing `server=10.42.0.3` line.
2. Make the file read:

   ```
   no-resolv
   strict-order
   server=1.1.1.1
   server=10.42.0.10
   server=10.42.0.3
   server=/ryzhkov.dev/10.42.0.3
   server=/ryzhkov.dev/10.42.0.10
   ```

3. Restart dnsmasq: `/etc/init.d/dnsmasq restart`.

Check, from a client behind the travel router:

- `nslookup vault.ryzhkov.dev` answers 10.42.0.3 or 10.42.0.10.
- A blocked domain still answers 0.0.0.0 with the mesh up, as in the 2026-09-02 test.

The `server=/ryzhkov.dev/` lines keep home names away from 1.1.1.1.
