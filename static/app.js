"use strict";

const BASE = (window.PIDASH && window.PIDASH.base) || "";

const conn = document.getElementById("conn");
const updated = document.getElementById("updated");

// ───────── formatters ─────────
const fmtBytes = (n) => {
  if (n == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
};

const fmtRate = (bps) => {
  if (bps == null || !isFinite(bps)) return "–";
  return `${fmtBytes(bps)}/s`;
};

const fmtUptime = (s) => {
  if (s == null) return "–";
  s = Math.floor(s);
  const d = Math.floor(s / 86400); s -= d * 86400;
  const h = Math.floor(s / 3600); s -= h * 3600;
  const m = Math.floor(s / 60);
  const parts = [];
  if (d) parts.push(`${d}d`);
  if (d || h) parts.push(`${h}h`);
  parts.push(`${m}m`);
  return parts.join(" ");
};

const fmtRelative = (ts) => {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}s`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
  return `${Math.floor(diff / 86400)}d`;
};

const fmtFromNow = (ts) => {
  if (!ts) return "–";
  const diff = ts - Date.now() / 1000;
  if (diff <= 0) return "now";
  if (diff < 60) return `in ${Math.floor(diff)}s`;
  if (diff < 3600) return `in ${Math.floor(diff / 60)}m`;
  if (diff < 86400) return `in ${Math.floor(diff / 3600)}h`;
  return `in ${Math.floor(diff / 86400)}d`;
};

const escapeHtml = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const classForPercent = (p, warn = 75, bad = 90) => {
  if (p == null) return "";
  if (p >= bad) return "bad";
  if (p >= warn) return "warn";
  return "";
};
const classForTemp = (t) => {
  if (t == null) return "";
  if (t >= 80) return "bad";
  if (t >= 70) return "warn";
  return "";
};

// ───────── sparklines ─────────
const histories = { cpu: [], mem: [], temp: [] };
const histories7d = { cpu: [], mem: [], temp: [] };
const MAX_POINTS = 360;
let currentRange = "live";  // "live" (2h, snapshot-fed) or "7d" (one-shot fetch)

function pushHist(key, v) {
  const arr = histories[key];
  if (v == null) return;
  arr.push(v);
  if (arr.length > MAX_POINTS) arr.shift();
}

function drawSpark(canvas, data, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 200;
  const cssH = canvas.clientHeight || 50;
  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr;
    canvas.height = cssH * dpr;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  if (data.length < 2) return;

  const min = opts.min ?? Math.min(...data);
  const max = opts.max ?? Math.max(...data);
  const span = max - min || 1;
  const step = cssW / (data.length - 1);

  ctx.lineWidth = 1.5;
  ctx.strokeStyle = opts.color || "#4cc9f0";
  ctx.fillStyle = (opts.color || "#4cc9f0") + "22";
  ctx.beginPath();
  data.forEach((v, i) => {
    const x = i * step;
    const y = cssH - ((v - min) / span) * (cssH - 4) - 2;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.lineTo(cssW, cssH);
  ctx.lineTo(0, cssH);
  ctx.closePath();
  ctx.fill();
}

function refreshSparks() {
  const source = currentRange === "7d" ? histories7d : histories;
  document.querySelectorAll("canvas.spark").forEach((c) => {
    const key = c.dataset.key;
    const data = source[key] || [];
    const opts = key === "temp"
      ? { color: "#fbbf24", min: 30, max: 90 }
      : { color: "#4cc9f0", min: 0, max: 100 };
    drawSpark(c, data, opts);
  });
}

async function loadRange(range) {
  const cache = range === "7d" ? histories7d : histories;
  for (const k of Object.keys(cache)) cache[k].length = 0;
  try {
    const r = await fetch(`${BASE}/api/history?range=${encodeURIComponent(range === "7d" ? "7d" : "2h")}`);
    const data = await r.json();
    for (const s of data.samples || []) {
      if (s.cpu != null) cache.cpu.push(s.cpu);
      if (s.mem != null) cache.mem.push(s.mem);
      if (s.temp != null) cache.temp.push(s.temp);
    }
  } catch (_) {}
}

async function setRange(range) {
  if (range !== "live" && range !== "7d") return;
  currentRange = range;
  document.querySelectorAll(".range-btn").forEach((b) => {
    b.setAttribute("aria-pressed", b.dataset.range === range ? "true" : "false");
  });
  if (range === "7d") await loadRange("7d");
  refreshSparks();
}

document.querySelectorAll(".range-btn").forEach((b) => {
  b.addEventListener("click", () => setRange(b.dataset.range));
});

function setMetric(id, text, cls = "") {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = "value" + (cls ? " " + cls : "");
}

// ───────── overview snapshot ─────────
function renderCores(perCore) {
  const root = document.getElementById("cpu-cores");
  if (!root || !Array.isArray(perCore)) return;
  // Keep DOM elements stable across renders so the height transition can interpolate.
  if (root.children.length !== perCore.length) {
    root.innerHTML = perCore.map(() => '<div class="core-bar"><span></span></div>').join("");
  }
  perCore.forEach((p, i) => {
    const bar = root.children[i];
    if (!bar) return;
    const cls = p >= 90 ? "core-bar bad" : p >= 70 ? "core-bar warn" : "core-bar";
    if (bar.className !== cls) bar.className = cls;
    bar.firstElementChild.style.height = `${Math.max(2, Math.min(100, p)).toFixed(1)}%`;
    bar.title = `Core ${i}: ${p.toFixed(0)}%`;
  });
}

function applySnapshot(snap) {
  const sys = snap.system;
  setMetric("cpu-val", `${sys.cpu.percent.toFixed(0)}%`, classForPercent(sys.cpu.percent));
  document.getElementById("cpu-foot").textContent =
    `load: ${sys.cpu.load1.toFixed(2)} / ${sys.cpu.load5.toFixed(2)} / ${sys.cpu.load15.toFixed(2)}`;
  pushHist("cpu", sys.cpu.percent);
  renderCores(sys.cpu.per_core);

  setMetric("mem-val", `${sys.memory.percent.toFixed(0)}%`, classForPercent(sys.memory.percent));
  document.getElementById("mem-foot").textContent =
    `${fmtBytes(sys.memory.used)} / ${fmtBytes(sys.memory.total)}`;
  pushHist("mem", sys.memory.percent);

  if (sys.temp_c != null) {
    setMetric("temp-val", `${sys.temp_c.toFixed(1)} °C`, classForTemp(sys.temp_c));
    pushHist("temp", sys.temp_c);
  } else {
    setMetric("temp-val", "n/a");
  }

  renderThrottle(sys.throttle);
  renderDisks(sys.disks || []);
  renderStorage(sys.storage_health || []);

  setMetric("uptime-val", fmtUptime(sys.uptime_sec));
  document.getElementById("swap-foot").textContent =
    `swap: ${sys.swap.percent.toFixed(0)}% (${fmtBytes(sys.swap.used)} / ${fmtBytes(sys.swap.total)})`;

  refreshSparks();
  renderServices(snap.services);
  renderCron(snap.cron);
  renderTimers(snap.timers || []);
  renderDocker(snap.docker || []);
  renderProcs(sys.top_processes);
  renderTrackedProcs(sys.tracked_processes || []);
  populateLogDropdowns(snap);
  updateFavicon(sys.cpu.percent);

  conn.textContent = "live";
  conn.className = "conn on";
  updated.textContent = `updated ${new Date().toLocaleTimeString("en-GB")}`;
  document.title = `${sys.cpu.percent.toFixed(0)}% · pidashboard`;
}

function dotForActive(active) {
  if (active === "active") return '<span class="dot good"></span>';
  if (active === "activating" || active === "reloading") return '<span class="dot warn"></span>';
  if (active === "failed") return '<span class="dot bad"></span>';
  return '<span class="dot muted"></span>';
}

function renderServices(items) {
  const tbody = document.querySelector("#services-table tbody");
  const controllable = new Set(window.PIDASH.controllable);
  tbody.innerHTML = items.map((s) => {
    let sinceCell = "–";
    if (s.active_since_ts) {
      const original = (s.active_since || "").replace(/^[A-Za-z]+ /, "");
      sinceCell = `<span title="${escapeHtml(original)}">${fmtRelative(s.active_since_ts)}</span>`;
    } else if (s.active_since && s.active_since !== "n/a") {
      sinceCell = escapeHtml(s.active_since.replace(/^[A-Za-z]+ /, ""));
    }
    const ctrlButtons = controllable.has(s.unit)
      ? window.PIDASH.actions.map((a) =>
          `<button class="act${a === "stop" ? " danger" : ""}" data-unit="${escapeHtml(s.unit)}" data-action="${a}">${a}</button>`
        ).join("")
      : "";
    const logButton = `<button class="show-svc-log" data-unit="${escapeHtml(s.unit)}">log</button>`;
    return `<tr>
      <td>${dotForActive(s.active)}<code>${escapeHtml(s.unit)}</code></td>
      <td>${escapeHtml(s.active)}</td>
      <td>${escapeHtml(s.sub_state || "–")}</td>
      <td>${s.main_pid && s.main_pid !== "0" ? `<button class="pid-link" data-pid="${escapeHtml(s.main_pid)}">${escapeHtml(s.main_pid)}</button>` : "–"}</td>
      <td>${sinceCell}</td>
      <td>${ctrlButtons}${ctrlButtons ? " " : ""}${logButton}</td>
    </tr>`;
  }).join("");
}

function renderCron(items) {
  const tbody = document.querySelector("#cron-table tbody");
  const now = Date.now() / 1000;
  tbody.innerHTML = items.map((j) => {
    let dot = '<span class="dot muted"></span>';
    let statusText = "no log";
    let statusTitle = "";
    if (j.last_run) {
      const ageSec = now - j.last_run;
      const stale = j.interval_sec && ageSec > 2 * j.interval_sec;
      if (j.has_error) {
        dot = '<span class="dot bad"></span>';
        statusText = "error in log";
        statusTitle = j.last_error_line || "";
      } else if (stale) {
        dot = '<span class="dot muted"></span>';
        statusText = `silent (since ${fmtRelative(j.last_run)})`;
        statusTitle = `Schedule ${j.schedule_human}, no output — job likely only logs on activity`;
      } else {
        dot = '<span class="dot good"></span>';
        statusText = "ok";
      }
    }
    const scheduleCell = j.schedule_human !== j.schedule
      ? `<span title="${escapeHtml(j.schedule)}">${escapeHtml(j.schedule_human)}</span>`
      : `<code>${escapeHtml(j.schedule)}</code>`;
    const nextSuffix = j.next_run ? `<br><span class="muted">next ${fmtFromNow(j.next_run)}</span>` : "";
    return `<tr>
      <td><code>${escapeHtml(j.name)}</code></td>
      <td>${scheduleCell}</td>
      <td>${fmtRelative(j.last_run)}${nextSuffix}</td>
      <td title="${escapeHtml(statusTitle)}">${dot}${escapeHtml(statusText)}</td>
      <td><button class="show-log" data-job="${escapeHtml(j.name)}">open</button></td>
    </tr>`;
  }).join("");
}

function renderProcs(items) {
  const tbody = document.querySelector("#proc-table tbody");
  tbody.innerHTML = (items || []).map((p) =>
    `<tr><td><button class="pid-link" data-pid="${p.pid}">${p.pid}</button></td><td><code>${escapeHtml(p.name)}</code></td><td>${p.cpu.toFixed(1)}</td><td>${p.mem.toFixed(1)}</td></tr>`
  ).join("");
}

function renderDocker(items) {
  const tbody = document.querySelector("#docker-table tbody");
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="muted">no containers (or docker unreachable)</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((c) => {
    const dot = c.state === "running"
      ? '<span class="dot good"></span>'
      : c.state === "exited" || c.state === "dead"
        ? '<span class="dot bad"></span>'
        : '<span class="dot muted"></span>';
    const stats = c.stats || {};
    const memCell = stats.mem
      ? `<span title="${escapeHtml(stats.mem_use || "")}">${escapeHtml(stats.mem)}</span>`
      : "–";
    return `<tr>
      <td>${dot}<code>${escapeHtml(c.name)}</code></td>
      <td><code>${escapeHtml(c.image)}</code></td>
      <td>${escapeHtml(c.state)}</td>
      <td>${escapeHtml(stats.cpu || "–")}</td>
      <td>${memCell}</td>
      <td>${escapeHtml(c.status)}</td>
      <td><code>${escapeHtml(c.ports || "–")}</code></td>
      <td><button class="show-docker-log" data-name="${escapeHtml(c.name)}">open</button></td>
    </tr>`;
  }).join("");
}

function renderTimers(items) {
  const tbody = document.querySelector("#timers-table tbody");
  if (!tbody) return;
  if (!items.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted">no timers</td></tr>';
    return;
  }
  tbody.innerHTML = items.map((t) => {
    const last = t.last ? fmtRelative(t.last) : '<span class="muted">–</span>';
    const next = t.next
      ? `<span title="${escapeHtml(new Date(t.next * 1000).toLocaleString("en-GB"))}">${fmtFromNow(t.next)}</span>`
      : '<span class="muted">inactive</span>';
    return `<tr>
      <td><code>${escapeHtml(t.unit)}</code></td>
      <td><code>${escapeHtml(t.activates)}</code></td>
      <td>${last}</td>
      <td>${next}</td>
    </tr>`;
  }).join("");
}

const THROTTLE_LABELS = {
  undervoltage: "Undervoltage",
  arm_freq_capped: "ARM freq capped",
  throttled: "Throttled",
  soft_temp_limit: "Temp limit",
};

function renderThrottle(t) {
  const valEl = document.getElementById("throttle-val");
  const footEl = document.getElementById("throttle-foot");
  if (!t) { valEl.textContent = "n/a"; valEl.className = "value small"; footEl.textContent = "vcgencmd unavailable"; return; }
  const nowFlags = Object.entries(t.now).filter(([_, v]) => v).map(([k]) => THROTTLE_LABELS[k]);
  const bootFlags = Object.entries(t.since_boot).filter(([_, v]) => v).map(([k]) => THROTTLE_LABELS[k]);
  if (nowFlags.length) {
    valEl.textContent = nowFlags.join(", ");
    valEl.className = "value small bad";
  } else if (bootFlags.length) {
    valEl.textContent = "OK";
    valEl.className = "value warn";
  } else {
    valEl.textContent = "OK";
    valEl.className = "value good";
  }
  footEl.textContent = bootFlags.length
    ? `since boot: ${bootFlags.join(", ")}`
    : "since boot: clean";
}

function renderStorage(items) {
  const valEl = document.getElementById("storage-val");
  const footEl = document.getElementById("storage-foot");
  if (!valEl) return;
  if (!items.length) {
    valEl.textContent = "n/a";
    valEl.className = "value small";
    footEl.textContent = "no eMMC/SD/NVMe found";
    return;
  }
  // Show worst-of in the value cell, list-of devices in the foot.
  const wear = items.map((d) => d.life_used_pct).filter((v) => v != null);
  const worstWear = wear.length ? Math.max(...wear) : null;
  const eolBad = items.some((d) => d.pre_eol === "warning" || d.pre_eol === "urgent");
  if (worstWear != null) {
    valEl.textContent = `${worstWear}% used`;
    valEl.className = "value small" + (worstWear >= 80 || eolBad ? " bad" : worstWear >= 50 ? " warn" : " good");
  } else {
    valEl.textContent = "no wear data";
    valEl.className = "value small";
  }
  footEl.innerHTML = items.map((d) => {
    const parts = [`<code>${escapeHtml(d.device)}</code>`];
    if (d.model) parts.push(escapeHtml(d.model));
    if (d.life_used_pct != null) parts.push(`life ${d.life_used_pct}%`);
    if (d.pre_eol) parts.push(`EOL: ${escapeHtml(d.pre_eol)}`);
    if (d.bytes_written_since_boot != null) parts.push(`${fmtBytes(d.bytes_written_since_boot)} since boot`);
    return parts.join(" · ");
  }).join("<br>");
}

function renderDisks(disks) {
  const root = document.getElementById("disks-list");
  if (!disks.length) { root.innerHTML = '<div class="muted">no mounts</div>'; return; }
  root.innerHTML = disks.map((d) => {
    const cls = d.percent >= 90 ? " bad" : d.percent >= 75 ? " warn" : "";
    return `<div class="disk-row">
      <div class="disk-mount">
        <code>${escapeHtml(d.mount)}</code>
        <span class="muted">${escapeHtml(d.fstype)} · ${escapeHtml(d.device)}</span>
      </div>
      <div class="disk-numbers">${fmtBytes(d.used)} / ${fmtBytes(d.total)} <span class="muted">(${d.percent.toFixed(0)}%)</span></div>
      <div class="disk-bar${cls}"><span style="width: ${Math.max(0, Math.min(100, d.percent)).toFixed(1)}%"></span></div>
    </div>`;
  }).join("");
}

// ───────── maintenance pill ─────────
const maintPill = document.getElementById("maint-pill");
const alertsPill = document.getElementById("alerts-pill");
let maintTimer = null;

async function fetchAlerts() {
  if (!alertsPill) return;
  try {
    const r = await fetch(`${BASE}/api/alerts`);
    if (!r.ok) return;
    const d = await r.json();
    const firing = (d.alerts || []).filter((a) => a.firing);
    if (!firing.length) {
      alertsPill.hidden = true;
      return;
    }
    alertsPill.hidden = false;
    alertsPill.textContent = firing.length === 1
      ? `Alert: ${firing[0].id}`
      : `${firing.length} alerts firing`;
    alertsPill.className = "pill bad";
    alertsPill.title = firing.map((a) => `${a.id}: ${a.metric} ${a.op} ${a.threshold ?? ""} (now ${a.last_value})`).join("\n");
  } catch (_) {}
}

async function fetchMaintenance() {
  try {
    const r = await fetch(`${BASE}/api/maintenance`);
    if (!r.ok) return;
    renderMaintenance(await r.json());
  } catch (_) {}
}

function renderMaintenance(m) {
  if (!maintPill) return;
  const reboot = m.reboot && m.reboot.required;
  const updates = (m.updates && m.updates.count) || 0;
  const checkedAt = m.updates && m.updates.checked_at;
  const ageStr = checkedAt
    ? `checked ${fmtRelative(checkedAt)} ago`
    : "checking …";

  const parts = [];
  if (reboot) parts.push("Reboot needed");
  if (updates > 0) parts.push(`${updates} update${updates === 1 ? "" : "s"}`);

  let cls;
  if (reboot) cls = "bad";
  else if (updates > 0) cls = "warn";
  else { cls = "muted"; parts.push("system clean"); }
  parts.push(ageStr);

  maintPill.hidden = false;
  maintPill.textContent = parts.join(" · ");
  maintPill.className = "pill " + cls;
  const pkgs = (m.reboot && m.reboot.packages) || [];
  maintPill.title = pkgs.length
    ? `Reboot due to: ${pkgs.join(", ")}`
    : (updates > 0 ? "apt list --upgradable reported packages" : "apt list --upgradable refreshed hourly");
}

// ───────── service controls ─────────
document.querySelector("#services-table").addEventListener("click", async (ev) => {
  const actBtn = ev.target.closest("button.act");
  const logBtn = ev.target.closest("button.show-svc-log");
  if (actBtn) {
    const unit = actBtn.dataset.unit;
    const action = actBtn.dataset.action;
    if (!confirm(`${action} ${unit}?`)) return;
    actBtn.disabled = true;
    try {
      const r = await fetch(`${BASE}/api/services/${encodeURIComponent(unit)}/${encodeURIComponent(action)}`, { method: "POST" });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.ok) alert(`Error: ${data.message || r.status}`);
    } catch (e) {
      alert(`Network error: ${e}`);
    } finally {
      actBtn.disabled = false;
    }
  } else if (logBtn) {
    openLogModal(`${logBtn.dataset.unit} (journalctl, last 200 lines)`,
                 `${BASE}/api/services/${encodeURIComponent(logBtn.dataset.unit)}/log?lines=200`);
  }
});

// ───────── cron log modal ─────────
const modal = document.getElementById("log-modal");
const logTitle = document.getElementById("log-title");
const logBody = document.getElementById("log-body");
document.getElementById("log-close").addEventListener("click", () => modal.close());

async function openLogModal(title, url) {
  logTitle.textContent = title;
  logBody.textContent = "loading …";
  modal.showModal();
  try {
    const r = await fetch(url);
    const data = await r.json();
    logBody.textContent = (data.lines || []).join("\n") || "(empty)";
    logBody.scrollTop = logBody.scrollHeight;
  } catch (e) {
    logBody.textContent = `Error: ${e}`;
  }
}

async function openProcessModal(pid) {
  logTitle.textContent = `pid ${pid}`;
  logBody.textContent = "loading …";
  modal.showModal();
  try {
    const r = await fetch(`${BASE}/api/process/${pid}`);
    if (r.status === 404) {
      logBody.textContent = `pid ${pid}: process not found (already exited?)`;
      return;
    }
    const d = await r.json();
    const fmt = (k, v) => v == null ? "" : `${k.padEnd(14)} ${v}\n`;
    const created = d.create_time ? new Date(d.create_time * 1000).toISOString().replace("T", " ").slice(0, 19) : null;
    const ageStr = d.create_time ? `(${fmtRelative(d.create_time)} ago)` : "";
    logBody.textContent =
      fmt("pid", d.pid) +
      fmt("name", d.name) +
      fmt("status", d.status) +
      fmt("user", d.username) +
      fmt("started", created ? `${created} ${ageStr}` : null) +
      fmt("threads", d.num_threads) +
      fmt("cpu %", d.cpu_percent != null ? d.cpu_percent.toFixed(1) : null) +
      fmt("mem %", d.memory_percent != null ? d.memory_percent.toFixed(1) : null) +
      fmt("exe", d.exe) +
      fmt("cwd", d.cwd) +
      "\ncmdline:\n" + (d.cmdline || "(unavailable)");
    logBody.scrollTop = 0;
  } catch (e) {
    logBody.textContent = `Error: ${e}`;
  }
}

document.body.addEventListener("click", (ev) => {
  const pidBtn = ev.target.closest("button.pid-link");
  if (!pidBtn) return;
  ev.stopPropagation();
  openProcessModal(pidBtn.dataset.pid);
});

document.querySelector("#cron-table").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button.show-log");
  if (!btn) return;
  const job = btn.dataset.job;
  openLogModal(`${job} (last 200 lines)`, `${BASE}/api/cron/${encodeURIComponent(job)}/log?lines=200`);
});

document.querySelector("#docker-table").addEventListener("click", (ev) => {
  const btn = ev.target.closest("button.show-docker-log");
  if (!btn) return;
  const name = btn.dataset.name;
  openLogModal(`${name} (last 200 lines)`, `${BASE}/api/docker/${encodeURIComponent(name)}/log?lines=200`);
});

// ───────── tabs ─────────
const TABS = ["overview", "services", "network", "logs"];

function activateTab(name) {
  if (!TABS.includes(name)) name = "overview";
  document.querySelectorAll(".tabs:not(.subtabs) > .tab").forEach((b) => {
    const on = b.dataset.tab === name;
    b.setAttribute("aria-selected", on ? "true" : "false");
  });
  document.querySelectorAll(".tab-panel").forEach((p) => {
    p.hidden = p.dataset.panel !== name;
  });
  if (name === "network") startNetworkPolling(); else stopNetworkPolling();
  if (name === "overview") { refreshSparks(); fetchEvents(); }
  if (name === "logs") fetchActiveSubtab();
}

document.querySelectorAll(".tabs:not(.subtabs) > .tab").forEach((b) => {
  b.addEventListener("click", () => {
    const name = b.dataset.tab;
    history.replaceState(null, "", `#${name}`);
    activateTab(name);
  });
});
window.addEventListener("hashchange", () => {
  activateTab((location.hash || "#overview").slice(1));
});

