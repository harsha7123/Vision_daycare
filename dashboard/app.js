"use strict";

const RULES_DOC = {
  R1_phone_use: ["Caretaker on phone", "An adult holds a phone near hand or face, cumulatively, within a sliding window"],
  R2_left_zone: ["Caretaker left the child zone", "Children in the play zone while a caretaker stays outside it"],
  R3_unattended: ["Children unattended", ">= 1 child in the play zone and 0 adults inside"],
  R4_ratio: ["Adult : child ratio", "Children per adult in the zone exceeds the centre's policy"],
  R5_fall: ["Child fall / lying still", "A child lying down outside the nap zone"],
  R6_idle: ["Caretaker idle / asleep", "An adult seated, head down, near-zero motion while children are present"],
};
const ZONE_COLORS = { play: "#50be50", nap: "#aa78c8", exit: "#f58c1e", staff_only: "#d24646" };
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "");
const fmtS = (s) => (s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`);

const state = { cam: null, status: null, feed: new Map(), rules: null, zones: null, editing: false, draft: null, frameImg: null };

async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.method = init.method || "POST";
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(opts.json);
  }
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch (_) { /* not json */ }
    throw new Error(msg);
  }
  return r.headers.get("content-type")?.includes("json") ? r.json() : r;
}

function toast(html, cls = "") {
  const t = document.createElement("div");
  t.className = `toast ${cls}`;
  t.innerHTML = html;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 6000);
}

function beep(priority) {
  if (!$("#sound").checked) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const tones = priority === "critical" ? [880, 660, 880] : priority === "high" ? [740, 740] : [600];
    tones.forEach((f, i) => {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
      const t0 = ctx.currentTime + i * 0.18;
      g.gain.setValueAtTime(0.12, t0); g.gain.exponentialRampToValueAtTime(0.001, t0 + 0.16);
      o.start(t0); o.stop(t0 + 0.17);
    });
  } catch (_) { /* audio blocked until user interaction */ }
}

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${b.dataset.tab}`));
  if (b.dataset.tab === "events") loadEvents();
  if (b.dataset.tab === "rules") loadRules();
}));

/* ---------------- status polling ---------------- */
function camStatus() { return state.status?.cameras.find((c) => c.id === state.cam); }

async function pollStatus() {
  try {
    state.status = await api("/api/status");
  } catch (e) {
    $("#pills").innerHTML = `<span class="pill warn">API offline</span>`;
    return;
  }
  const st = state.status;
  const sel = $("#camera-select");
  if (!sel.options.length) {
    st.cameras.forEach((c) => sel.add(new Option(`${c.id} - ${c.room}`, c.id)));
    state.cam = st.cameras[0]?.id;
    setStream();
  }
  const c = camStatus();
  if (!c) return;
  const pills = [
    `<span class="pill">profile <b>${esc(st.profile)}</b></span>`,
    `<span class="pill">fps <b>${c.fps}</b></span>`,
    `<span class="pill">detector <b>${c.models.yolo ? "YOLO " + esc(c.models.device) : "simulated"}</b></span>`,
    `<span class="pill">alerts <b>${esc(st.channels.join(", ") || "none")}</b></span>`,
  ];
  if (c.models.action_model) pills.push(`<span class="pill">action model <b>on</b></span>`);
  $("#pills").innerHTML = pills.join("");
  $("#source-label").textContent = c.source_label || c.source;
  const msg = $("#stage-msg");
  msg.hidden = !c.error;
  msg.textContent = c.error || "";
  if (c.phase !== "running" && !c.error) {
    msg.hidden = false;
    msg.textContent = c.phase === "loading models" ? "Loading YOLO models (first run downloads the weights)..." : "Starting camera...";
  }
  const srcSel = $("#source-select");
  if (document.activeElement !== srcSel) {
    let opt = [...srcSel.options].find((o) => o.value === String(c.source));
    if (!opt) {
      opt = srcSel.querySelector("option[data-current]") || new Option("", "", false, false);
      opt.dataset.current = "1";
      opt.value = String(c.source);
      srcSel.prepend(opt);
    }
    if (opt.dataset.current) opt.textContent = `Current: ${c.source_label || String(c.source).split(/[\/]/).pop()}`;
    srcSel.value = opt.value;
  }
  renderKpis(c, st);
  renderTracks(c);
  renderDeliveries(st.deliveries);
}

