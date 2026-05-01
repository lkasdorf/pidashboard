"""Reachability monitor for a configured list of LAN/Tailnet devices.

Probes run on a background daemon thread on a fixed interval, in parallel
across hosts (one thread per probe so a slow device doesn't delay the rest).
The sampler reads the cached results — never triggers a probe itself —
so a flaky router can't slow down the SSE stream.

Three probe methods:
  ping  — `ping -c 1 -W <timeout>` (works without privileges on Linux because
          /bin/ping is suid; falls through to "method unsupported" if not).
  tcp   — `socket.create_connection((host, port), timeout)`. Best for embedded
          devices (Tasmota/Shelly/Tuya) that may have ICMP disabled but always
          serve their HTTP UI.
  http  — issue HEAD against `http(s)://host[:port]/`, accept any 2xx/3xx as up.
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import config

REFRESH_INTERVAL_SEC = 30.0
DEFAULT_TIMEOUT_SEC = 2.0
DEFAULT_TCP_PORT = 80

# Reachability is the goal, not authenticity — a NAS with a self-signed cert
# is still "up". Opt back into verification per device with `"verify_tls": True`.
_INSECURE_TLS = ssl.create_default_context()
_INSECURE_TLS.check_hostname = False
_INSECURE_TLS.verify_mode = ssl.CERT_NONE

_cache_lock = threading.Lock()
_cache: list[dict] = []  # populated by _refresh_loop with one dict per WATCHED_DEVICES entry
_thread_started = False


def _probe_ping(host: str, timeout: float) -> tuple[bool, float | None, str | None]:
    try:
        cp = subprocess.run(
            ["ping", "-c", "1", "-W", str(int(max(1, timeout))), "-q", host],
            capture_output=True, text=True, timeout=timeout + 1.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, None, str(exc) or "ping unavailable"
    if cp.returncode != 0:
        return False, None, "no reply"
    # Parse the "time=12.3 ms" from the last line "rtt min/avg/max/mdev = ..."
    rtt: float | None = None
    for line in cp.stdout.splitlines():
        if line.startswith("rtt") and "=" in line:
            try:
                stats = line.split("=", 1)[1].strip().split(" ", 1)[0]
                rtt = float(stats.split("/")[1])  # avg
            except (ValueError, IndexError):
                rtt = None
            break
    return True, rtt, None


def _probe_tcp(host: str, port: int, timeout: float) -> tuple[bool, float | None, str | None]:
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, (time.monotonic() - t0) * 1000.0, None
    except (OSError, socket.timeout) as exc:
        return False, None, type(exc).__name__


def _probe_http(host: str, port: int | None, timeout: float, scheme: str, verify_tls: bool) -> tuple[bool, float | None, str | None]:
    netloc = host if not port or (scheme == "http" and port == 80) or (scheme == "https" and port == 443) else f"{host}:{port}"
    url = f"{scheme}://{netloc}/"
    ctx = None if (scheme != "https" or verify_tls) else _INSECURE_TLS
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            ok = 200 <= resp.status < 400
            return ok, (time.monotonic() - t0) * 1000.0, (None if ok else f"http {resp.status}")
    except urllib.error.HTTPError as exc:
        # 4xx counts as "device replied" — many embedded devices return 401 here.
        return True, (time.monotonic() - t0) * 1000.0, f"http {exc.code}"
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        return False, None, type(exc).__name__


def _probe_one(spec: dict) -> dict:
    name = spec["name"]
    host = spec["host"]
    method = (spec.get("method") or "tcp").lower()
    timeout = float(spec.get("timeout") or DEFAULT_TIMEOUT_SEC)
    verify_tls = bool(spec.get("verify_tls", False))
    if method == "ping":
        ok, rtt, err = _probe_ping(host, timeout)
    elif method == "http":
        ok, rtt, err = _probe_http(host, spec.get("port"), timeout, "http", verify_tls)
    elif method == "https":
        ok, rtt, err = _probe_http(host, spec.get("port"), timeout, "https", verify_tls)
    else:  # tcp default
        ok, rtt, err = _probe_tcp(host, int(spec.get("port") or DEFAULT_TCP_PORT), timeout)
    return {
        "name":       name,
        "host":       host,
        "method":     method,
        "ok":         ok,
        "latency_ms": round(rtt, 1) if rtt is not None else None,
        "error":      err,
        "checked_at": time.time(),
    }


def _refresh_loop() -> None:
    while True:
        specs = list(getattr(config, "WATCHED_DEVICES", []) or [])
        if not specs:
            with _cache_lock:
                _cache[:] = []
            time.sleep(REFRESH_INTERVAL_SEC)
            continue
        # One worker per device — tiny LANs only, no need for a bounded pool.
        with ThreadPoolExecutor(max_workers=max(4, len(specs))) as ex:
            results = list(ex.map(_probe_one, specs))
        # Carry forward last_seen across this refresh so the UI can show "down for N min".
        with _cache_lock:
            prev_by_name = {r["name"]: r for r in _cache}
            for r in results:
                prev = prev_by_name.get(r["name"])
                if r["ok"]:
                    r["last_seen"] = r["checked_at"]
                else:
                    r["last_seen"] = prev["last_seen"] if prev and prev.get("last_seen") else None
            _cache[:] = results
        time.sleep(REFRESH_INTERVAL_SEC)


def start() -> None:
    global _thread_started
    if _thread_started:
        return
    _thread_started = True
    threading.Thread(target=_refresh_loop, name="pidashboard-devices", daemon=True).start()


def status() -> list[dict]:
    with _cache_lock:
        return list(_cache)
