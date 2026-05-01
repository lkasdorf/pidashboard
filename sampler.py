"""Background sampler: builds snapshots, broadcasts to SSE subscribers, persists history."""

from __future__ import annotations

import json
import queue
import threading
import time

import config
import storage
from collectors import cron_jobs as cron_coll
from collectors import services as svc_coll
from collectors import system as sys_coll

_subscribers: list[queue.Queue] = []
_sub_lock = threading.Lock()

_last_snapshot: dict | None = None
_snap_lock = threading.Lock()


def latest() -> dict | None:
    with _snap_lock:
        return _last_snapshot


def _set_latest(snap: dict) -> None:
    global _last_snapshot
    with _snap_lock:
        _last_snapshot = snap


def subscribe() -> queue.Queue:
    q: queue.Queue = queue.Queue(maxsize=8)
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue) -> None:
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)


def _broadcast(snap: dict) -> None:
    payload = json.dumps(snap)
    with _sub_lock:
        targets = list(_subscribers)
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass


def _build_snapshot() -> dict:
    return {
        "system": sys_coll.collect(),
        "services": svc_coll.collect_all(),
        "cron": cron_coll.collect_all(),
    }


def _loop() -> None:
    last_persist = 0.0
    while True:
        try:
            snap = _build_snapshot()
            _set_latest(snap)
            _broadcast(snap)
            now = time.time()
            if now - last_persist >= config.HISTORY_INTERVAL_SEC:
                storage.insert(snap["system"])
                last_persist = now
        except Exception as exc:
            print(f"[sampler] error: {exc}", flush=True)
        time.sleep(config.LIVE_INTERVAL_SEC)


def start() -> None:
    threading.Thread(target=_loop, name="pidashboard-sampler", daemon=True).start()