function renderKpis(c, st) {
  const k = c.counts || {};
  const t = c.timers || {};
  const bar = (tm) => (tm ? Math.min(100, (100 * tm.value) / tm.threshold) : 0);
  const unatt = t.R3_unattended, ratio = t.R4_ratio;
  $("#kpis").innerHTML = `
    <div class="kpi"><div class="label">Children in zone</div><div class="value">${k.children ?? "-"}</div></div>
    <div class="kpi"><div class="label">Adults in zone</div><div class="value">${k.adults ?? "-"}</div></div>
    <div class="kpi ${ratio && ratio.value > 0 ? "alarm" : ""}"><div class="label">Children per adult</div>
      <div class="value">${k.ratio === null ? "&infin;" : k.ratio ?? "-"}</div><div class="bar"><i style="width:${bar(ratio)}%"></i></div></div>
    <div class="kpi ${unatt && unatt.value > 0 ? "alarm" : ""}"><div class="label">Unattended timer</div>
      <div class="value">${unatt ? unatt.value.toFixed(1) : "0"}<span class="muted"> / ${unatt ? unatt.threshold : "-"}s</span></div>
      <div class="bar"><i style="width:${bar(unatt)}%"></i></div></div>
    <div class="kpi"><div class="label">Alerts (24 h)</div><div class="value">${st.stats_24h.total}</div></div>
    <div class="kpi ${st.stats_24h.unacked ? "alarm" : ""}"><div class="label">Unacknowledged</div><div class="value">${st.stats_24h.unacked}</div></div>`;
}

function renderTracks(c) {
  const rows = (c.tracks || []).map((t) => {
    const timers = [];
    if (t.out_t > 0) timers.push(`<span class="tag hot">outside ${fmtS(t.out_t)}</span>`);
    if (t.idle_t > 0) timers.push(`<span class="tag hot">idle ${fmtS(t.idle_t)}</span>`);
    if (t.lying_t > 0) timers.push(`<span class="tag hot">down ${fmtS(t.lying_t)}</span>`);
    if (t.seated) timers.push(`<span class="tag">seated</span>`);
    if (t.head_down) timers.push(`<span class="tag">head down</span>`);
    const phone = t.role === "adult"
      ? `<span class="tag ${t.phone_state !== "NORMAL" ? "hot" : ""}">${t.phone_ema.toFixed(2)}</span>${t.phone_t > 0 ? `<span class="tag hot">${fmtS(t.phone_t)}</span>` : ""}` : "-";
    const btn = (r) => `<button class="btn small ${(t.override || "auto") === r ? "sel" : ""}" data-tid="${t.track_id}" data-role="${r}">${r}</button>`;
    return `<tr><td>#${t.track_id}</td>
      <td><span class="role ${t.role}">${t.role}</span> <span class="muted">p(child) ${t.p_child}</span></td>
      <td>${t.zones.map((z) => `<span class="tag">${esc(z)}</span>`).join("") || "-"}</td>
      <td>${phone}</td><td>${timers.join("") || "-"}</td>
      <td>${btn("adult")} ${btn("child")} ${btn("auto")}</td></tr>`;
  });
  $("#tracks tbody").innerHTML = rows.join("") || `<tr><td colspan="6" class="muted">Nobody in view.</td></tr>`;
}

$("#tracks").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-tid]");
  if (!b) return;
  try {
    await api(`/api/cameras/${state.cam}/tracks/${b.dataset.tid}/role`, { json: { role: b.dataset.role } });
    toast(`Track #${b.dataset.tid} set to <b>${esc(b.dataset.role)}</b>`);
  } catch (err) { toast(esc(err.message), "critical"); }
});

function renderDeliveries(list) {
  $("#deliveries").innerHTML = list.map((d) => `<li class="${d.status === "sent" ? "" : "fail"}">
    ${new Date(d.at * 1000).toLocaleTimeString()} - <b>${esc(d.channel)}</b> -> ${esc(d.audiences.join(", "))}
    ${d.kind === "escalation" ? "(escalation)" : ""} - ${esc(d.event_id.split("_").slice(-2).join(" "))} - ${esc(d.status)}</li>`).join("")
    || `<li>Nothing sent yet. Set TELEGRAM_* or WEBHOOK_URL in .env to add channels.</li>`;
}

