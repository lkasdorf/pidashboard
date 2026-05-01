"""System log tail via `journalctl --priority=...`.

Requires the user running pidashboard to be in `adm` (or `systemd-journal`) so
they can read system-wide journal entries. Returns an empty list if journalctl
is missing or the user lacks read access.
"""

from __future__ import annotations

import subprocess

# journalctl accepts these names for `--priority`; we restrict to a small set
# the UI exposes via dropdown to avoid surprises.
ALLOWED_PRIORITIES = {"err", "warning", "info"}


def tail(priority: str = "warning", lines: int = 100) -> list[str]:
    if priority not in ALLOWED_PRIORITIES:
        priority = "warning"
    lines = max(1, min(2000, int(lines)))
    try:
        cp = subprocess.run(
            ["journalctl", f"--priority={priority}", "-n", str(lines),
             "--no-pager", "--output=short-iso"],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return [f"(journalctl rc={cp.returncode})"]
    return cp.stdout.splitlines()
