"""Network telemetry: host identity, interfaces, reachability, listening sockets.

Designed to be safe to call from a regular user (no sudo) and to degrade
gracefully on missing tools (`iw`, `resolvectl`).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import psutil

_VETH_RE = re.compile(r"^(veth|br-)")

_TS_CACHE_TTL_SEC = 1.0
_ts_cache_lock = threading.Lock()
_ts_cache: dict = {"ts": 0.0, "data": None}


def _run(args: list[str], timeout: float = 2.0) -> tuple[int, str]:
    try:
        cp = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        return cp.returncode, (cp.stdout or "")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 127, ""


def _read_text(path: str | os.PathLike) -> str | None:
    try:
        return Path(path).read_text()
    except OSError:
        return None


def _read_pi_model() -> str | None:
    txt = _read_text("/sys/firmware/devicetree/base/model")
    return txt.strip("\x00").strip() if txt else None


def _read_os_pretty() -> str | None:
    txt = _read_text("/etc/os-release")
    if not txt:
        return None
    for line in txt.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def _tailscale_status() -> dict | None:
    """Cached `tailscale status --json` so host/reachability/peers share one subprocess call."""
    now = time.time()
    with _ts_cache_lock:
        if _ts_cache["data"] is not None and now - _ts_cache["ts"] < _TS_CACHE_TTL_SEC:
            return _ts_cache["data"]
    rc, out = _run(["tailscale", "status", "--json"], timeout=2.0)
    data: dict | None = None
    if rc == 0 and out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
    with _ts_cache_lock:
        _ts_cache["data"] = data
        _ts_cache["ts"] = now
    return data


def _tailscale_self() -> dict | None:
    d = _tailscale_status()
    if not d:
        return None
    self_ = d.get("Self") or {}
    return {
        "hostname": self_.get("HostName"),
        "dns_name": (self_.get("DNSName") or "").rstrip("."),
        "ips": self_.get("TailscaleIPs") or [],
        "tailnet": d.get("MagicDNSSuffix"),
        "backend_state": d.get("BackendState"),
        "online": bool(self_.get("Online", False)),
    }


def tailscale_peers() -> list[dict]:
    d = _tailscale_status()
    if not d:
        return []
    out: list[dict] = []
    for p in (d.get("Peer") or {}).values():
        ips = p.get("TailscaleIPs") or []
        last_seen = p.get("LastSeen") or ""
        # Tailscale uses 0001-01-01... as a sentinel for "no meaningful timestamp"
        # (typically for currently-online peers).
        if last_seen.startswith("0001-"):
            last_seen = None
        out.append({
            "hostname": p.get("HostName") or "",
            "dns_name": (p.get("DNSName") or "").rstrip("."),
            "ipv4": next((ip for ip in ips if ":" not in ip), None),
            "os": p.get("OS"),
            "online": bool(p.get("Online")),
            "last_seen": last_seen,
        })
    out.sort(key=lambda r: (not r["online"], r["hostname"].lower()))
    return out


def host() -> dict:
    uname = os.uname()
    return {
        "hostname": socket.gethostname(),
        "fqdn": socket.getfqdn(),
        "model": _read_pi_model(),
        "os": _read_os_pretty(),
        "kernel": f"{uname.sysname} {uname.release}",
        "arch": uname.machine,
        "tailscale": _tailscale_self(),
    }


def _wireless_levels() -> dict[str, dict]:
    txt = _read_text("/proc/net/wireless")
    if not txt:
        return {}
    out: dict[str, dict] = {}
    for line in txt.splitlines()[2:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        try:
            out[parts[0].rstrip(":")] = {
                "link_quality": float(parts[2].rstrip(".")),
                "signal_dbm": float(parts[3].rstrip(".")),
            }
        except ValueError:
            continue
    return out


def _wifi_ssid(iface: str) -> str | None:
    rc, out = _run(["iwgetid", "-r", iface], timeout=1.0)
    if rc == 0:
        ssid = out.strip()
        return ssid or None
    return None


def _cidr(ip: str | None, netmask: str | None) -> str | None:
    if not ip:
        return None
    if not netmask:
        return ip
    try:
        bits = sum(bin(int(o)).count("1") for o in netmask.split("."))
        return f"{ip}/{bits}"
    except ValueError:
        return ip


def interfaces() -> list[dict]:
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    io = psutil.net_io_counters(pernic=True)
    wireless = _wireless_levels()

    out: list[dict] = []
    for name in addrs:
        if name == "lo" or _VETH_RE.match(name):
            continue
        ipv4 = next((a for a in addrs[name] if a.family == socket.AF_INET), None)
        mac = next((a.address for a in addrs[name] if a.family == psutil.AF_LINK), None)
        st = stats.get(name)
        c = io.get(name)
        wl = wireless.get(name)
        if wl is not None:
            wl = {**wl, "ssid": _wifi_ssid(name)}
        out.append({
            "name": name,
            "up": st.isup if st else False,
            "speed_mbps": (st.speed if st and st.speed > 0 else None),
            "mtu": st.mtu if st else 0,
            "ipv4": _cidr(ipv4.address if ipv4 else None, ipv4.netmask if ipv4 else None),
            "mac": mac,
            "rx_bytes": c.bytes_recv if c else 0,
            "tx_bytes": c.bytes_sent if c else 0,
            "rx_packets": c.packets_recv if c else 0,
            "tx_packets": c.packets_sent if c else 0,
            "rx_errors": c.errin if c else 0,
            "tx_errors": c.errout if c else 0,
            "wireless": wl,
        })
    return out


def _default_routes() -> list[dict]:
    rc, out = _run(["ip", "-4", "route", "show", "default"], timeout=2.0)
    if rc != 0:
        return []
    routes: list[dict] = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "default":
            continue
        gw = parts[2] if "via" in parts else None
        dev = parts[parts.index("dev") + 1] if "dev" in parts else None
        metric = parts[parts.index("metric") + 1] if "metric" in parts else None
        routes.append({
            "gateway": gw,
            "iface": dev,
            "metric": int(metric) if metric and metric.isdigit() else None,
        })
    return routes


def _dns_servers() -> list[str]:
    for path in ("/run/systemd/resolve/resolv.conf", "/etc/resolv.conf"):
        txt = _read_text(path)
        if not txt:
            continue
        seen: set[str] = set()
        servers: list[str] = []
        for line in txt.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "nameserver" and parts[1] not in seen:
                seen.add(parts[1])
                servers.append(parts[1])
        if servers:
            return servers
    return []


def reachability() -> dict:
    return {
        "default_routes": _default_routes(),
        "dns_servers": _dns_servers(),
    }


def listening_sockets() -> list[dict]:
    seen: set[tuple[str, str, int]] = set()
    out: list[dict] = []
    for proto in ("tcp", "udp"):
        try:
            conns = psutil.net_connections(kind=proto)
        except (psutil.AccessDenied, OSError):
            continue
        for c in conns:
            if proto == "tcp" and c.status != psutil.CONN_LISTEN:
                continue
            if not c.laddr:
                continue
            key = (proto, c.laddr.ip, c.laddr.port)
            if key in seen:
                continue
            seen.add(key)
            proc = None
            if c.pid:
                try:
                    proc = psutil.Process(c.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    proc = None
            out.append({
                "proto": proto,
                "addr": c.laddr.ip,
                "port": c.laddr.port,
                "pid": c.pid,
                "process": proc,
            })
    out.sort(key=lambda r: (r["proto"], r["port"], r["addr"]))
    return out