// ───────── network tab ─────────
let netTimer = null;
let lastIfaceSample = null; // { ts, byName: {name: {rx, tx}} }

async function fetchNetwork() {
  try {
    const r = await fetch(`${BASE}/api/network`);
    if (!r.ok) return;
    const d = await r.json();
    renderHost(d.host);
    renderTailscaleSelf(d.host && d.host.tailscale);
    renderInterfaces(d.interfaces);
    renderReachability(d.reachability);
    renderPeers(d.peers || []);
    renderSockets(d.sockets);
  } catch (_) {}
}

function startNetworkPolling() {
  if (netTimer) return;
  fetchNetwork();
  fetchBandwidthIfaces();
  netTimer = setInterval(fetchNetwork, 5000);
}
function stopNetworkPolling() {
  if (netTimer) { clearInterval(netTimer); netTimer = null; }
}

function renderHost(h) {
  if (!h) return;
  const ts = h.tailscale;
  const fqdnRow = (h.fqdn && h.fqdn !== h.hostname)
    ? `<dt>FQDN</dt><dd><code>${escapeHtml(h.fqdn)}</code></dd>` : "";
  const tsRow = ts ? `
    <dt>Tailscale</dt>
    <dd class="stack">
      <span><code>${escapeHtml(ts.dns_name || "")}</code> <span class="muted">(${escapeHtml(ts.backend_state || "?")}${ts.online ? ", online" : ""})</span></span>
      ${(ts.ips || []).map((ip) => `<code>${escapeHtml(ip)}</code>`).join("")}
    </dd>` : "";
  document.getElementById("net-host").innerHTML = `
    <dt>Hostname</dt><dd><code>${escapeHtml(h.hostname || "–")}</code></dd>
    ${fqdnRow}
    <dt>Model</dt><dd>${escapeHtml(h.model || "–")}</dd>
    <dt>OS</dt><dd>${escapeHtml(h.os || "–")} <span class="muted">(${escapeHtml(h.kernel || "")} · ${escapeHtml(h.arch || "")})</span></dd>
    ${tsRow}
  `;
}

