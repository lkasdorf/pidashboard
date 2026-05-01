"""Flask entrypoint for pidashboard.

Routes:
    GET  /                              dashboard page
    GET  /healthz                       liveness probe
    GET  /api/snapshot                  current metrics (one-shot JSON)
    GET  /api/history                   2h history series for charts
    GET  /stream                        Server-Sent Events live stream
    POST /api/services/<unit>/<action>  start/stop/restart whitelisted unit
    GET  /api/cron/<job>/log            tail last N lines of a cron log
    GET  /api/docker/<name>/log         tail last N lines of a container log
"""

from __future__ import annotations

import argparse
import json
import os
import queue

from flask import Flask, Response, jsonify, render_template, request

import alerts
import config
import sampler
import storage
from collectors import auth as auth_coll
from collectors import cron_jobs as cron_coll
from collectors import docker as docker_coll
from collectors import maintenance as maint_coll
from collectors import network as net_coll
from collectors import services as svc_coll
from collectors import syslog as syslog_coll
from collectors import system as sys_coll

app = Flask(__name__, static_folder="static", template_folder="templates")


class ScriptNamePrefix:
    """WSGI middleware so the app can be mounted under a sub-path like /pi.

    Sets SCRIPT_NAME (so url_for() and request.script_root know the mount
    point) and strips the prefix from PATH_INFO when present — this way it
    works whether the upstream proxy strips the prefix before forwarding
    or passes it through verbatim.
    """

    def __init__(self, wsgi_app, script_name: str):
        self.wsgi_app = wsgi_app
        self.script_name = script_name.rstrip("/")

    def __call__(self, environ, start_response):
        if self.script_name:
            environ["SCRIPT_NAME"] = self.script_name
            path = environ.get("PATH_INFO", "")
            if path == self.script_name or path.startswith(self.script_name + "/"):
                environ["PATH_INFO"] = path[len(self.script_name):] or "/"
        return self.wsgi_app(environ, start_response)


_script_name = os.environ.get("PIDASH_SCRIPT_NAME", "").strip()
if _script_name:
    app.wsgi_app = ScriptNamePrefix(app.wsgi_app, _script_name)


@app.get("/")
def index():
    return render_template(
        "index.html",
        services=config.WATCHED_SERVICES,
        controllable_services=config.CONTROLLABLE_SERVICES,
        allowed_actions=config.ALLOWED_ACTIONS,
        cron_jobs=config.CRON_JOBS,
        live_interval_sec=config.LIVE_INTERVAL_SEC,
        history_retention_sec=config.HISTORY_RETENTION_SEC,
    )


@app.get("/healthz")
def healthz():
    return jsonify(status="ok")


@app.get("/api/snapshot")
def api_snapshot():
    snap = sampler.latest()
    if snap is None:
        return jsonify(error="warming up"), 503
    return jsonify(snap)


@app.get("/api/history")
def api_history():
    range_key = request.args.get("range", "2h")
    return jsonify(samples=storage.history(range_key))


@app.get("/api/events")
def api_events():
    try:
        limit = max(1, min(500, int(request.args.get("limit", 50))))
    except ValueError:
        limit = 50
    return jsonify(events=storage.recent_events(limit))


@app.get("/stream")
def stream():
    q = sampler.subscribe()

    def gen():
        try:
            snap = sampler.latest()
            if snap is not None:
                yield f"data: {json.dumps(snap)}\n\n"
            while True:
                try:
                    payload = q.get(timeout=15)
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            sampler.unsubscribe(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/services/<unit>/<action>")
def api_service_action(unit: str, action: str):
    ok, msg = svc_coll.control(unit, action)
    return jsonify(ok=ok, message=msg), (200 if ok else 400)


@app.get("/api/services/<unit>/log")
def api_service_log(unit: str):
    try:
        lines = max(1, min(2000, int(request.args.get("lines", 200))))
    except ValueError:
        lines = 200
    return jsonify(lines=svc_coll.journal(unit, lines))


@app.get("/api/cron/<job>/log")
def api_cron_log(job: str):
    try:
        lines = max(1, min(2000, int(request.args.get("lines", 200))))
    except ValueError:
        lines = 200
    return jsonify(lines=cron_coll.tail_log(job, lines))


@app.get("/api/docker/<name>/log")
def api_docker_log(name: str):
    try:
        lines = max(1, min(2000, int(request.args.get("lines", 200))))
    except ValueError:
        lines = 200
    return jsonify(lines=docker_coll.logs(name, lines))


@app.get("/api/network")
def api_network():
    return jsonify(
        host=net_coll.host(),
        interfaces=net_coll.interfaces(),
        reachability=net_coll.reachability(),
        peers=net_coll.tailscale_peers(),
        sockets=net_coll.listening_sockets(),
    )


@app.get("/api/maintenance")
def api_maintenance():
    return jsonify(maint_coll.status())


@app.get("/api/auth/summary")
def api_auth_summary():
    return jsonify(auth_coll.summary())


@app.get("/api/alerts")
def api_alerts():
    return jsonify(alerts=alerts.status())


@app.get("/api/net_history")
def api_net_history():
    iface = request.args.get("iface", "")
    if not iface:
        return jsonify(ifaces=storage.net_ifaces_with_history(), samples=[])
    return jsonify(iface=iface, samples=storage.net_history(iface))


@app.get("/api/process/<int:pid>")
def api_process(pid: int):
    info = sys_coll.process_info(pid)
    if info is None:
        return jsonify(error="not found"), 404
    return jsonify(info)


@app.get("/api/system/log")
def api_system_log():
    priority = request.args.get("priority", "warning")
    source = request.args.get("source", "journal")
    try:
        lines = max(1, min(2000, int(request.args.get("lines", 100))))
    except ValueError:
        lines = 100
    return jsonify(lines=syslog_coll.tail(priority, lines, source))


def main() -> None:
    parser = argparse.ArgumentParser(description="pidashboard server")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    storage.init()
    sampler.start()
    maint_coll.start()

    if args.debug:
        app.run(host=args.host, port=args.port, debug=True, threaded=True, use_reloader=False)
        return

    # Each long-lived SSE consumer occupies one thread; bump default 4 to give
    # headroom for concurrent browser tabs plus the periodic polling endpoints.
    from waitress import serve
    serve(app, host=args.host, port=args.port, threads=8, channel_timeout=300, ident="pidashboard")


if __name__ == "__main__":
    main()
