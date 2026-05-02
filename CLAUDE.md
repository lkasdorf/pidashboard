# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run / develop

```bash
# Dev (Flask reloader off; sampler thread is incompatible with Werkzeug's reloader)
.venv/bin/python app.py --debug

# Production
sudo systemctl restart pidashboard.service   # required for any Python change
journalctl -u pidashboard.service -f         # tail server logs

# Health (rich JSON, HTTP 503 on critical)
curl -sf http://127.0.0.1:8081/healthz | jq
```

There is no build step (vanilla JS, no bundler) and no test suite — do not invent one. Static files and Jinja templates are picked up live via mtime; only Python edits need a service restart.

Sub-path mounting (e.g. behind `tailscale serve --set-path=/pi`) is controlled by `PIDASH_SCRIPT_NAME=/pi` — the `ScriptNamePrefix` WSGI middleware in `app.py` both sets `SCRIPT_NAME` (so `url_for()` emits prefixed URLs) and strips the prefix from `PATH_INFO` if the proxy didn't, so the same code works either way.

## Architecture

Three layers, one process:

1. **`collectors/`** — thin wrappers, mostly around `subprocess` calls to `systemctl`, `journalctl`, `vcgencmd`, `ip`, `tailscale`, `docker`, `ping`, plus `psutil`. Each module is independently importable, has no shared state, and degrades silently if its underlying tool is missing or returns non-zero (returns `[]` / empty dict / sentinel).
2. **`sampler.py`** — single daemon thread runs `_loop()` every `LIVE_INTERVAL_SEC` (2.5 s default), builds a snapshot from `system + services + cron + timers + docker + devices`, stores it as `_last_snapshot`, broadcasts JSON to every SSE subscriber, persists to history tables on the long cadence, tracks throttle/device transitions and emits events, and runs `alerts.evaluate_and_dispatch(snap)`. Slow consumers get their oldest broadcast message dropped rather than blocking the broadcaster.
3. **`app.py`** — Flask routes. `/stream` is the SSE endpoint (one thread per browser tab — Waitress is configured with `threads=8` to allow several open tabs plus polling endpoints simultaneously). `/api/snapshot` returns the cached `_last_snapshot` for one-shot requests. Network and maintenance data are *not* in the snapshot — they're polled on-demand via `/api/network` and `/api/maintenance` because they're more expensive and less time-critical.

`storage.py` holds **five SQLite tables**, all on the same connection:
- `samples` — high-res system slice (~30 s, 2 h retention) → drives live sparklines.
- `samples_long` — coarse system slice (60 s, 7 d) → drives the 2 h/7 d range toggle on Overview.
- `net_samples` — per-interface cumulative byte counters (60 s, 7 d) → drives the Network tab's bandwidth chart; frontend computes bps deltas.
- `proc_samples_long` — per-tracked-process CPU%/MEM% (60 s, 7 d) → drives the Tracked processes panel sparklines.
- `events` — append-only log of throttle transitions, alert fire/resolve, device up/down. Surfaced on Overview's Recent events panel and used as vertical-line overlays on sparklines (`drawSpark` accepts `events` + `tsArray` opts).

`alerts.py` is the top-level module that consumes `_last_snapshot` on every sampler tick, evaluates `config.ALERT_RULES` (sustain_sec + cooldown_sec semantics), records fired alerts as events, and dispatches via `config.ALERT_CHANNELS` (ntfy push or generic JSON webhook, urllib only — no extra deps). `ALERT_CHANNELS` defaults to empty so nothing is sent until the user adds an ntfy URL — the alerts pill in the topbar still surfaces firing alerts regardless. Supported metric paths: `cpu`/`memory`/`swap`/`disk_root`/`temp` (numeric ops), throttle bits like `undervoltage`/`throttled` (`is_true`), `device.<name>.ok` (`is_false`), and `pihole.<key>` for keys exposed by `collectors/pihole.py` (`pihole.dhcp_active` with `is_false`, `pihole.gravity_stale` with `is_true`). `pihole.*` returns `None` when the collector reports `available: False`, so rules don't fire on "unknown" — they fire only when we positively observe the bad state.

`/healthz` returns rich JSON: `status` ∈ {ok, degraded, critical, warming-up} plus a `checks` block. **`critical` (firing alert OR currently throttled/undervolted) is reported as HTTP 503** so external monitors (Uptime Kuma etc.) alarm without parsing JSON. `degraded` (disk >80 %, temp >70 °C, swap >50 %) stays HTTP 200.