function renderInterfaces(ifaces) {
  const tbody = document.querySelector("#net-interfaces tbody");
  const now = Date.now() / 1000;
  const prev = lastIfaceSample;
  const byName = {};
  for (const i of ifaces) byName[i.name] = { rx: i.rx_bytes, tx: i.tx_bytes };

  tbody.innerHTML = ifaces.map((i) => {
    const stateDot = i.up ? '<span class="dot good"></span>' : '<span class="dot muted"></span>';
    const stateText = i.up ? "up" : "down";
    let bw = "–";
    if (prev && prev.byName[i.name]) {
      const dt = now - prev.ts;
      if (dt > 0.1) {
        const drx = (i.rx_bytes - prev.byName[i.name].rx) / dt;
        const dtx = (i.tx_bytes - prev.byName[i.name].tx) / dt;
        if (drx >= 0 && dtx >= 0) {
          bw = `↓ ${fmtRate(drx)} <span class="muted">·</span> ↑ ${fmtRate(dtx)}`;
        }
      }
    }
    const wifi = i.wireless
      ? ` <span class="muted">(${i.wireless.signal_dbm.toFixed(0)} dBm${i.wireless.ssid ? ", " + escapeHtml(i.wireless.ssid) : ""})</span>` : "";
    return `<tr>
      <td>${stateDot}<code>${escapeHtml(i.name)}</code>${wifi}</td>
      <td>${stateText}${i.speed_mbps ? ` <span class="muted">${i.speed_mbps} Mbit/s</span>` : ""}</td>
      <td><code>${escapeHtml(i.ipv4 || "–")}</code></td>
      <td><code>${escapeHtml(i.mac || "–")}</code></td>
      <td>${fmtBytes(i.rx_bytes)}</td>
      <td>${fmtBytes(i.tx_bytes)}</td>
      <td>${bw}</td>
    </tr>`;
  }).join("");

  lastIfaceSample = { ts: now, byName };
}

