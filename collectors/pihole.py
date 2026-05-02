"""Pi-hole v6 stats collector.

Pulls four numbers off the local FTL install without going through the HTTP API
(no app-password ceremony, no extra deps, no third failure mode beyond filesystem):

  queries_last_hour / blocked_last_hour / blocked_pct
      Counts grouped by `status` from the FTL SQLite db (read-only WAL attach).
      The blocked-status set is taken from FTL's `enum-types.h`.
  dhcp_active
      Parsed out of `[dhcp]` section in /etc/pihole/pihole.toml.
  dhcp_leases
      Line count of /etc/pihole/dhcp.leases (dnsmasq format).
  gravity_age_sec / gravity_stale
      mtime of gravity.db. Stale threshold defaults to 14 days (Pi-hole's
      cron-driven gravity update is weekly, so 14 d gives one missed run of slack).

Files under /etc/pihole are mode 0640 owned by `pihole:pihole` — the dashboard
process needs to be in the `pihole` group, otherwise this collector reports
`available: False` and the panel goes muted. No fallback paths; if the user
is in the group the reads are cheap and fast, if not we fail loudly in one place.

Runs on its own 30 s daemon thread (FTL queries get expensive on big home-LAN
DBs and we don't want to hold up the live SSE loop)."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import time

PIHOLE_DIR = "/etc/pihole"
FTL_DB = f"{PIHOLE_DIR}/pihole-FTL.db"
DHCP_LEASES = f"{PIHOLE_DIR}/dhcp.leases"
PIHOLE_TOML = f"{PIHOLE_DIR}/pihole.toml"
GRAVITY_DB = f"{PIHOLE_DIR}/gravity.db"

REFRESH_INTERVAL_SEC = 30.0
GRAVITY_STALE_SEC = 14 * 24 * 3600

# FTL v6 status codes that count as "blocked" — see FTL src/enums.h. Anything
# else (forwarded, cached, retried, dbbusy, special-domain, …) is allowed.
BLOCKED_STATUS = (1, 4, 5, 6, 7, 8, 9, 10, 11, 15, 18)

_TOML_DHCP_ACTIVE_RE = re.compile(r"^\s*active\s*=\s*(true|false)\b", re.IGNORECASE)

_cache_lock = threading.Lock()
_cache: dict = {"available": False, "checked_at": 0.0}
_thread_started = False


def _query_counts() -> tuple[int | None, int | None]:
    """Return (queries_last_hour, blocked_last_hour) or (None, None) on failure."""
    try:
        con = sqlite3.connect(f"file:{FTL_DB}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return None, None
    try:
        cutoff = int(time.time()) - 3600
        cur = con.execute(
            "SELECT status, COUNT(*) FROM query_storage WHERE timestamp > ? GROUP BY status",
            (cutoff,),
        )
        total = 0
        blocked = 0
        for status, n in cur.fetchall():
            total += n
            if status in BLOCKED_STATUS:
                blocked += n
        return total, blocked
    except sqlite3.Error:
        return None, None
    finally:
        con.close()


def _dhcp_active() -> bool | None:
    """Walk pihole.toml top-down; flip on `[dhcp]` and read first `active = …`
    inside that table. Returns None if file unreadable or key missing."""
    try:
        with open(PIHOLE_TOML, "r", encoding="utf-8") as fh:
            in_dhcp = False
            for line in fh:
                stripped = line.lstrip()
                if stripped.startswith("[") and stripped.rstrip().endswith("]"):
                    in_dhcp = stripped.rstrip().lower() == "[dhcp]"
                    continue
                if not in_dhcp:
                    continue
                m = _TOML_DHCP_ACTIVE_RE.match(line)
                if m:
                    return m.group(1).lower() == "true"
    except OSError:
        return None
    return None


def _dhcp_lease_count() -> int | None:
    try:
        with open(DHCP_LEASES, "r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())
    except OSError:
        return None


def _gravity_age_sec() -> int | None:
    try:
        return int(time.time() - os.stat(GRAVITY_DB).st_mtime)
    except OSError:
        return None


def _collect() -> dict:
    queries, blocked = _query_counts()
    dhcp_active = _dhcp_active()
    leases = _dhcp_lease_count()
    gravity_age = _gravity_age_sec()

    available = any(v is not None for v in (queries, dhcp_active, leases, gravity_age))

    pct: float | None = None
    if queries and queries > 0 and blocked is not None:
        pct = round(blocked / queries * 100.0, 1)

    return {
        "available":         available,
        "queries_last_hour": queries,
        "blocked_last_hour": blocked,
        "blocked_pct":       pct,
        "dhcp_active":       dhcp_active,
        "dhcp_leases":       leases,
        "gravity_age_sec":   gravity_age,
        "gravity_stale":     bool(gravity_age is not None and gravity_age > GRAVITY_STALE_SEC),
        "checked_at":        time.time(),
    }


def _refresh_loop() -> None:
    while True:
        try:
            snap = _collect()
        except Exception:
            snap = {"available": False, "checked_at": time.time()}
        with _cache_lock:
            _cache.clear()
            _cache.update(snap)
        time.sleep(REFRESH_INTERVAL_SEC)


def start() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    threading.Thread(target=_refresh_loop, name="pidashboard-pihole", daemon=True).start()


def status() -> dict:
    with _cache_lock:
        return dict(_cache)
