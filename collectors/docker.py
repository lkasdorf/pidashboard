"""Docker container inventory.

Reads from the local Docker daemon via the `docker` CLI. Requires the user
running pidashboard to be in the `docker` group; degrades to an empty list if
the binary is missing or the daemon is unreachable.
"""

from __future__ import annotations

import json
import subprocess


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
    out: list[dict] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append({
            "name": c.get("Names") or "",
            "image": c.get("Image") or "",
            "state": c.get("State") or "",
            "status": c.get("Status") or "",
            "ports": c.get("Ports") or "",
            "running_for": c.get("RunningFor") or "",
        })
    out.sort(key=lambda c: (c["state"] != "running", c["name"].lower()))
    return out