function renderReachability(r) {
  if (!r) return;
  const routes = (r.default_routes || []).map((rt) =>
    `<span><code>${escapeHtml(rt.gateway || "?")}</code> via <code>${escapeHtml(rt.iface || "?")}</code>${rt.metric != null ? ` <span class="muted">metric ${rt.metric}</span>` : ""}</span>`
  ).join("");
  const dns = (r.dns_servers || []).map((d) => `<code>${escapeHtml(d)}</code>`).join(" ");
  document.getElementById("net-reachability").innerHTML = `
    <dt>Default gateway</dt><dd class="stack">${routes || "–"}</dd>
    <dt>DNS servers</dt><dd>${dns || "–"}</dd>
  `;
}

function renderTailscaleSelf(ts) {
  const root = document.getElementById("net-tailscale-self");
  if (!root) return;
  if (!ts) {
    root.innerHTML = '<dt>State</dt><dd class="muted">tailscale not running</dd>';
    return;
  }
  const ips = (ts.ips || []).map((ip) => `<code>${escapeHtml(ip)}</code>`).join(" ");
  const tags = (ts.tags || []).map((t) => `<code>${escapeHtml(t)}</code>`).join(" ");
  const routes = (ts.advertised_routes || []).map((r) => `<code>${escapeHtml(r)}</code>`).join(" ");
  const exitRow = ts.exit_node_active && ts.exit_node
    ? `<dt>Exit node</dt><dd><code>${escapeHtml(ts.exit_node)}</code></dd>`
    : "";
  const isExitRow = ts.is_exit_node ? '<dt>Role</dt><dd>this node is an exit node</dd>' : "";
  root.innerHTML = `
    <dt>State</dt><dd>${escapeHtml(ts.backend_state || "?")}${ts.online ? " · online" : " · offline"}</dd>
    <dt>DNS</dt><dd><code>${escapeHtml(ts.dns_name || "–")}</code> <span class="muted">(${escapeHtml(ts.tailnet || "?")})</span></dd>
    <dt>IPs</dt><dd class="stack">${ips || "–"}</dd>
    ${tags ? `<dt>Tags</dt><dd>${tags}</dd>` : ""}
    ${routes ? `<dt>Advertised routes</dt><dd>${routes}</dd>` : ""}
    ${exitRow}
    ${isExitRow}
    <dt>Tailscale SSH</dt><dd>${ts.ssh_enabled ? "enabled" : "disabled"}</dd>
  `;
}

