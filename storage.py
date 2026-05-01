"""SQLite ring buffers for system metric history.

Two tables, one process:
  samples       — high-resolution (HISTORY_INTERVAL_SEC, ~30 s), HISTORY_RETENTION_SEC (2 h).
                  Drives the live sparklines.
  samples_long  — coarse (LONG_HISTORY_INTERVAL_SEC, ~60 s), LONG_HISTORY_RETENTION_SEC (7 d).
                  Drives the trend view.
Both share the same column schema so the read path is parametrised by table name.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

_COLUMNS = "ts, cpu_percent, mem_percent, swap_percent, disk_percent, temp_c, load1"
_RANGES = {
    "2h": ("samples",      config.HISTORY_RETENTION_SEC),
    "7d": ("samples_long", config.LONG_HISTORY_RETENTION_SEC),
}


def _create(table: str) -> str:
    return f"""
        CREATE TABLE IF NOT EXISTS {table} (
            ts            REAL PRIMARY KEY,
            cpu_percent   REAL,
            mem_percent   REAL,
            swap_percent  REAL,
            disk_percent  REAL,
            temp_c        REAL,
            load1         REAL
        )
        """


def init() -> None:
    global _conn
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    _conn.execute(_create("samples"))
    _conn.execute(_create("samples_long"))
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            ts       REAL NOT NULL,
            kind     TEXT NOT NULL,
            detail   TEXT,
            severity TEXT
        )
        """
    )
    _conn.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS net_samples (
            ts        REAL NOT NULL,
            iface     TEXT NOT NULL,
            rx_bytes  INTEGER,
            tx_bytes  INTEGER,
            PRIMARY KEY (ts, iface)
        )
        """
    )
    _conn.commit()


def insert_net(ts: float, samples: list[dict]) -> None:
    """Persist current cumulative byte counters for a list of interfaces.

    `samples` is a list of {iface, rx_bytes, tx_bytes}. Frontend turns
    consecutive samples into a bps rate with the timestamp delta.
    """
    if _conn is None or not samples:
        return
    with _lock:
        _conn.executemany(
            "INSERT OR REPLACE INTO net_samples VALUES (?,?,?,?)",
            [(ts, s["iface"], s.get("rx_bytes"), s.get("tx_bytes")) for s in samples],
        )
        cutoff = time.time() - config.LONG_HISTORY_RETENTION_SEC
        _conn.execute("DELETE FROM net_samples WHERE ts < ?", (cutoff,))
        _conn.commit()


def net_history(iface: str) -> list[dict]:
    if _conn is None:
        return []
    cutoff = time.time() - config.LONG_HISTORY_RETENTION_SEC
    with _lock:
        rows = _conn.execute(
            "SELECT ts, rx_bytes, tx_bytes FROM net_samples WHERE iface = ? AND ts >= ? ORDER BY ts ASC",
            (iface, cutoff),
        ).fetchall()
    return [{"ts": r[0], "rx": r[1], "tx": r[2]} for r in rows]


def net_ifaces_with_history() -> list[str]:
    if _conn is None:
        return []
    cutoff = time.time() - config.LONG_HISTORY_RETENTION_SEC
    with _lock:
        rows = _conn.execute(
            "SELECT DISTINCT iface FROM net_samples WHERE ts >= ? ORDER BY iface",
            (cutoff,),
        ).fetchall()
    return [r[0] for r in rows]


def record_event(kind: str, detail: str = "", severity: str = "info") -> None:
    if _conn is None:
        return
    with _lock:
        _conn.execute(
            "INSERT INTO events (ts, kind, detail, severity) VALUES (?,?,?,?)",
            (time.time(), kind, detail, severity),
        )
        # Keep events for the same window as the long history.
        cutoff = time.time() - config.LONG_HISTORY_RETENTION_SEC
        _conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        _conn.commit()


def recent_events(limit: int = 100) -> list[dict]:
    if _conn is None:
        return []
    with _lock:
        rows = _conn.execute(
            "SELECT ts, kind, detail, severity FROM events ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"ts": r[0], "kind": r[1], "detail": r[2], "severity": r[3]} for r in rows]


def _insert_into(table: str, retention_sec: int, system_sample: dict) -> None:
    if _conn is None:
        return
    with _lock:
        _conn.execute(
            f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?,?,?)",
            (
                system_sample["ts"],
                system_sample["cpu"]["percent"],
                system_sample["memory"]["percent"],
                system_sample["swap"]["percent"],
                system_sample["disk"]["percent"],
                system_sample.get("temp_c"),
                system_sample["cpu"]["load1"],
            ),
        )
        cutoff = time.time() - retention_sec
        _conn.execute(f"DELETE FROM {table} WHERE ts < ?", (cutoff,))
        _conn.commit()


def insert(system_sample: dict) -> None:
    _insert_into("samples", config.HISTORY_RETENTION_SEC, system_sample)


def insert_long(system_sample: dict) -> None:
    _insert_into("samples_long", config.LONG_HISTORY_RETENTION_SEC, system_sample)


def history(range_key: str = "2h") -> list[dict]:
    if _conn is None:
        return []
    table, retention_sec = _RANGES.get(range_key, _RANGES["2h"])
    cutoff = time.time() - retention_sec
    with _lock:
        rows = _conn.execute(
            f"SELECT {_COLUMNS} FROM {table} WHERE ts >= ? ORDER BY ts ASC",
            (cutoff,),
        ).fetchall()
    return [
        {
            "ts": r[0],
            "cpu": r[1],
            "mem": r[2],
            "swap": r[3],
            "disk": r[4],
            "temp": r[5],
            "load1": r[6],
        }
        for r in rows
    ]
