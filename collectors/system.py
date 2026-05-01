"""System metrics: CPU, memory, disk, temperature, uptime, top processes."""

from __future__ import annotations

import os
import subprocess
import time

import psutil

import config

# Filesystem types worth showing as "real" disks. Excludes squashfs (snap),
# tmpfs/devtmpfs, overlayfs, and other pseudo filesystems.
_REAL_FSTYPES = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "ntfs", "exfat", "zfs"}

# Bit positions in the vcgencmd get_throttled bitmask.
_THROTTLE_BITS_NOW = {
    "undervoltage": 0x1,
    "arm_freq_capped": 0x2,
    "throttled": 0x4,
    "soft_temp_limit": 0x8,
}
_THROTTLE_BITS_BOOT = {
    "undervoltage": 0x10000,
    "arm_freq_capped": 0x20000,
    "throttled": 0x40000,
    "soft_temp_limit": 0x80000,
}


def _read_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        pass
    try:
        temps = psutil.sensors_temperatures()
        for entries in temps.values():
            for entry in entries:
                if entry.current:
                    return float(entry.current)
    except (AttributeError, OSError):
        pass
    return None


def _throttle_status() -> dict | None:
    try:
        cp = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=1.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if cp.returncode != 0:
        return None
    out = (cp.stdout or "").strip()
    if "=" not in out:
        return None
    try:
        val = int(out.split("=", 1)[1], 16)
    except ValueError:
        return None
    return {
        "raw": val,
        "now": {k: bool(val & m) for k, m in _THROTTLE_BITS_NOW.items()},
        "since_boot": {k: bool(val & m) for k, m in _THROTTLE_BITS_BOOT.items()},
    }


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read().strip()
    except (OSError, ValueError):
        return None


def _parse_life_time(raw: str | None) -> int | None:
    """Translate the eMMC EXT_CSD `life_time` pair to a worst-case 0-100 % wear estimate.

    Format is two hex values like "0x01 0x01" (Type A / Type B regions). Each
    bucket = ~10 % of rated lifetime; 0x0b means "exceeded". We return the
    higher of the two, mapped to a percent. Returns None for SD cards that
    don't expose the attribute.
    """
    if not raw:
        return None
    try:
        parts = [int(x, 16) for x in raw.split() if x]
    except ValueError:
        return None
    if not parts:
        return None
    bucket = max(parts)
    if bucket == 0 or bucket > 0x0b:
        return None
    if bucket == 0x0b:
        return 100
    return min(100, bucket * 10)


def _storage_health() -> list[dict]:
    """Per-block-device wear/write metrics. eMMC exposes life_time/pre_eol_info;
    SD cards don't, in which case we still return write totals from /sys stat."""
    out: list[dict] = []
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        return out
    for name in names:
        if not (name.startswith("mmcblk") or name.startswith("sd") or name.startswith("nvme")):
            continue
        # Skip partitions / loop / dm devices already filtered above.
        base = f"/sys/block/{name}"
        stat = _read_text(f"{base}/stat")
        sectors_written: int | None = None
        if stat:
            fields = stat.split()
            if len(fields) >= 7:
                try:
                    sectors_written = int(fields[6])
                except ValueError:
                    pass
        life_pct = _parse_life_time(_read_text(f"{base}/device/life_time"))
        pre_eol_raw = _read_text(f"{base}/device/pre_eol_info")
        pre_eol = None
        if pre_eol_raw:
            pre_eol_map = {"0x01": "normal", "0x02": "warning", "0x03": "urgent"}
            pre_eol = pre_eol_map.get(pre_eol_raw.strip())
        model = _read_text(f"{base}/device/name") or _read_text(f"{base}/device/model")
        # Skip devices we can't say anything useful about.
        if sectors_written is None and life_pct is None:
            continue
        out.append({
            "device": f"/dev/{name}",
            "model": model,
            "bytes_written_since_boot": (sectors_written * 512) if sectors_written is not None else None,
            "life_used_pct": life_pct,
            "pre_eol": pre_eol,
        })
    return out


