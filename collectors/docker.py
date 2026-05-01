"""Docker container inventory.

Reads from the local Docker daemon via the `docker` CLI. Requires the user
running pidashboard to be in the `docker` group; degrades to an empty list if
the binary is missing or the daemon is unreachable.
"""

from __future__ import annotations

import json
import subprocess


def _stats_by_name() -> dict[str, dict]:
    """`docker stats --no-stream` snapshot keyed by container name.

    Empty if the daemon is unreachable. CPUPerc/MemPerc come back as strings
    like "0.40%" / "0.37%"; we keep them as strings for direct rendering.
    """
    try:
        cp = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    if cp.returncode != 0:
        return {}
    out: dict[str, dict] = {}
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            s = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = s.get("Name") or ""
        if not name:
            continue
        out[name] = {
            "cpu":     s.get("CPUPerc") or "",
            "mem":     s.get("MemPerc") or "",
            "mem_use": s.get("MemUsage") or "",
            "net_io":  s.get("NetIO") or "",
            "blk_io":  s.get("BlockIO") or "",
        }
    return out


def containers() -> list[dict]:
    try:
        cp = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=5.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return []
    stats = _stats_by_name()
    out: list[dict] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        name = c.get("Names") or ""
        st = stats.get(name) or {}
        out.append({
            "name": name,
            "image": c.get("Image") or "",
            "state": c.get("State") or "",
            "status": c.get("Status") or "",
            "ports": c.get("Ports") or "",
            "running_for": c.get("RunningFor") or "",
            "stats": st,
        })
    out.sort(key=lambda c: (c["state"] != "running", c["name"].lower()))
    return out


def logs(name: str, lines: int = 200) -> list[str]:
    """Tail the last N lines of a container's logs. Returns empty if the
    container is unknown or docker isn't reachable."""
    lines = max(1, min(2000, int(lines)))
    try:
        cp = subprocess.run(
            ["docker", "logs", "--tail", str(lines), "--timestamps", name],
            capture_output=True, text=True, timeout=10.0, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if cp.returncode != 0:
        return []
    # docker writes both stdout and stderr; merge in time order is not preserved,
    # so we just concatenate stderr after stdout (good enough for a tail view).
    parts: list[str] = []
    if cp.stdout:
        parts.extend(cp.stdout.splitlines())
    if cp.stderr:
        parts.extend(cp.stderr.splitlines())
    return parts[-lines:]
