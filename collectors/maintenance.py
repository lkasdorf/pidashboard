"""System maintenance status: pending apt updates and reboot-required marker.

`apt list --upgradable` is slow (1–10 s on a fresh cache, can be longer with
network access). To avoid blocking user requests we maintain a module-level
cache that is refreshed by a daemon thread on a long interval. Frontend
callers always read the cache and never trigger a refresh themselves.
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path

REFRESH_INTERVAL_SEC = 3600.0  # 1 h
APT_TIMEOUT_SEC = 60.0

_cache_lock = threading.Lock()
_cache: dict = {
    "updates": None,            # int | None  (None = not yet checked / unavailable)
    "updates_checked_at": None, # epoch seconds
}
_thread_started = False


def reboot_required() -> dict:
    flag = Path("/var/run/reboot-required")
    pkgs_path = Path("/var/run/reboot-required.pkgs")
    if not flag.exists():
        return {"required": False, "packages": []}
    pkgs: list[str] = []
    try:
        if pkgs_path.exists():
            pkgs = sorted({line.strip() for line in pkgs_path.read_text().splitlines() if line.strip()})
    except OSError:
        pass
    return {"required": True, "packages": pkgs}


def _count_upgradable() -> int | None:
    try:
        cp = subprocess.run(
            ["apt", "list", "--upgradable"],
            capture_output=True, text=True, timeout=APT_TIMEOUT_SEC, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if cp.returncode != 0:
        return None
    return sum(1 for line in cp.stdout.splitlines() if "upgradable" in line)


def _refresh_loop() -> None:
    while True:
        count = _count_upgradable()
        with _cache_lock:
            _cache["updates"] = count
            _cache["updates_checked_at"] = time.time()
        time.sleep(REFRESH_INTERVAL_SEC)


def start() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    threading.Thread(target=_refresh_loop, name="pidashboard-maintenance", daemon=True).start()


def status() -> dict:
    with _cache_lock:
        updates = _cache["updates"]
        checked_at = _cache["updates_checked_at"]
    return {
        "reboot": reboot_required(),
        "updates": {"count": updates, "checked_at": checked_at},
    }