## `config.py` is the central control panel

- `WATCHED_SERVICES` — units shown on the Services tab.
- `CONTROLLABLE_SERVICES` — units that get start/stop/restart buttons. **MUST stay in sync with the unit list in `deploy/pidashboard.sudoers`** — the sudoers rule is what actually permits passwordless control, and `pidashboard.service` is intentionally excluded (no self-stop footgun).
- `CRON_JOBS` — list of `{name, log, schedule}`. The dashboard never reads `crontab`; it derives "last run" from log file mtime in `CRON_LOG_DIR` and detects errors only on the *latest non-empty line* so historical errors don't keep flagging recovered jobs (`collectors/cron_jobs.py:_job_state`). Schedule humanisation in `_parse_schedule` only covers `*/N * * * *`, daily, and weekly cron forms — anything else falls back to the raw expression.
- `TRACKED_PROCESSES` — process names sampled into `proc_samples_long`. Match is "exe name OR cmdline substring" so Python services whose os-level name is just `python3` are still tracked.
- `WATCHED_DEVICES` — list of `{name, host, method, port?, timeout?, verify_tls?}` probed every 30 s by `collectors/devices.py` (own daemon, parallel probes via `ThreadPoolExecutor`). Methods: `ping` / `tcp` / `http` / `https`. `verify_tls` defaults to **False** because reachability is the goal, not authenticity — a NAS with a self-signed cert is still "up". Up↔down transitions emit `device.up.<name>` / `device.down.<name>` events.
- `ALERT_RULES` and `ALERT_CHANNELS` — see alerts.py docs above.

`collectors/pihole.py` reads `/etc/pihole/{pihole-FTL.db,dhcp.leases,pihole.toml,gravity.db}` directly (no HTTP API, no app-password). The dashboard process must be in the `pihole` group for the SQLite/file reads — without it, `gravity_age_sec` still works (only needs dir traversal + stat) but the other three fields are `None` and the panel shows an "unavailable" hint. Daemon thread, 30 s cadence, separate from sampler so a slow FTL DB never delays the live SSE loop.

## Frontend (`static/app.js`, `templates/index.html`)

Single page, four top-level tabs (Overview / Services / Network / Logs), tab state in `location.hash`. Snapshot updates arrive via `EventSource` and fan out through `applySnapshot()` to per-section render functions. The Logs tab has its own sub-tab nav (System / Services / Cron / Auth) — top-tab selectors are scoped with `.tabs:not(.subtabs) > .tab` so sub-tabs don't accidentally toggle top-tab state. Service and cron log dropdowns are populated once from the first snapshot via `populateLogDropdowns()` (guarded by `dataset.populated`).

Sparklines maintain parallel `historiesTs` / `histories7dTs` arrays so `drawSpark` can position event overlays correctly along the time axis. `recentEventsCache` is populated by `fetchEvents` (every 60 s) and passed into `drawSpark` so vertical lines coloured by severity (red/amber/cyan) appear on every sparkline that covers the event timestamp.

Network polling only runs while the Network tab is visible (`startNetworkPolling`/`stopNetworkPolling`). The favicon is redrawn whenever CPU% crosses a colour threshold. Compact mobile mode kicks in below 480 px and hides sparklines + secondary panels for a phone-glance view of the metric pills only.

## Environment expectations

- `systemd` + `journalctl` available; user in `adm` (or `systemd-journal`) group for cross-unit journal reads — without it `journal()` and the System log just return empty.
- `vcgencmd` (Pi-only) for throttle status; absence is handled gracefully.
- `docker` group membership for the user if Docker stats/logs should appear.
- The service whitelist <-> sudoers coupling is the single most common source of "button does nothing" bugs — always check both files when changing controllable units.
- `collectors/syslog.py` uses `journalctl -k` (not `dmesg`) for kernel-ring-buffer access, because `dmesg` is restricted to root by default on Pi OS / Ubuntu (`kernel.dmesg_restrict=1`) while journal access works via the `adm` group we already require.
- `collectors/devices.py` uses `/bin/ping` (suid) for the ping method — works without CAP_NET_RAW. TCP and HTTP probes use plain Python sockets, no privileges needed.
