"""systemd timers via `systemctl list-timers --output=json`.

Returns one entry per timer unit with absolute next/last fire timestamps in
seconds-since-epoch (the JSON output reports microseconds; we divide here so
the frontend can `new Date(ts * 1000)` consistently with other collectors).
"""

from __future__ import annotations

import json
import subprocess


def _us_to_s(v) -> float | None:
    if not isinstance(v, (int, float)):
        return None
    if v <= 0:
        return None
    return v / 1_000_000.0


def collect_all() -> list[dict]:
    try:
        cp = subprocess.run(
            ["systemctl", "list-timers", "--all", "--no-pager", "--output=json"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return []
    try:
        items = json.loads(cp.stdout or "[]")
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for it in items:
        out.append({
            "unit":      it.get("unit") or "",
            "activates": it.get("activates") or "",
            "next":      _us_to_s(it.get("next")),
            "last":      _us_to_s(it.get("last")),
        })
    out.sort(key=lambda t: (t["next"] is None, t["next"] or 0))
    return out