/* ---------------- live stream & source ---------------- */
function setStream() {
  $("#live").src = `/api/cameras/${state.cam}/stream.mjpg?t=${Date.now()}`;
}
$("#live").addEventListener("error", () => setTimeout(setStream, 1500));
$("#camera-select").addEventListener("change", (e) => { state.cam = e.target.value; setStream(); });

$("#source-select").addEventListener("change", async (e) => {
  let v = e.target.value;
  if (v === "__upload") { $("#upload-input").click(); return; }
  if (v === "__rtsp") {
    v = prompt("Stream URL (rtsp://user:pass@host:554/stream or http://...)");
    if (!v) return;
  }
  try {
    await api(`/api/cameras/${state.cam}/source`, { json: { source: v } });
    toast(`Switching source to <b>${esc(v)}</b>${v !== "sim" ? " (loading YOLO the first time can take a few seconds)" : ""}`);
    setTimeout(setStream, 800);
  } catch (err) { toast(esc(err.message), "critical"); }
});

$("#upload-input").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("file", f);
  toast(`Uploading <b>${esc(f.name)}</b>...`);
  try {
    await api(`/api/cameras/${state.cam}/upload`, { method: "POST", body: fd });
    toast(`Now analysing <b>${esc(f.name)}</b>`);
    setTimeout(setStream, 800);
  } catch (err) { toast(esc(err.message), "critical"); }
  e.target.value = "";
});

$("#btn-pose").addEventListener("click", async (e) => {
  const on = e.currentTarget.getAttribute("aria-pressed") !== "true";
  e.currentTarget.setAttribute("aria-pressed", String(on));
  await api(`/api/cameras/${state.cam}/pose?show=${on}`, { method: "POST" });
});

