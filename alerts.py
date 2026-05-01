"""Threshold-based alerting against the live snapshot.

Rules are evaluated on every sampler tick. A rule fires when its condition
has been true for at least `sustain_sec` AND the last fire was more than
`cooldown_sec` ago. Fired alerts are dispatched to all configured channels
and recorded as `events` so they surface on the Overview timeline.

Channel transport is intentionally tiny (urllib only — no extra deps);
ntfy.sh and generic webhook are supported. All network errors are swallowed
so a flaky channel never breaks the sampler.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import config
import storage

_state_lock = threading.Lock()
# rule_id -> {first_seen: float|None, last_fired: float, firing: bool, last_value: float|bool|None}
_state: dict[str, dict] = {}

_OPS = {
    ">":       lambda v, t: v is not None and v >  t,
    ">=":      lambda v, t: v is not None and v >= t,
    "<":       lambda v, t: v is not None and v <  t,
    "<=":      lambda v, t: v is not None and v <= t,
    "is_true": lambda v, _: bool(v),
}


def _extract(snap: dict, metric: str):
    sys_ = snap.get("system") or {}
    if metric == "cpu":       return (sys_.get("cpu") or {}).get("percent")
    if metric == "memory":    return (sys_.get("memory") or {}).get("percent")
    if metric == "swap":      return (sys_.get("swap") or {}).get("percent")
    if metric == "disk_root": return (sys_.get("disk") or {}).get("percent")
    if metric == "temp":      return sys_.get("temp_c")
    throttle_now = ((sys_.get("throttle") or {}).get("now")) or {}
    if metric in throttle_now:
        return throttle_now[metric]
    return None


def _format_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:.1f}"
    return str(v)


def _format_message(rule: dict, value) -> str:
    threshold = rule.get("threshold")
    if rule["op"] == "is_true":
        return f"{rule['metric']} is active (rule {rule['id']})"
    return (f"{rule['metric']} {rule['op']} {threshold} "
            f"(current: {_format_value(value)}, rule {rule['id']})")


def _send(channel: dict, rule: dict, message: str) -> None:
    ch_type = channel.get("type")
    url = channel.get("url")
    if not url:
        return
    try:
        if ch_type == "ntfy":
            req = urllib.request.Request(
                url, data=message.encode("utf-8"), method="POST",
                headers={
                    "Title": f"pidashboard: {rule['id']}",
                    "Priority": channel.get("priority", "default"),
                    "Tags": channel.get("tags", "warning"),
                },
            )
            urllib.request.urlopen(req, timeout=5.0).read()
        elif ch_type == "webhook":
            payload = json.dumps({
                "id": rule["id"], "metric": rule["metric"], "message": message,
            }).encode("utf-8")
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5.0).read()
    except (urllib.error.URLError, OSError, TimeoutError):
        pass


def evaluate_and_dispatch(snap: dict) -> None:
    """Single tick: evaluate every rule and dispatch newly-fired alerts."""
    rules = getattr(config, "ALERT_RULES", []) or []
    channels = getattr(config, "ALERT_CHANNELS", []) or []
    if not rules:
        return
    now = time.time()
    fired: list[tuple[dict, object, str]] = []
    with _state_lock:
        for rule in rules:
            v = _extract(snap, rule["metric"])
            op = _OPS.get(rule["op"])
            cond = bool(op and op(v, rule.get("threshold")))
            st = _state.setdefault(rule["id"], {
                "first_seen": None, "last_fired": 0.0, "firing": False, "last_value": None,
            })
            st["last_value"] = v
            if cond:
                if st["first_seen"] is None:
                    st["first_seen"] = now
                sustained = (now - st["first_seen"]) >= rule.get("sustain_sec", 0)
                cooled = (now - st["last_fired"]) >= rule.get("cooldown_sec", 0)
                if sustained and cooled:
                    st["last_fired"] = now
                    st["firing"] = True
                    fired.append((rule, v, _format_message(rule, v)))
            else:
                if st["firing"]:
                    storage.record_event(
                        kind=f"alert.resolved.{rule['id']}",
                        detail=f"resolved (current {_format_value(v)})",
                        severity="info",
                    )
                st["first_seen"] = None
                st["firing"] = False
    # Dispatch + persist OUTSIDE the lock so a slow channel doesn't block evaluation.
    for rule, _v, msg in fired:
        storage.record_event(kind=f"alert.{rule['id']}", detail=msg, severity="warning")
        for ch in channels:
            _send(ch, rule, msg)


def status() -> list[dict]:
    rules = getattr(config, "ALERT_RULES", []) or []
    out: list[dict] = []
    with _state_lock:
        for rule in rules:
            st = _state.get(rule["id"], {})
            out.append({
                "id":         rule["id"],
                "metric":     rule["metric"],
                "op":         rule["op"],
                "threshold":  rule.get("threshold"),
                "firing":     st.get("firing", False),
                "last_value": st.get("last_value"),
                "last_fired": st.get("last_fired") or None,
            })
    return out
