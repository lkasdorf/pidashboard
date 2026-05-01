"""Systemd service status + (whitelisted) start/stop/restart control."""

from __future__ import annotations

import subprocess

import config


def _run(args: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        cp = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
        return cp.returncode, (cp.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return 124, ""
    except FileNotFoundError:
        return 127, ""


def status(unit: str) -> dict:
    _, active = _run(["systemctl", "is-active", unit])
    _, enabled = _run(["systemctl", "is-enabled", unit])
    _, show = _run(
        [
            "systemctl",
            "show",
            unit,
            "--property=ActiveEnterTimestamp,SubState,MainPID,LoadState",
        ]
    )
    props: dict[str, str] = {}
    for line in show.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return {
        "unit": unit,
        "active": active,
        "enabled": enabled,
        "sub_state": props.get("SubState", ""),
        "main_pid": props.get("MainPID", ""),
        "active_since": props.get("ActiveEnterTimestamp", ""),
        "load_state": props.get("LoadState", ""),
    }


def collect_all() -> list[dict]:
    return [status(unit) for unit in config.WATCHED_SERVICES]


def control(unit: str, action: str) -> tuple[bool, str]:
    if unit not in config.CONTROLLABLE_SERVICES:
        return False, f"unit not allowed: {unit}"
    if action not in config.ALLOWED_ACTIONS:
        return False, f"action not allowed: {action}"
    rc, out = _run(["sudo", "-n", "systemctl", action, unit], timeout=15.0)
    if rc == 0:
        return True, "ok"
    return False, out or f"systemctl exited {rc}"


def journal(unit: str, lines: int = 200) -> list[str]:
    if unit not in config.WATCHED_SERVICES:
        return []
    lines = max(1, min(2000, int(lines)))
    rc, out = _run(
        ["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "--output=short-iso"],
        timeout=10.0,
    )
    if rc != 0:
        return [f"(journalctl rc={rc})"]
    return out.splitlines()
