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
    _conn.commit()


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
