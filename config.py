"""Configuration for pidashboard.

Lists which systemd units the dashboard monitors and may control,
which cronjobs are tracked (with their log files), and a few runtime knobs.

The service whitelist defined here MUST match the units enumerated in
deploy/pidashboard.sudoers — only those units can be controlled at runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

HOST: str = "0.0.0.0"
PORT: int = 8081

LIVE_INTERVAL_SEC: float = 2.5
HISTORY_INTERVAL_SEC: float = 30.0
HISTORY_RETENTION_SEC: int = 2 * 60 * 60

# Coarser, longer ringbuffer for trend views beyond the 2 h window.
LONG_HISTORY_INTERVAL_SEC: float = 60.0
LONG_HISTORY_RETENTION_SEC: int = 7 * 24 * 60 * 60

DB_PATH: Path = Path(__file__).parent / "data" / "samples.db"

CONTROLLABLE_SERVICES: list[str] = [
    "financeos.service",
    "tailscaled.service",
    "ssh.service",
    "cron.service",
    "pihole-FTL.service",
]

WATCHED_SERVICES: list[str] = [
    *CONTROLLABLE_SERVICES,
    "pidashboard.service",
]

ALLOWED_ACTIONS: tuple[str, ...] = ("start", "stop", "restart")

CRON_LOG_DIR: Path = Path(os.path.expanduser("~/logs"))

CRON_JOBS: list[dict[str, str]] = [
    {"name": "cron_commit",    "log": "cron_commit.log",    "schedule": "*/5 * * * *"},
    {"name": "cron_sched",     "log": "cron_sched.log",     "schedule": "0 6 * * *"},
    {"name": "cron_fx",        "log": "cron_fx.log",        "schedule": "0 7 * * *"},
    {"name": "cron_integrity", "log": "cron_integrity.log", "schedule": "30 6 * * 1"},
    {"name": "cron_metals",    "log": "cron_metals.log",    "schedule": "0 8 * * *"},
]

CRON_ERROR_PATTERNS: tuple[str, ...] = (
    "Traceback",
    "Error:",
    "ERROR",
    "FAILED",
    "fatal:",
    "Exception",
)

TOP_PROCESS_COUNT: int = 5

# Process names tracked over the long-history window (7 d at 60 s resolution).
# Matched by `psutil.Process.name()`. CPU/MEM is summed across all matching PIDs
# so a name with several worker children appears as one aggregated series.
TRACKED_PROCESSES: list[str] = [
    "financeos",
    "pidashboard",
    "tailscaled",
    "dockerd",
    "pihole-FTL",
]

# LAN/Tailnet devices probed every 30 s by collectors/devices.py.
# method: "ping" | "tcp" | "http" | "https"  (tcp is the default and most
# reliable for embedded firmware that may have ICMP disabled).
# `port` is required for tcp; optional for http/https (defaults to 80/443).
# `verify_tls` defaults to False — reachability is the goal, not authenticity,
# so self-signed certs on a NAS/router don't count as "down". Set to True for
# public endpoints where cert validity actually matters.
# Examples — uncomment + adapt to your network:
#     {"name": "router",     "host": "192.168.0.1",   "method": "http"},
#     {"name": "tasmota-1",  "host": "192.168.0.42",  "method": "tcp", "port": 80},
#     {"name": "shelly-pm",  "host": "192.168.0.55",  "method": "http"},
#     {"name": "uplink",     "host": "1.1.1.1",       "method": "ping"},
WATCHED_DEVICES: list[dict] = [
    {"name": "router",     "host": "192.168.0.1",   "method": "http"},
    {"name": "synology",   "host": "192.168.0.2",   "method": "https", "port": 443},
    {"name": "gw-223",     "host": "192.168.223.1", "method": "http"},
    {"name": "pihole-dns", "host": "127.0.0.1",     "method": "tcp",   "port": 53},
]

# Alert channels — leave empty to evaluate rules silently (Overview events
# panel still shows fired alerts). To get push notifications, add e.g.:
#   {"type": "ntfy", "url": "https://ntfy.sh/<random-hard-to-guess-topic>"}
#   {"type": "ntfy", "url": "https://ntfy.sh/<topic>", "priority": "high"}
#   {"type": "webhook", "url": "https://hooks.slack.com/..."}
ALERT_CHANNELS: list[dict] = []

# Each rule fires when its metric meets the op/threshold for `sustain_sec`
# consecutive seconds, and at most once per `cooldown_sec` while it stays true.
# Supported metrics:
#   cpu, memory, swap, disk_root, temp                — numeric (use >, >=, <, <=)
#   undervoltage, throttled, arm_freq_capped,
#     soft_temp_limit                                 — bool (use is_true)
#   device.<name>.ok                                  — bool, True iff probe ok
#                                                       (use is_false for "down")
#   pihole.dhcp_active                                — bool; pair with is_false
#                                                       to alert on DHCP off
#   pihole.gravity_stale                              — bool; True iff gravity.db
#                                                       mtime > 14 d (use is_true)
# To alert on a watched device going down for >60 s, add a rule like:
#   {"id": "router-down", "metric": "device.router.ok", "op": "is_false",
#    "sustain_sec": 60, "cooldown_sec": 600}
ALERT_RULES: list[dict] = [
    {"id": "cpu-high",       "metric": "cpu",                 "op": ">",        "threshold": 90, "sustain_sec": 300, "cooldown_sec": 1800},
    {"id": "temp-hot",       "metric": "temp",                "op": ">",        "threshold": 75, "sustain_sec": 60,  "cooldown_sec": 1800},
    {"id": "disk-full",      "metric": "disk_root",           "op": ">",        "threshold": 90, "sustain_sec": 0,   "cooldown_sec": 86400},
    {"id": "swap-high",      "metric": "swap",                "op": ">",        "threshold": 50, "sustain_sec": 600, "cooldown_sec": 3600},
    {"id": "undervoltage",   "metric": "undervoltage",        "op": "is_true",                   "sustain_sec": 0,   "cooldown_sec": 1800},
    {"id": "throttled",      "metric": "throttled",           "op": "is_true",                   "sustain_sec": 0,   "cooldown_sec": 1800},
    {"id": "pihole-dhcp-off","metric": "pihole.dhcp_active",  "op": "is_false",                  "sustain_sec": 60,  "cooldown_sec": 3600},
    {"id": "gravity-stale",  "metric": "pihole.gravity_stale","op": "is_true",                   "sustain_sec": 0,   "cooldown_sec": 86400},
]
