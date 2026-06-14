# `tremplin.local` won't load in the browser (but ping works)

## Symptom

- `ping tremplin.local` works.
- Loading `http://tremplin.local/` in a browser fails / hangs.
- Connecting to the Pi **by its IP address** works:
  `http://10.8.20.103/` and `http://10.8.20.103:5000/live` both load.

So the Pi, the Flask service, and the port-80 redirect are all healthy — only
the **hostname** fails to load.

## Cause

The Pi is multihomed (e.g. WiFi `wlan0` has an IP, `eth0` does not, or vice
versa). avahi advertises **every** address it has for `tremplin.local`,
including the **IPv6 link-local** address (`fe80::…`) on the active interface.

When a browser resolves `tremplin.local` it receives both an `AAAA`
(IPv6 link-local) and an `A` (IPv4) record. Many browsers/OSes **prefer IPv6**,
try the `fe80::…` address first, and a link-local IPv6 address can't be reached
without a zone index (`%iface`) — so the connection hangs or fails. `ping` and
direct-IP access worked because they used IPv4.

You can spot the IPv6 link-local being published in the avahi log:

```
avahi-daemon[680]: Registering new address record for fe80::e65f:1ff:fe86:772 on wlan0.*.
```

## Solution

Tell avahi not to use IPv6, so `tremplin.local` only ever resolves to the
reachable IPv4 address.

On the Pi, edit `/etc/avahi/avahi-daemon.conf` so the `[server]` section has:

```ini
use-ipv6=no
```

One-liner that handles both the "key exists" and "key missing" cases:

```bash
if grep -q '^#*use-ipv6=' /etc/avahi/avahi-daemon.conf; then
    sudo sed -i 's/^#*use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf
else
    sudo sed -i '/^\[server\]/a use-ipv6=no' /etc/avahi/avahi-daemon.conf
fi
sudo systemctl restart avahi-daemon
```

Confirm it took:

```bash
grep '^use-ipv6' /etc/avahi/avahi-daemon.conf   # should print: use-ipv6=no
```

Then flush the client's mDNS cache (or just wait ~30s) and reload
`http://tremplin.local/`:

- **macOS:** `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`
- **Windows:** `ipconfig /flushdns`

## Notes

- This is handled automatically by `install.sh` (in the **mDNS aliases**
  section) for new installs.
- Disabling IPv6 in avahi is safe for this LAN scoreboard use case — all
  clients reach the Pi over IPv4.
- If the page is still unreachable after this and **even direct IP fails from
  the client**, the network has WiFi client/AP isolation enabled (common on
  corporate networks). That blocks device-to-device traffic and can only be
  fixed on the network side, or by connecting clients to the pool-deck `eth0`
  network instead.