// ───────── bandwidth chart ─────────
const bwIfaceEl = document.getElementById("bw-iface");
const bwMetaEl = document.getElementById("bw-meta");
const bwChartEl = document.getElementById("bw-chart");

async function fetchBandwidthIfaces() {
  if (!bwIfaceEl) return;
  try {
    const r = await fetch(`${BASE}/api/net_history`);
    const d = await r.json();
    const ifaces = d.ifaces || [];
    if (!ifaces.length) {
      bwIfaceEl.innerHTML = '<option value="">(no history yet)</option>';
      bwMetaEl.textContent = "no samples persisted yet — wait one minute after first start";
      return;
    }
    const prev = bwIfaceEl.value;
    bwIfaceEl.innerHTML = ifaces.map((n) =>
      `<option value="${escapeHtml(n)}"${n === prev ? " selected" : ""}>${escapeHtml(n)}</option>`).join("");
    if (!prev) bwIfaceEl.value = ifaces[0];
    fetchBandwidthSeries();
  } catch (_) {}
}

async function fetchBandwidthSeries() {
  if (!bwChartEl || !bwIfaceEl || !bwIfaceEl.value) return;
  try {
    const r = await fetch(`${BASE}/api/net_history?iface=${encodeURIComponent(bwIfaceEl.value)}`);
    const d = await r.json();
    const samples = d.samples || [];
    drawBandwidth(bwChartEl, samples);
    bwMetaEl.textContent = `${samples.length} samples · ${bwIfaceEl.value}`;
  } catch (_) {}
}