def _disks() -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for p in psutil.disk_partitions(all=False):
        if p.fstype not in _REAL_FSTYPES:
            continue
        if p.mountpoint in seen:
            continue
        seen.add(p.mountpoint)
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        out.append({
            "mount": p.mountpoint,
            "device": p.device,
            "fstype": p.fstype,
            "total": u.total,
            "used": u.used,
            "free": u.free,
            "percent": u.percent,
        })
    out.sort(key=lambda d: (d["mount"] != "/", d["mount"]))
    return out


def _safe(fn, default=None):
    try:
        return fn()
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return default


def process_info(pid: int) -> dict | None:
    try:
        p = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return None
    with p.oneshot():
        cmdline = _safe(p.cmdline) or []
        return {
            "pid": pid,
            "name": _safe(p.name) or "",
            "cmdline": " ".join(cmdline) if cmdline else None,
            "exe": _safe(p.exe),
            "cwd": _safe(p.cwd),
            "username": _safe(p.username),
            "status": _safe(p.status),
            "create_time": _safe(p.create_time),
            "num_threads": _safe(p.num_threads),
            "memory_percent": _safe(p.memory_percent),
            "cpu_percent": _safe(p.cpu_percent),
        }


def tracked_processes(names: list[str]) -> list[dict]:
    """Sum CPU%/MEM% across every PID matching one of `names`. A name matches
    if it equals the process executable name (psutil.Process.name()) OR appears
    as a substring of any argv element — this catches Python services like
    `python3 -m pidashboard` whose exe name is just "python3".
    Returns one entry per requested name (with count=0 when no match) so the
    history has stable series identifiers across restarts."""
    if not names:
        return []
    by_name: dict[str, dict] = {n: {"name": n, "cpu": 0.0, "mem": 0.0, "count": 0, "pids": []} for n in names}
    for p in psutil.process_iter(["pid", "name", "cmdline", "cpu_percent", "memory_percent"]):
        info = p.info
        proc_name = info.get("name") or ""
        cmdline = info.get("cmdline") or []
        cmdline_blob = " ".join(cmdline)
        for wanted in names:
            if wanted == proc_name or (wanted and wanted in cmdline_blob):
                by_name[wanted]["cpu"] += info.get("cpu_percent") or 0.0
                by_name[wanted]["mem"] += info.get("memory_percent") or 0.0
                by_name[wanted]["count"] += 1
                by_name[wanted]["pids"].append(info.get("pid"))
                break  # avoid double-counting one PID under two names
    for entry in by_name.values():
        entry["cpu"] = round(entry["cpu"], 1)
        entry["mem"] = round(entry["mem"], 1)
    return list(by_name.values())


def _top_processes(n: int) -> list[dict]:
    procs: list[dict] = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        info = p.info
        procs.append(
            {
                "pid": info["pid"],
                "name": info["name"] or "",
                "cpu": round(info["cpu_percent"] or 0.0, 1),
                "mem": round(info["memory_percent"] or 0.0, 1),
            }
        )
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:n]


def collect() -> dict:
    cpu_percent = psutil.cpu_percent(interval=None)
    cpu_per_core = psutil.cpu_percent(interval=None, percpu=True)
    vm = psutil.virtual_memory()
    sm = psutil.swap_memory()
    du = psutil.disk_usage("/")
    disks = _disks()
    boot = psutil.boot_time()
    load1, load5, load15 = os.getloadavg()
    return {
        "ts": time.time(),
        "cpu": {
            "percent": cpu_percent,
            "per_core": cpu_per_core,
            "load1": load1,
            "load5": load5,
            "load15": load15,
        },
        "memory": {
            "total": vm.total,
            "used": vm.used,
            "available": vm.available,
            "percent": vm.percent,
        },
        "swap": {
            "total": sm.total,
            "used": sm.used,
            "percent": sm.percent,
        },
        "disk": {
            "total": du.total,
            "used": du.used,
            "free": du.free,
            "percent": du.percent,
        },
        "disks": disks,
        "storage_health": _storage_health(),
        "throttle": _throttle_status(),
        "temp_c": _read_temp_c(),
        "uptime_sec": time.time() - boot,
        "top_processes": _top_processes(config.TOP_PROCESS_COUNT),
        "tracked_processes": tracked_processes(getattr(config, "TRACKED_PROCESSES", []) or []),
    }
