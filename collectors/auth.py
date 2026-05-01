"""SSH login summary from the systemd journal.

Counts and lists recent `Accepted`/`Failed password` lines from the
ssh.service unit. Returns empty/zero values when the journal is unreadable
(non-`adm` user) or sshd hasn't logged anything in the window.
"""

from __future__ import annotations

import re
import subprocess

# Matches `Accepted <method> for <user> from <ip> port <port> ssh2`
_ACCEPTED = re.compile(r"Accepted\s+\S+\s+for\s+(\S+)\s+from\s+(\S+)")
# Matches `Failed <method> for [invalid user ]<user> from <ip> port <port> ssh2`
_FAILED = re.compile(r"Failed\s+\S+\s+for\s+(?:invalid user\s+)?(\S+)\s+from\s+(\S+)")
# Pull the timestamp prefix produced by --output=short-iso, e.g. "2026-05-01T11:25:23+03:00"
_TS = re.compile(r"^(\S+)\s")


def _journal(since: str = "24 hours ago") -> list[str]:
    try:
        cp = subprocess.run(
            ["journalctl", "_SYSTEMD_UNIT=ssh.service",
             "--since", since, "--no-pager", "--output=short-iso"],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return []
    return cp.stdout.splitlines()


def summary(window: str = "24 hours ago", recent: int = 10) -> dict:
    accepted: list[dict] = []
    failed: list[dict] = []
    for line in _journal(window):
        ts_match = _TS.match(line)
        ts = ts_match.group(1) if ts_match else ""
        m = _ACCEPTED.search(line)
        if m:
            accepted.append({"ts": ts, "user": m.group(1), "ip": m.group(2)})
            continue
        m = _FAILED.search(line)
        if m:
            failed.append({"ts": ts, "user": m.group(1), "ip": m.group(2)})
    return {
        "window":         window,
        "accepted_count": len(accepted),
        "failed_count":   len(failed),
        "recent_accepted": accepted[-recent:],
        "recent_failed":   failed[-recent:],
    }