function drawBandwidth(canvas, samples) {
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth || 600;
  const cssH = canvas.clientHeight || 120;
  if (canvas.width !== cssW * dpr || canvas.height !== cssH * dpr) {
    canvas.width = cssW * dpr; canvas.height = cssH * dpr;
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  if (samples.length < 2) return;
  // Compute bps deltas. Skip negative deltas (counter resets across reboot).
  const rxBps = []; const txBps = [];
  for (let i = 1; i < samples.length; i++) {
    const dt = samples[i].ts - samples[i - 1].ts;
    if (dt <= 0) continue;
    const drx = samples[i].rx - samples[i - 1].rx;
    const dtx = samples[i].tx - samples[i - 1].tx;
    rxBps.push(drx >= 0 ? drx / dt : 0);
    txBps.push(dtx >= 0 ? dtx / dt : 0);
  }
  if (!rxBps.length) return;
  const max = Math.max(1, ...rxBps, ...txBps);
  const step = cssW / Math.max(1, rxBps.length - 1);
  const drawSeries = (data, color) => {
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = color;
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = i * step;
      const y = cssH - (v / max) * (cssH - 4) - 2;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  drawSeries(rxBps, "#4cc9f0");
  drawSeries(txBps, "#fbbf24");
  // Legend in top-left.
  ctx.font = "11px ui-monospace, monospace";
  ctx.fillStyle = "#4cc9f0"; ctx.fillText(`↓ rx (peak ${fmtRate(Math.max(...rxBps))})`, 6, 12);
  ctx.fillStyle = "#fbbf24"; ctx.fillText(`↑ tx (peak ${fmtRate(Math.max(...txBps))})`, 6, 26);
}

if (bwIfaceEl) bwIfaceEl.addEventListener("change", fetchBandwidthSeries);

function renderPeers(peers) {
  const tbody = document.querySelector("#net-peers tbody");
  if (!peers.length) { tbody.innerHTML = '<tr><td colspan="5" class="muted">no peers</td></tr>'; return; }
  tbody.innerHTML = peers.map((p) => {
    const dot = p.online ? '<span class="dot good"></span>online' : '<span class="dot muted"></span>offline';
    const lastSeen = p.online
      ? '<span class="muted">now</span>'
      : (p.last_seen ? fmtRelative(Date.parse(p.last_seen) / 1000) : '<span class="muted">–</span>');
    return `<tr>
      <td>${dot}</td>
      <td><code>${escapeHtml(p.hostname)}</code><br><span class="muted">${escapeHtml(p.dns_name)}</span></td>
      <td><code>${escapeHtml(p.ipv4 || "–")}</code></td>
      <td>${escapeHtml(p.os || "–")}</td>
      <td>${lastSeen}</td>
    </tr>`;
  }).join("");
}

function renderSockets(sockets) {
  const tbody = document.querySelector("#net-sockets tbody");
  tbody.innerHTML = (sockets || []).map((s) => `
    <tr>
      <td><code>${escapeHtml(s.proto)}</code></td>
      <td><code>${escapeHtml(s.addr)}</code></td>
      <td>${s.port}</td>
      <td>${s.process ? `<code>${escapeHtml(s.process)}</code>${s.pid ? ` <button class="pid-link" data-pid="${s.pid}">pid ${s.pid}</button>` : ""}` : '<span class="muted">–</span>'}</td>
    </tr>`).join("");
}

// ───────── logs tab (sub-tabs: system / services / cron / auth) ─────────
const SUBTABS = ["system", "services", "cron", "auth"];

function activateSubtab(name) {
  if (!SUBTABS.includes(name)) name = "system";
  document.querySelectorAll(".subtabs > .tab").forEach((b) => {
    b.setAttribute("aria-selected", b.dataset.subtab === name ? "true" : "false");
  });
  document.querySelectorAll(".sub-tab-panel").forEach((p) => {
    p.hidden = p.dataset.subpanel !== name;
  });
  fetchActiveSubtab();
}

function fetchActiveSubtab() {
  const active = document.querySelector(".subtabs > .tab[aria-selected='true']");
  const name = active ? active.dataset.subtab : "system";
  if (name === "system") fetchSyslog();
  if (name === "services") fetchServiceLog();
  if (name === "cron") fetchCronLog();
  if (name === "auth") fetchAuthSummary();
}

document.querySelectorAll(".subtabs > .tab").forEach((b) => {
  b.addEventListener("click", () => activateSubtab(b.dataset.subtab));
});

async function loadLog(bodyEl, metaEl, url, emptyMsg) {
  if (!bodyEl) return;
  bodyEl.textContent = "loading …";
  if (metaEl) metaEl.textContent = "";
  try {
    const r = await fetch(url);
    if (!r.ok) { bodyEl.textContent = `Error: ${r.status}`; return; }
    const d = await r.json();
    const lines = d.lines || [];
    bodyEl.textContent = lines.length ? lines.join("\n") : emptyMsg;
    bodyEl.scrollTop = bodyEl.scrollHeight;
    if (metaEl) metaEl.textContent = `${lines.length} lines · loaded ${new Date().toLocaleTimeString("en-GB")}`;
  } catch (e) {
    bodyEl.textContent = `Error: ${e}`;
  }
}

// system
const syslogSourceEl = document.getElementById("syslog-source");
const syslogPriorityEl = document.getElementById("syslog-priority");
const syslogRefreshEl = document.getElementById("syslog-refresh");
const syslogBodyEl = document.getElementById("syslog-body");
const syslogMetaEl = document.getElementById("syslog-meta");

function fetchSyslog() {
  if (!syslogPriorityEl) return;
  const priority = encodeURIComponent(syslogPriorityEl.value);
  const source = encodeURIComponent(syslogSourceEl ? syslogSourceEl.value : "journal");
  loadLog(syslogBodyEl, syslogMetaEl,
    `${BASE}/api/system/log?source=${source}&priority=${priority}&lines=100`,
    "(no entries at this priority)");
}
if (syslogSourceEl) syslogSourceEl.addEventListener("change", fetchSyslog);
if (syslogPriorityEl) syslogPriorityEl.addEventListener("change", fetchSyslog);
if (syslogRefreshEl) syslogRefreshEl.addEventListener("click", fetchSyslog);

// services (journalctl -u)
const svclogUnitEl = document.getElementById("svclog-unit");
const svclogRefreshEl = document.getElementById("svclog-refresh");
const svclogBodyEl = document.getElementById("svclog-body");
const svclogMetaEl = document.getElementById("svclog-meta");

function fetchServiceLog() {
  if (!svclogUnitEl) return;
  const unit = svclogUnitEl.value;
  if (!unit) { svclogBodyEl.textContent = "select a unit and click refresh"; return; }
  loadLog(svclogBodyEl, svclogMetaEl,
    `${BASE}/api/services/${encodeURIComponent(unit)}/log?lines=200`,
    "(journal empty for this unit)");
}
if (svclogUnitEl) svclogUnitEl.addEventListener("change", fetchServiceLog);
if (svclogRefreshEl) svclogRefreshEl.addEventListener("click", fetchServiceLog);

// cron (file tail)
const cronlogJobEl = document.getElementById("cronlog-job");
const cronlogRefreshEl = document.getElementById("cronlog-refresh");
const cronlogBodyEl = document.getElementById("cronlog-body");
const cronlogMetaEl = document.getElementById("cronlog-meta");

function fetchCronLog() {
  if (!cronlogJobEl) return;
  const job = cronlogJobEl.value;
  if (!job) { cronlogBodyEl.textContent = "select a job and click refresh"; return; }
  loadLog(cronlogBodyEl, cronlogMetaEl,
    `${BASE}/api/cron/${encodeURIComponent(job)}/log?lines=200`,
    "(log file empty)");
}
if (cronlogJobEl) cronlogJobEl.addEventListener("change", fetchCronLog);
if (cronlogRefreshEl) cronlogRefreshEl.addEventListener("click", fetchCronLog);

// auth (ssh logins, last 24h)
const authMetaEl = document.getElementById("auth-meta");
const authRefreshEl = document.getElementById("auth-refresh");
const authAcceptedListEl = document.getElementById("auth-accepted-list");
const authFailedListEl = document.getElementById("auth-failed-list");
const authAcceptedCountEl = document.getElementById("auth-accepted-count");
const authFailedCountEl = document.getElementById("auth-failed-count");

function fmtAuthTime(isoTs) {
  if (!isoTs) return "";
  const d = new Date(isoTs);
  if (isNaN(d.getTime())) return isoTs;
  return fmtEventTime(d.getTime() / 1000);
}

function renderAuthList(root, items, severity) {
  if (!items.length) { root.innerHTML = '<span class="muted">none in window</span>'; return; }
  root.innerHTML = items.slice().reverse().map((it) => {
    const when = fmtAuthTime(it.ts);
    return `<div class="event-row ${severity}" title="${escapeHtml(it.ts || "")}">
      <span class="event-when">${escapeHtml(when)}</span>
      <span class="event-kind">${escapeHtml(it.user || "?")}</span>
      <span class="muted">${escapeHtml(it.ip || "")}</span>
    </div>`;
  }).join("");
}

async function fetchAuthSummary() {
  if (!authMetaEl) return;
  authMetaEl.textContent = "loading…";
  try {
    const r = await fetch(`${BASE}/api/auth/summary`);
    if (!r.ok) { authMetaEl.textContent = `error: ${r.status}`; return; }
    const d = await r.json();
    authAcceptedCountEl.textContent = `(${d.accepted_count})`;
    authFailedCountEl.textContent = `(${d.failed_count})`;
    renderAuthList(authAcceptedListEl, d.recent_accepted || [], "info");
    renderAuthList(authFailedListEl, d.recent_failed || [], "error");
    authMetaEl.textContent = `window: ${d.window} · loaded ${new Date().toLocaleTimeString("en-GB")}`;
  } catch (e) {
    authMetaEl.textContent = `error: ${e}`;
  }
}
if (authRefreshEl) authRefreshEl.addEventListener("click", fetchAuthSummary);

function populateLogDropdowns(snap) {
  if (svclogUnitEl && snap.services && svclogUnitEl.dataset.populated !== "1") {
    svclogUnitEl.innerHTML = snap.services.map((s) =>
      `<option value="${escapeHtml(s.unit)}">${escapeHtml(s.unit)}</option>`).join("");
    svclogUnitEl.dataset.populated = "1";
  }
  if (cronlogJobEl && snap.cron && cronlogJobEl.dataset.populated !== "1") {
    cronlogJobEl.innerHTML = snap.cron.map((j) =>
      `<option value="${escapeHtml(j.name)}">${escapeHtml(j.name)}</option>`).join("");
    cronlogJobEl.dataset.populated = "1";
  }
}

// ───────── favicon (CPU-tinted π) ─────────
const faviconEl = document.getElementById("favicon");
let lastFaviconColor = null;

function colorForCpu(p) {
  if (p == null) return "#4cc9f0";
  if (p >= 90) return "#f87171";
  if (p >= 70) return "#fbbf24";
  return "#4ade80";
}

function updateFavicon(cpuPercent) {
  if (!faviconEl) return;
  const color = colorForCpu(cpuPercent);
  if (color === lastFaviconColor) return;
  lastFaviconColor = color;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
    <rect width="32" height="32" rx="6" fill="#0f1115"/>
    <text x="50%" y="55%" text-anchor="middle" dominant-baseline="central"
          font-family="ui-monospace, monospace" font-size="22" font-weight="700" fill="${color}">π</text>
  </svg>`;
  faviconEl.href = `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`;
}

// ───────── tracked processes (overview) ─────────
const trackedProcsRoot = document.getElementById("tracked-procs-list");
const procHistoryCache = {};  // name -> [cpu values]

function renderTrackedProcs(items) {
  if (!trackedProcsRoot) return;
  if (!items.length) {
    trackedProcsRoot.innerHTML = '<span class="muted">no processes configured (config.TRACKED_PROCESSES)</span>';
    return;
  }
  trackedProcsRoot.innerHTML = items.map((p) => {
    const absent = p.count === 0;
    const stats = absent
      ? "not running"
      : `${p.cpu.toFixed(1)}% CPU · ${p.mem.toFixed(1)}% MEM · ${p.count} PID${p.count === 1 ? "" : "s"}`;
    return `<div class="tracked-row${absent ? " absent" : ""}">
      <span class="tp-name"><code>${escapeHtml(p.name)}</code></span>
      <span class="tp-stats">${stats}</span>
      <canvas data-proc="${escapeHtml(p.name)}"></canvas>
    </div>`;
  }).join("");
  // Lazy-load history for any proc we haven't fetched yet, then redraw.
  for (const item of items) fetchProcHistory(item.name);
  redrawTrackedSparks();
}

async function fetchProcHistory(name) {
  if (procHistoryCache[name] !== undefined) return;
  procHistoryCache[name] = [];  // mark in-flight to avoid duplicate fetches
  try {
    const r = await fetch(`${BASE}/api/proc_history?name=${encodeURIComponent(name)}`);
    const d = await r.json();
    procHistoryCache[name] = (d.samples || []).map((s) => s.cpu || 0);
    redrawTrackedSparks();
  } catch (_) {
    delete procHistoryCache[name];
  }
}

function redrawTrackedSparks() {
  if (!trackedProcsRoot) return;
  trackedProcsRoot.querySelectorAll("canvas").forEach((c) => {
    const data = procHistoryCache[c.dataset.proc] || [];
    drawSpark(c, data, { color: "#a78bfa", min: 0 });
  });
}

// ───────── recent events (overview) ─────────
const EVENT_LABELS = {
  "throttle.undervoltage":    "Undervoltage",
  "throttle.throttled":       "Throttled",
  "throttle.arm_freq_capped": "ARM freq capped",
  "throttle.soft_temp_limit": "Soft temp limit",
};
function fmtEventTime(ts) {
  const d = new Date(ts * 1000);
  const now = Date.now();
  const ageDays = (now - d.getTime()) / 86400000;
  if (ageDays < 1) return d.toLocaleTimeString("en-GB");
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
async function fetchEvents() {
  const root = document.getElementById("events-list");
  if (!root) return;
  try {
    const r = await fetch(`${BASE}/api/events?limit=50`);
    if (!r.ok) return;
    const d = await r.json();
    const events = d.events || [];
    if (!events.length) {
      root.innerHTML = '<span class="muted">no events recorded yet</span>';
      return;
    }
    root.innerHTML = events.map((e) => {
      const sev = ["error", "warning", "info"].includes(e.severity) ? e.severity : "info";
      const label = EVENT_LABELS[e.kind] || e.kind;
      return `<div class="event-row ${sev}">
        <span class="event-when">${escapeHtml(fmtEventTime(e.ts))}</span>
        <span class="event-kind">${escapeHtml(label)}</span>
        <span class="muted">${escapeHtml(e.detail || "")}</span>
      </div>`;
    }).join("");
  } catch (_) {}
}

// ───────── boot ─────────
async function loadHistory() {
  try {
    const r = await fetch(`${BASE}/api/history`);
    const data = await r.json();
    for (const s of data.samples || []) {
      pushHist("cpu", s.cpu);
      pushHist("mem", s.mem);
      if (s.temp != null) pushHist("temp", s.temp);
    }
    refreshSparks();
  } catch (_) {}
}

function connect() {
  const es = new EventSource(`${BASE}/stream`);
  es.onmessage = (ev) => {
    try { applySnapshot(JSON.parse(ev.data)); } catch (_) {}
  };
  es.onerror = () => {
    conn.textContent = "connection lost";
    conn.className = "conn off";
    es.close();
    setTimeout(connect, 3000);
  };
}

window.addEventListener("resize", refreshSparks);
activateTab((location.hash || "#overview").slice(1));
loadHistory().then(connect);
fetchMaintenance();
maintTimer = setInterval(fetchMaintenance, 60000);
fetchEvents();
setInterval(fetchEvents, 60000);
fetchAlerts();
setInterval(fetchAlerts, 15000);
// Drop the per-proc history cache once a minute so the lazy-fetch on the
// next render picks up fresh long-history samples.
setInterval(() => { for (const k of Object.keys(procHistoryCache)) delete procHistoryCache[k]; }, 60000);
