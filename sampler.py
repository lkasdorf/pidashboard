"""Background sampler: builds snapshots, broadcasts to SSE subscribers, persists history."""

from __future__ import annotations

import json
import queue
import threading
import time

import re

import psutil

import alerts
import config
import storage
from collectors import cron_jobs as cron_coll
from collectors import docker as docker_coll
from collectors import services as svc_coll
from collectors import system as sys_coll
from collectors import timers as timer_coll

_VETH_RE = re.compile(r"^(veth|docker|br-|tailscale|cni|flannel)")

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
        "timers": timer_coll.collect_all(),
        "docker": docker_coll.containers(),
    }


_THROTTLE_SEVERITY = {
    "undervoltage":    "error",
    "throttled":       "warning",
    "arm_freq_capped": "warning",
    "soft_temp_limit": "warning",
}


def _track_throttle_transitions(prev: dict | None, curr: dict | None) -> dict | None:
    """Persist 0→1 flips of any `now`-bit as events. Returns curr.now for next tick.
    First sample after boot reports any active flag as an event so the timeline
    shows what was already wrong on startup."""
    if not curr:
        return None
    now_bits = curr.get("now") or {}
    prev_bits = (prev or {}) if isinstance(prev, dict) else {}
    for k, on in now_bits.items():
        if on and not prev_bits.get(k, False):
            storage.record_event(
                kind=f"throttle.{k}",
                detail="active",
                severity=_THROTTLE_SEVERITY.get(k, "warning"),
            )
    return dict(now_bits)


def _net_samples() -> list[dict]:
    out: list[dict] = []
    try:
        io = psutil.net_io_counters(pernic=True)
    except Exception:
        return out
    for name, c in io.items():
        if name == "lo" or _VETH_RE.match(name):
            continue
        out.append({"iface": name, "rx_bytes": c.bytes_recv, "tx_bytes": c.bytes_sent})
    return out


def _loop() -> None:
    last_persist = 0.0
    last_persist_long = 0.0
    prev_throttle: dict | None = None
    while True:
        try:
            snap = _build_snapshot()
            _set_latest(snap)
            _broadcast(snap)
            now = time.time()
            if now - last_persist >= config.HISTORY_INTERVAL_SEC:
                storage.insert(snap["system"])
                last_persist = now
            if now - last_persist_long >= config.LONG_HISTORY_INTERVAL_SEC:
                storage.insert_long(snap["system"])
                storage.insert_net(now, _net_samples())
                storage.insert_proc(now, snap["system"].get("tracked_processes") or [])
                last_persist_long = now
            prev_throttle = _track_throttle_transitions(prev_throttle, snap["system"].get("throttle"))
            alerts.evaluate_and_dispatch(snap)
        except Exception as exc:
            print(f"[sampler] error: {exc}", flush=True)
        time.sleep(config.LIVE_INTERVAL_SEC)


def start() -> None:
    threading.Thread(target=_loop, name="pidashboard-sampler", daemon=True).start()
