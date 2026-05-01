"""System log tail via `journalctl --priority=...`.

Requires the user running pidashboard to be in `adm` (or `systemd-journal`) so
they can read system-wide journal entries. Returns an empty list if journalctl
is missing or the user lacks read access. Kernel mode uses `journalctl -k`
rather than `dmesg` so it works for non-root users with journal access.
"""

from __future__ import annotations

import subprocess

ALLOWED_PRIORITIES = {"err", "warning", "info"}
ALLOWED_SOURCES = {"journal", "kernel"}


def tail(priority: str = "warning", lines: int = 100, source: str = "journal") -> list[str]:
    if priority not in ALLOWED_PRIORITIES:
        priority = "warning"
    if source not in ALLOWED_SOURCES:
        source = "journal"
    lines = max(1, min(2000, int(lines)))
    cmd = ["journalctl", f"--priority={priority}", "-n", str(lines),
           "--no-pager", "--output=short-iso"]
    if source == "kernel":
        cmd.append("-k")
    try:
        cp = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return [f"(journalctl rc={cp.returncode})"]
    return cp.stdout.splitlines()
