# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run / develop

```bash
# Dev (Flask reloader off; sampler thread is incompatible with Werkzeug's reloader)
.venv/bin/python app.py --debug

# Production
sudo systemctl restart pidashboard.service   # required for any Python change
journalctl -u pidashboard.service -f         # tail server logs

# Health
curl -sf http://127.0.0.1:8081/healthz
```

There is no build step (vanilla JS, no bundler) and no test suite — do not invent one. Static files and Jinja templates are picked up live via mtime; only Python edits need a service restart.

Sub-path mounting (e.g. behind `tailscale serve --set-path=/pi`) is controlled by `PIDASH_SCRIPT_NAME=/pi` — the `ScriptNamePrefix` WSGI middleware in `app.py` both sets `SCRIPT_NAME` (so `url_for()` emits prefixed URLs) and strips the prefix from `PATH_INFO` if the proxy didn't, so the same code works either way.

## Architecture

Three layers, one process:

1. **`collectors/`** — thin wrappers, mostly around `subprocess` calls to `systemctl`, `journalctl`, `vcgencmd`, `ip`, `tailscale`, `docker`. Each module is independently importable, has no shared state, and degrades silently if its underlying tool is missing or returns non-zero (returns `[]` / empty dict / sentinel).
2. **`sampler.py`** — a single daemon thread runs `_loop()` every `LIVE_INTERVAL_SEC` (2.5 s default), builds a snapshot from `system + services + cron + docker` collectors, stores it as `_last_snapshot`, and broadcasts JSON to every SSE subscriber's `queue.Queue`. Slow consumers get their oldest message dropped rather than blocking the broadcaster. Every `HISTORY_INTERVAL_SEC` (30 s) the system slice is also persisted to SQLite.
3. **`app.py`** — Flask routes. `/stream` is the SSE endpoint (one thread per browser tab — Waitress is configured with `threads=8` to allow several open tabs plus polling endpoints simultaneously). `/api/snapshot` returns the cached `_last_snapshot` for one-shot requests. Network and maintenance data are *not* in the snapshot — they're polled on-demand via `/api/network` and `/api/maintenance` because they're more expensive and less time-critical.

`storage.py` is a SQLite ringbuffer holding only the **system metrics slice** (CPU/mem/swap/disk/temp/load) for `HISTORY_RETENTION_SEC` (2 h) — used for sparklines on the Overview tab. Services, cron, docker are never persisted.

## `config.py` is the central control panel

- `WATCHED_SERVICES` — units shown on the Services tab.
- `CONTROLLABLE_SERVICES` — units that get start/stop/restart buttons. **MUST stay in sync with the unit list in `deploy/pidashboard.sudoers`** — the sudoers rule is what actually permits passwordless control, and `pidashboard.service` is intentionally excluded (no self-stop footgun).
- `CRON_JOBS` — list of `{name, log, schedule}`. The dashboard never reads `crontab`; it derives "last run" from log file mtime in `CRON_LOG_DIR` and detects errors only on the *latest non-empty line* so historical errors don't keep flagging recovered jobs (`collectors/cron_jobs.py:_job_state`). Schedule humanisation in `_parse_schedule` only covers `*/N * * * *`, daily, and weekly cron forms — anything else falls back to the raw expression.

## Frontend (`static/app.js`, `templates/index.html`)

Single page, four top-level tabs (Overview / Services / Network / Logs), tab state in `location.hash`. Snapshot updates arrive via `EventSource` and fan out through `applySnapshot()` to per-section render functions. The Logs tab has its own sub-tab nav (System / Services / Cron) — top-tab selectors are scoped with `.tabs:not(.subtabs) > .tab` so sub-tabs don't accidentally toggle top-tab state. Service and cron log dropdowns are populated once from the first snapshot via `populateLogDropdowns()` (guarded by `dataset.populated`).

Network polling only runs while the Network tab is visible (`startNetworkPolling`/`stopNetworkPolling`). The favicon is redrawn whenever CPU% crosses a colour threshold.

## Environment expectations

- `systemd` + `journalctl` available; user in `adm` (or `systemd-journal`) group for cross-unit journal reads — without it `journal()` and the System log just return empty.
- `vcgencmd` (Pi-only) for throttle status; absence is handled gracefully.
- The service whitelist <-> sudoers coupling is the single most common source of "button does nothing" bugs — always check both files when changing controllable units.
- `collectors/syslog.py` uses `journalctl -k` (not `dmesg`) for kernel-ring-buffer access, because `dmesg` is restricted to root by default on Pi OS / Ubuntu (`kernel.dmesg_restrict=1`) while journal access works via the `adm` group we already require.
