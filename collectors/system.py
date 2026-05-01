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
        "throttle": _throttle_status(),
        "temp_c": _read_temp_c(),
        "uptime_sec": time.time() - boot,
        "top_processes": _top_processes(config.TOP_PROCESS_COUNT),
    }
