"""Cronjob status from log files: last run (mtime), size, error markers, tail."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import config

_DOW_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def _parse_schedule(spec: str) -> tuple[int | None, str]:
    """Returns (interval_sec, human_label).

    Recognises only the patterns we actually use:
    `*/N * * * *`, `M H * * *` (daily), `M H * * D` (weekly).
    Falls back to (None, spec) for anything else — UI will then just show
    the raw cron expression.
    """
    fields = spec.split()
    if len(fields) != 5:
        return None, spec
    minute, hour, dom, month, dow = fields
    if minute.startswith("*/") and hour == dom == month == dow == "*":
        try:
            n = int(minute[2:])
            return n * 60, f"every {n} min"
        except ValueError:
            return None, spec
    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*":
        m, h = int(minute), int(hour)
        if dow == "*":
            return 86400, f"daily {h:02d}:{m:02d}"
        if dow.isdigit():
            return 604800, f"weekly {_DOW_EN[int(dow) % 7]} {h:02d}:{m:02d}"
    return None, spec


def _next_run(spec: str, now: datetime | None = None) -> float | None:
    """Compute the next nominal Unix timestamp the schedule will fire.

    Same pattern coverage as `_parse_schedule`. Returns None for unsupported
    expressions. Local time, no DST awareness — close enough for a dashboard
    that says "next: in 3m".
    """
    fields = spec.split()
    if len(fields) != 5:
        return None
    minute, hour, dom, month, dow = fields
    n = (now or datetime.now()).replace(microsecond=0)

    if minute.startswith("*/") and hour == dom == month == dow == "*":
        try:
            step = int(minute[2:])
        except ValueError:
            return None
        if step <= 0:
            return None
        rem = n.minute % step
        delta_min = step - rem if rem > 0 or n.second > 0 else step
        candidate = n.replace(second=0) + timedelta(minutes=delta_min)
        return candidate.timestamp()

    if minute.isdigit() and hour.isdigit() and dom == "*" and month == "*":
        m, h = int(minute), int(hour)
        candidate = n.replace(hour=h, minute=m, second=0)
        if dow == "*":
            if candidate <= n:
                candidate += timedelta(days=1)
            return candidate.timestamp()
        if dow.isdigit():
            cron_dow = int(dow) % 7        # 0 = Sun in cron
            python_dow = (cron_dow - 1) % 7  # 0 = Mon in datetime
            days_ahead = (python_dow - n.weekday()) % 7
            if days_ahead == 0 and candidate <= n:
                days_ahead = 7
            return (candidate + timedelta(days=days_ahead)).timestamp()

    return None


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block = 4096
            data = b""
            while size > 0 and data.count(b"\n") <= lines:
                read = min(block, size)
                size -= read
                f.seek(size)
                data = f.read(read) + data
        text = data.decode("utf-8", errors="replace")
        return text.splitlines()[-lines:]
    except OSError:
        return []


def _matches_error(line: str) -> bool:
    return any(pat in line for pat in config.CRON_ERROR_PATTERNS)


def _job_state(log_path: Path) -> dict:
    if not log_path.exists():
        return {
            "last_run": None,
            "size": 0,
            "has_error": False,
            "last_error_line": None,
        }
    st = log_path.stat()
    tail = _tail(log_path, 200)
    # has_error reflects the CURRENT health: only true if the last non-empty
    # line matches an error pattern. A historical error followed by successful
    # output is treated as recovered.
    last_nonempty = next((ln for ln in reversed(tail) if ln.strip()), "")
    has_error = _matches_error(last_nonempty)
    # last_error_line still scans the full tail so the tooltip can show the most
    # recent error seen, even if the job has since recovered.
    last_error: str | None = next((ln for ln in reversed(tail) if _matches_error(ln)), None)
    return {
        "last_run": st.st_mtime,
        "size": st.st_size,
        "has_error": has_error,
        "last_error_line": last_error,
    }


def collect_all() -> list[dict]:
    out: list[dict] = []
    for job in config.CRON_JOBS:
        path = config.CRON_LOG_DIR / job["log"]
        interval_sec, schedule_human = _parse_schedule(job["schedule"])
        out.append(
            {
                "name": job["name"],
                "schedule": job["schedule"],
                "schedule_human": schedule_human,
                "interval_sec": interval_sec,
                "next_run": _next_run(job["schedule"]),
                "log": job["log"],
                **_job_state(path),
            }
        )
    return out


def tail_log(name: str, lines: int = 200) -> list[str]:
    for job in config.CRON_JOBS:
        if job["name"] == name:
            return _tail(config.CRON_LOG_DIR / job["log"], lines)
    return []
