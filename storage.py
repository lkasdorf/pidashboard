"""SQLite ring buffer for 2h of system samples."""

from __future__ import annotations

import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def init() -> None:
    global _conn
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
    _conn.execute(
        """
        CREATE TABLE IF NOT EXISTS samples (
            ts            REAL PRIMARY KEY,
            cpu_percent   REAL,
            mem_percent   REAL,
            swap_percent  REAL,
            disk_percent  REAL,
            temp_c        REAL,
            load1         REAL
        )
        """
    )
    _conn.commit()


def insert(system_sample: dict) -> None:
    if _conn is None:
        return
    with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO samples VALUES (?,?,?,?,?,?,?)",
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
        cutoff = time.time() - config.HISTORY_RETENTION_SEC
        _conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        _conn.commit()


def history() -> list[dict]:
    if _conn is None:
        return []
    cutoff = time.time() - config.HISTORY_RETENTION_SEC
    with _lock:
        rows = _conn.execute(
            "SELECT ts, cpu_percent, mem_percent, swap_percent, disk_percent, temp_c, load1 "
            "FROM samples WHERE ts >= ? ORDER BY ts ASC",
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
