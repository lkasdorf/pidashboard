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

# Alert channels — leave empty to evaluate rules silently (Overview events
# panel still shows fired alerts). To get push notifications, add e.g.:
#   {"type": "ntfy", "url": "https://ntfy.sh/<random-hard-to-guess-topic>"}
#   {"type": "ntfy", "url": "https://ntfy.sh/<topic>", "priority": "high"}
#   {"type": "webhook", "url": "https://hooks.slack.com/..."}
ALERT_CHANNELS: list[dict] = []

# Each rule fires when its metric meets the op/threshold for `sustain_sec`
# consecutive seconds, and at most once per `cooldown_sec` while it stays true.
# Supported metrics: cpu, memory, swap, disk_root, temp,
# undervoltage, throttled, arm_freq_capped, soft_temp_limit (the throttle
# bits use op="is_true").
ALERT_RULES: list[dict] = [
    {"id": "cpu-high",     "metric": "cpu",          "op": ">",       "threshold": 90, "sustain_sec": 300, "cooldown_sec": 1800},
    {"id": "temp-hot",     "metric": "temp",         "op": ">",       "threshold": 75, "sustain_sec": 60,  "cooldown_sec": 1800},
    {"id": "disk-full",    "metric": "disk_root",    "op": ">",       "threshold": 90, "sustain_sec": 0,   "cooldown_sec": 86400},
    {"id": "swap-high",    "metric": "swap",         "op": ">",       "threshold": 50, "sustain_sec": 600, "cooldown_sec": 3600},
    {"id": "undervoltage", "metric": "undervoltage", "op": "is_true",                  "sustain_sec": 0,   "cooldown_sec": 1800},
    {"id": "throttled",    "metric": "throttled",    "op": "is_true",                  "sustain_sec": 0,   "cooldown_sec": 1800},
]
