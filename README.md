# pidashboard

A lightweight, single-host system monitoring dashboard for a Raspberry Pi. Live system metrics, service control, cron-job health, and network/Tailscale telemetry — served over HTTP/SSE on port 8081, intended for LAN or Tailscale access only.

![Stack](https://img.shields.io/badge/python-3.10%2B-blue) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Live system metrics over Server-Sent Events** — CPU (overall + per-core mini-bars), memory, temperature, disk usage per mountpoint, uptime, and Raspberry Pi throttle status (`vcgencmd get_throttled`).
- **Service overview** — `systemd` unit state for a configurable whitelist, with start/stop/restart buttons (via a narrow `sudoers` rule) and inline `journalctl` log viewer.
- **Cron-job tracking** — schedule humanised (`*/5 * * * *` → "every 5 min"), last run derived from log mtime, error detection limited to the *latest* log line so historical errors don't keep flagging recovered jobs.
- **Network panel** — interfaces with live bandwidth (RX/TX delta), default routes, DNS resolvers, listening sockets, and Tailscale peer list with online status and last-seen.
- **Maintenance pill** — surfaces pending `apt` updates and `/var/run/reboot-required` only when relevant. `apt list --upgradable` is refreshed by a daemon thread on a 1 h interval, never blocking a request.
- **Mount-point aware** — distinguishes real filesystems (ext4, vfat, …) from `squashfs`/`tmpfs`; each mount gets its own usage bar.
- **2 h rolling history** in a tiny SQLite ringbuffer for sparkline trends.
- **Tabbed UI** with URL-hash state, dynamic favicon coloured by current CPU load, and `prefers-reduced-motion` respected.
- **Production WSGI server** (Waitress) — no Flask dev server in production. SSE streaming verified through Waitress + `tailscale serve`.

## Stack

- Python 3.10+, Flask, Waitress, psutil, SQLite
- Vanilla JS frontend with native Canvas sparklines — no framework, no CDN
- `systemd` unit + minimal `sudoers` rule for service control

## Requirements

- A Raspberry Pi (developed on Pi 4) running a recent Linux distribution with `systemd`
- `vcgencmd` (in `libraspberrypi-bin` on Ubuntu) for throttle status — degrades gracefully if absent
- The user running `pidashboard` should be in the `adm` group to read other units' journal entries
- Optional: `tailscale` for tailnet-only HTTPS access

## Quick start

```bash
git clone https://github.com/lkasdorf/pidashboard.git
cd pidashboard
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Edit config.py to list the systemd units, cron jobs, and log directory
# you want to monitor. CONTROLLABLE_SERVICES must stay in sync with the units
# enumerated in deploy/pidashboard.sudoers.

# Run for development:
.venv/bin/python app.py --debug
```

Open `http://<host>:8081/`.

## Production deployment

### 1. Install the sudoers rule

So the dashboard can start/stop/restart whitelisted services without a password. Edit the units inside the file first to match your `CONTROLLABLE_SERVICES`.

```bash
sudo install -m 0440 -o root -g root \
  deploy/pidashboard.sudoers /etc/sudoers.d/pidashboard
sudo visudo -c
```

### 2. Install the systemd unit

Edit `User=`, `WorkingDirectory=`, and `ExecStart=` in `deploy/pidashboard.service` to match your environment.

```bash
sudo install -m 0644 -o root -g root \
  deploy/pidashboard.service /etc/systemd/system/pidashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now pidashboard.service
```

### 3. Optional: expose via Tailscale on a sub-path

If another app already owns `/` on your Tailscale node, mount the dashboard under `/pi`:

```bash
sudo tailscale serve --bg --set-path=/pi http://127.0.0.1:8081
```

The app is sub-path-aware via the `PIDASH_SCRIPT_NAME` environment variable. Add this line to `deploy/pidashboard.service`:

```
Environment=PIDASH_SCRIPT_NAME=/pi
```

The bundled WSGI middleware (`ScriptNamePrefix` in `app.py`) sets `SCRIPT_NAME` so `url_for()` generates correct prefixed URLs, and also strips the prefix from incoming `PATH_INFO` if the proxy doesn't — so the same code works whether the upstream proxy strips the prefix or passes it through.

## Layout

```
app.py                Flask routes + WSGI middleware for sub-path mounting
sampler.py            Background snapshot loop, SSE broadcaster
storage.py            SQLite ringbuffer for 2 h of metric history
config.py             Service whitelist, cron jobs, sampling intervals
collectors/
  system.py           CPU, memory, disks, temp, throttle, top processes
  services.py         systemd unit state, control, and journal tail
  cron_jobs.py        Cron log parsing, schedule humanisation, error detection
  network.py          Interfaces, reachability, Tailscale, listening sockets
  maintenance.py      apt/reboot status with hourly refresh thread
templates/index.html  Single-page tabbed UI
static/               CSS, JS, favicon
deploy/               systemd unit + sudoers template
```

## Security notes

- Designed to run **unauthenticated** on a private network or Tailnet only. Do not expose to the public internet.
- The sudoers rule is intentionally minimal: only the four `start`/`stop`/`restart` actions on a hard-coded unit list. Keep `CONTROLLABLE_SERVICES` in `config.py` synchronised with `deploy/pidashboard.sudoers`.
- The dashboard cannot control its own `systemd` unit (intentional — a `stop` button that kills the request handler is a footgun).
- `journalctl -u <unit>` access for non-root users requires being in `adm` (or `systemd-journal`) group; without it the log button just returns empty output.

## License

MIT — see [LICENSE](LICENSE).