/* ---------------- alert feed (SSE) ---------------- */
function alertCard(ev, fresh = false) {
  const el = document.createElement("div");
  el.className = `alert ${ev.priority} ${ev.ack ? "acked" : ""} ${fresh ? "fresh" : ""}`;
  el.dataset.id = ev.event_id;
  const ack = ev.ack
    ? `<span class="muted">Acknowledged by ${esc(ev.ack.by)}${ev.ack.feedback ? ` - marked ${ev.ack.feedback === "true" ? "true alert" : "false alert"}` : ""}</span>`
    : `<button class="btn small primary" data-act="ack">Acknowledge</button>`;
  const fb = ev.ack?.feedback ? "" : `<button class="btn small" data-act="true" title="Correct alert">&#128077; true</button>
      <button class="btn small" data-act="false" title="False alert">&#128078; false</button>`;
  el.innerHTML = `${ev.snapshot ? `<img src="${esc(ev.snapshot)}" alt="Snapshot, faces blurred" data-act="open">` : ""}
    <div class="body">
      <div><span class="prio ${ev.priority}">${ev.priority}</span> <span class="t">${esc(ev.title)}</span></div>
      <div class="meta">${fmtTime(ev.created_at)} - ${esc(ev.room)} (${esc(ev.camera)})${ev.track_id != null ? ` - track #${ev.track_id}` : ""}
        - ${fmtS(ev.duration_s)} - kids ${ev.children_in_zone} / adults ${ev.adults_in_zone}${ev.escalated ? " - <b>escalated</b>" : ""}</div>
      <div class="actions">${ack} ${fb} ${ev.clip ? `<button class="btn small ghost" data-act="open">clip</button>` : ""}</div>
    </div>`;
  return el;
}

function upsertFeed(ev, fresh = false) {
  const feed = $("#feed");
  $(".empty", feed)?.remove();
  state.feed.set(ev.event_id, ev);
  const old = feed.querySelector(`[data-id="${CSS.escape(ev.event_id)}"]`);
  const card = alertCard(ev, fresh);
  if (old) old.replaceWith(card); else feed.prepend(card);
  while (feed.children.length > 40) feed.lastChild.remove();
}

$("#feed").addEventListener("click", async (e) => {
  const act = e.target.closest("[data-act]")?.dataset.act;
  const id = e.target.closest(".alert")?.dataset.id;
  if (!act || !id) return;
  if (act === "open") return openEvent(state.feed.get(id));
  const feedback = act === "ack" ? null : act;
  try { await api(`/api/events/${id}/ack`, { json: { by: "dashboard", feedback } }); }
  catch (err) { toast(esc(err.message), "critical"); }
});

function connectSSE() {
  const es = new EventSource("/api/events/stream");
  es.addEventListener("event", (m) => {
    const ev = JSON.parse(m.data);
    upsertFeed(ev, true);
    beep(ev.priority);
    toast(`<span class="prio ${ev.priority}">${ev.priority}</span> <b>${esc(ev.title)}</b><br><span class="muted">${esc(ev.room)} - ${fmtS(ev.duration_s)}</span>`, ev.priority);
  });
  es.addEventListener("update", (m) => {
    const ev = JSON.parse(m.data);
    if (state.feed.has(ev.event_id)) upsertFeed(ev);
  });
  es.onerror = () => { es.close(); setTimeout(connectSSE, 3000); };
}

function openEvent(ev) {
  if (!ev) return;
  const clip = ev.clip ? (ev.clip.endsWith(".webm") || ev.clip.endsWith(".mp4")
    ? `<video src="${esc(ev.clip)}" controls autoplay muted loop></video>` : "") : `<p class="muted">Clip is being written...</p>`;
  $("#modal-content").innerHTML = `
    <h2><span class="prio ${ev.priority}">${ev.priority}</span> ${esc(ev.title)}</h2>
    <dl class="kv">
      <dt>Event</dt><dd>${esc(ev.event_id)}</dd>
      <dt>Room / camera</dt><dd>${esc(ev.room)} / ${esc(ev.camera)}</dd>
      <dt>Started</dt><dd>${esc(ev.started_at)} (${fmtS(ev.duration_s)})</dd>
      <dt>In zone</dt><dd>${ev.children_in_zone} children, ${ev.adults_in_zone} adults</dd>
      <dt>Confidence</dt><dd>${ev.confidence}</dd>
      <dt>Status</dt><dd>${ev.ack ? `acknowledged by ${esc(ev.ack.by)} at ${fmtTime(ev.ack.at)}` : "open"}${ev.escalated ? ", escalated" : ""}</dd>
    </dl>
    ${ev.snapshot ? `<img src="${esc(ev.snapshot)}" alt="Snapshot (faces blurred)">` : ""}
    ${clip}
    ${ev.clip ? `<p class="muted">Admin-only clip: 10 s before to 3 s after the threshold was crossed.</p>` : ""}`;
  $("#modal").showModal();
}
$("#modal-close").addEventListener("click", () => $("#modal").close());
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").close(); });

/* ---------------- events tab ---------------- */
async function loadEvents() {
  const q = new URLSearchParams({ limit: 200 });
  if ($("#f-rule").value) q.set("rule", $("#f-rule").value);
  if ($("#f-priority").value) q.set("priority", $("#f-priority").value);
  if ($("#f-unacked").checked) q.set("unacked", "true");
  const list = await api(`/api/events?${q}`);
  list.forEach((ev) => state.feed.set(ev.event_id, ev));
  $("#events tbody").innerHTML = list.map((ev) => `<tr>
    <td>${ev.snapshot ? `<img class="thumb" src="${esc(ev.snapshot)}" data-id="${esc(ev.event_id)}" alt="">` : ""}</td>
    <td>${new Date(ev.created_at).toLocaleString()}</td><td>${esc(ev.rule.split("_")[0])} ${esc(ev.title)}</td>
    <td><span class="prio ${ev.priority}">${ev.priority}</span></td><td>${esc(ev.camera)}</td><td>${fmtS(ev.duration_s)}</td>
    <td>${ev.children_in_zone} / ${ev.adults_in_zone}</td><td>${ev.confidence}</td>
    <td>${ev.ack ? `ack ${esc(ev.ack.by)}${ev.ack.feedback ? ` (${ev.ack.feedback === "true" ? "true" : "false alarm"})` : ""}` : "<b>open</b>"}${ev.escalated ? " - escalated" : ""}</td>
    <td><button class="btn small" data-id="${esc(ev.event_id)}">view</button></td></tr>`).join("")
    || `<tr><td colspan="10" class="muted">No events.</td></tr>`;
  const s = state.status?.stats_24h;
  if (s) {
    $("#stats").innerHTML = s.by_rule.map((r) => `<span class="pill">${esc(r.rule.split("_")[0])} <b>${r.n}</b>
      ${r.tp ? ` - ${r.tp} true` : ""}${r.fp ? ` - ${r.fp} false` : ""}</span>`).join("") || `<span class="muted">No alerts in the last 24 h.</span>`;
  }
}
$("#events").addEventListener("click", (e) => {
  const id = e.target.closest("[data-id]")?.dataset.id;
  if (id) openEvent(state.feed.get(id));
});
["#f-rule", "#f-priority", "#f-unacked"].forEach((s) => $(s).addEventListener("change", loadEvents));
$("#f-refresh").addEventListener("click", loadEvents);

/* ---------------- rules tab ---------------- */
const FIELD_LABELS = { threshold_s: "Threshold (s)", window_s: "Window (s)", cooldown_s: "Cooldown (s)", enter: "Enter score",
  exit: "Exit score", max_children_per_adult: "Max children / adult", max_motion: "Max motion", min_conf: "Min model conf." };

async function loadRules() {
  state.rules = await api("/api/rules");
  $("#rules-profile").textContent = `profile: ${state.status?.profile ?? ""}`;
  $("#rules").innerHTML = Object.entries(state.rules).map(([key, r]) => {
    const fields = Object.keys(FIELD_LABELS).filter((f) => f in r).map((f) =>
      `<label class="f">${FIELD_LABELS[f]}<input type="number" step="any" min="0" data-rule="${key}" data-field="${f}" value="${r[f]}"></label>`).join("");
    return `<div class="rule">
      <div class="rule-head"><div><b>${key.split("_")[0]}</b> ${esc(RULES_DOC[key]?.[0] ?? key)}</div>
        <span class="prio ${r.priority}">${r.priority}</span></div>
      <div class="muted">${esc(RULES_DOC[key]?.[1] ?? "")}</div>
      <label class="switch"><input type="checkbox" data-rule="${key}" data-field="enabled" ${r.enabled !== false ? "checked" : ""}> enabled</label>
      ${fields}</div>`;
  }).join("");
}
$("#rules").addEventListener("change", async (e) => {
  const { rule, field } = e.target.dataset;
  if (!rule) return;
  const value = field === "enabled" ? e.target.checked : Number(e.target.value);
  try {
    await api("/api/rules", { method: "PUT", json: { [rule]: { [field]: value } } });
    toast(`${esc(rule)}.${esc(field)} = <b>${esc(value)}</b>`);
  } catch (err) { toast(esc(err.message), "critical"); loadRules(); }
});

/* ---------------- zone editor ---------------- */
const canvas = $("#zone-canvas");
const ctx = canvas.getContext("2d");

function frameRect() {
  const c = camStatus();
  const [fw, fh] = c?.frame?.[0] ? c.frame : [1280, 720];
  const cw = canvas.width, ch = canvas.height;
  const s = Math.min(cw / fw, ch / fh);
  return { x: (cw - fw * s) / 2, y: (ch - fh * s) / 2, w: fw * s, h: fh * s };
}

function drawZones() {
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width * devicePixelRatio;
  canvas.height = r.height * devicePixelRatio;
  const fr = frameRect();
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  if (state.frameImg) ctx.drawImage(state.frameImg, fr.x, fr.y, fr.w, fr.h);
  const pt = ([x, y]) => [fr.x + x * fr.w, fr.y + y * fr.h];
  const poly = (pts, color, closed) => {
    if (!pts.length) return;
    ctx.beginPath();
    pts.map(pt).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    if (closed) { ctx.closePath(); ctx.fillStyle = color + "33"; ctx.fill(); }
    ctx.strokeStyle = color; ctx.lineWidth = 2 * devicePixelRatio; ctx.stroke();
    pts.map(pt).forEach(([x, y]) => { ctx.fillStyle = color; ctx.fillRect(x - 3, y - 3, 6, 6); });
  };
  state.zones.zones.forEach((z) => {
    poly(z.polygon, ZONE_COLORS[z.type] || "#ccc", true);
    const [x, y] = pt(z.polygon[0]);
    ctx.fillStyle = "#fff"; ctx.font = `${13 * devicePixelRatio}px system-ui`; ctx.fillText(`${z.name} (${z.type})`, x + 6, y + 16 * devicePixelRatio);
  });
  if (state.draft) poly(state.draft.polygon, ZONE_COLORS[state.draft.type], false);
  $("#zone-list").innerHTML = state.zones.zones.map((z, i) =>
    `<li><span class="dot" style="background:${ZONE_COLORS[z.type]}"></span>${esc(z.name)} <span class="muted">${z.type}</span>
      <button class="btn small ghost" data-del="${i}" aria-label="Delete zone">&times;</button></li>`).join("");
}

$("#btn-zones").addEventListener("click", async () => {
  state.zones = await api(`/api/cameras/${state.cam}/zones`);
  const img = new Image();
  img.onload = () => { state.frameImg = img; drawZones(); };
  img.src = `/api/cameras/${state.cam}/frame.jpg?raw=1&t=${Date.now()}`;
  state.editing = true;
  canvas.hidden = false;
  $("#zone-panel").hidden = false;
  drawZones();
});
function closeEditor() {
  state.editing = false; state.draft = null;
  canvas.hidden = true; $("#zone-panel").hidden = true;
  $("#zone-finish").disabled = true;
}
$("#zone-cancel").addEventListener("click", closeEditor);
$("#zone-add").addEventListener("click", () => {
  const type = $("#zone-type").value;
  state.draft = { type, name: $("#zone-name").value.trim() || type, polygon: [] };
  $("#zone-finish").disabled = false;
  toast("Click points on the frame, then press <b>Finish</b>.");
});
canvas.addEventListener("click", (e) => {
  if (!state.draft) return;
  const r = canvas.getBoundingClientRect();
  const fr = frameRect();
  const x = ((e.clientX - r.left) * devicePixelRatio - fr.x) / fr.w;
  const y = ((e.clientY - r.top) * devicePixelRatio - fr.y) / fr.h;
  if (x < 0 || x > 1 || y < 0 || y > 1) return;
  state.draft.polygon.push([+x.toFixed(4), +y.toFixed(4)]);
  drawZones();
});
$("#zone-finish").addEventListener("click", () => {
  if (!state.draft || state.draft.polygon.length < 3) { toast("A zone needs at least 3 points.", "high"); return; }
  state.zones.zones.push(state.draft);
  state.draft = null;
  $("#zone-finish").disabled = true;
  $("#zone-name").value = "";
  drawZones();
});
$("#zone-list").addEventListener("click", (e) => {
  const i = e.target.dataset.del;
  if (i === undefined) return;
  state.zones.zones.splice(Number(i), 1);
  drawZones();
});
$("#zone-save").addEventListener("click", async () => {
  try {
    await api(`/api/cameras/${state.cam}/zones`, { method: "PUT", json: state.zones });
    toast("Zones saved and applied.");
    closeEditor();
  } catch (err) { toast(esc(err.message), "critical"); }
});
window.addEventListener("resize", () => state.editing && drawZones());

/* ---------------- boot ---------------- */
$("#rule-doc").innerHTML = Object.entries(RULES_DOC).map(([k, [t, d]]) =>
  `<tr><td><b>${k.split("_")[0]}</b></td><td>${t}</td><td class="muted">${d}</td><td>${
    { R1_phone_use: "high", R2_left_zone: "medium", R3_unattended: "critical", R4_ratio: "medium", R5_fall: "critical", R6_idle: "low" }[k]}</td></tr>`).join("");
Object.keys(RULES_DOC).forEach((k) => $("#f-rule").add(new Option(`${k.split("_")[0]} ${RULES_DOC[k][0]}`, k)));

(async function boot() {
  await pollStatus();
  setInterval(pollStatus, 1000);
  try { (await api("/api/events?limit=30")).reverse().forEach((ev) => upsertFeed(ev)); } catch (_) { /* empty */ }
  connectSSE();
})();
