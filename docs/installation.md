# Installation

## Requirements

| | Minimum |
| --- | --- |
| Raspberry Pi OS | **Trixie** (October 2025) |
| Python | **3.13** (included in Trixie) |

Earlier releases (Bookworm / Python 3.11) are not supported.

---

## Pi #1 — Server

Flash **Raspberry Pi OS Trixie** using Raspberry Pi Imager. Enable SSH during flash.

> **Tip:** Configure WiFi in Imager before flashing. The Pi will have `wlan0` (home WiFi) and `eth0` (static pool network `10.10.10.10`) active simultaneously — useful for SSH access at home and a clean pool network at the venue.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Tremplin/master/install.sh -o install.sh && bash install.sh server
```

The script:
- Installs Python dependencies via `uv`
- Creates the `tremplin` systemd service (starts on boot)
- Adds the user to the `dialout` group for serial port access
- Creates `~/TremplinData/` with `meet/`, `images/`, `icons/`, and `recorded/` subdirectories
- Copies `settings.default.json` to `~/TremplinData/settings.json`
- Downloads socket.io and xterm.js
- Sets the static IP to `10.10.10.10/24` (asks for confirmation — this will drop your SSH session if connected over Ethernet)
- Sets the hostname to `tremplin` (accessible as `tremplin.local` on the network)

---

## Pi #2 — Kiosk

Flash **Raspberry Pi OS Trixie** with SSH enabled.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Tremplin/master/install.sh -o install.sh && bash install.sh kiosk
```

The script configures Chromium to open fullscreen on boot pointing at `http://tremplin.local`.

> Pi #1 must be running and reachable before the kiosk boots.

---

## Cloud server

See [cloud.md](cloud.md) for deploying the optional public relay server.

---

## Running

The service starts automatically on boot. Manual control on Pi #1:

```bash
sudo systemctl start   tremplin
sudo systemctl stop    tremplin
sudo systemctl restart tremplin
journalctl -u tremplin -f          # live logs
```

---

## Updating

The easiest way is from the **Update & Backup** tab in the admin UI — it pulls the latest release, syncs dependencies, and restarts the service.

To update manually over SSH:

```bash
cd ~/Tremplin
git pull
uv sync
sudo systemctl restart tremplin
```

Pi #2 only needs updating if the scoreboard template changed — a reboot is enough since Chromium reloads from Pi #1 on startup.

---

## Reinstalling

If the server is down and the web UI is unreachable, re-run the install script directly on Pi #1.

**From the desktop** — double-click the **Reinstall Scoreboard** icon created during install.

**From the terminal:**

```bash
bash ~/Tremplin/reinstall.sh
# or pass the role directly:
bash ~/Tremplin/reinstall.sh server
```
