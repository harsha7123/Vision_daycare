import { useCallback, useEffect, useRef, useState } from "react";
import { absUrl, getApiUrl } from "../lib/api";
import { PRESETS, PRIORITY, ROLE, RULES, fmtTime } from "../lib/constants";
import { contentRect, drawFrame } from "../lib/overlay";
import { Icon } from "./Controls";
import LatencyReport, { buildReport } from "./LatencyReport";
import { PriorityTag } from "./Panels";

const SEND_WIDTH = 960;       // frames are resized to this width before upload (enough for phones at desk distance)
const MAX_FPS = 12;

/**
 * Live analysis: the laptop camera (or a video file played as a camera) is streamed frame by frame
 * to the server over a WebSocket; each answer (people, keypoints, phones, rule state, new alerts)
 * is drawn over the live picture. One frame in flight at a time, so latency never builds up.
 */
async function playWhenVisible(v) {
  for (let i = 0; i < 3; i++) {
    try {
      await v.play();
      return;
    } catch (e) {
      if (e.name !== "AbortError" && !document.hidden) throw e;
      // paused because the tab is in the background: wait until it is visible, then retry
      await new Promise((res) => {
        if (!document.hidden) return setTimeout(res, 300);
        const h = () => { if (!document.hidden) { document.removeEventListener("visibilitychange", h); res(); } };
        document.addEventListener("visibilitychange", h);
      });
    }
  }
  await v.play();
}

export default function Live({ opts, setOpts, onEvent, onCall, calledIds, autoCall, setAutoCall }) {
  const videoRef = useRef(null);
  const canvasRef = useRef(null);
  const wsRef = useRef(null);
  const streamRef = useRef(null);
  const latest = useRef(null);
  const loop = useRef({ running: false, inflight: false, sentAt: 0, last: 0, frames: 0, fpsT: 0 });
  const [status, setStatus] = useState("idle");        // idle | starting | live | error
  const [error, setError] = useState("");
  const [source, setSource] = useState("camera");
  const [preset, setPreset] = useState("quick");
  const [allAdults, setAllAdults] = useState(true);      // webcam tests: everyone in view is a caretaker
  const [stats, setStats] = useState(null);
  const [device, setDevice] = useState("");
  const [events, setEvents] = useState([]);
  const [tracks, setTracks] = useState([]);
  const [overrides, setOverrides] = useState({});
  const [rules, setRules] = useState(null);
  const [now, setNow] = useState(0);
  const fileInput = useRef(null);
  // per-frame timings for the latency report
  const metrics = useRef({ frames: [], alerts: [], phoneStart: {}, hello: null, source: "", resolution: "" });
  const [report, setReport] = useState(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  /* ---------------- overlay draw loop ---------------- */
  useEffect(() => {
    let raf;
    const tick = () => {
      const v = videoRef.current, c = canvasRef.current;
      if (v && c) {
        const dpr = window.devicePixelRatio || 1;
        if (c.width !== Math.round(v.clientWidth * dpr)) { c.width = Math.round(v.clientWidth * dpr); c.height = Math.round(v.clientHeight * dpr); }
        const ctx = c.getContext("2d");
        ctx.clearRect(0, 0, c.width, c.height);
        const f = latest.current;
        if (f && loop.current.running && performance.now() - f.at < 1500) {
          const r = contentRect(v, c);
          const scale = dpr * Math.max(0.85, Math.min(1.4, r.w / dpr / 900));
          drawFrame(ctx, v, r, scale, f, f.w, f.h, { ...opts, roleOverrides: overrides });
          if (f.metric && f.metric.render == null) {          // first time this answer reaches the screen
            f.metric.render = performance.now() - f.at;
            f.metric.e2e = f.metric.capturedToReply + f.metric.render;
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [opts, overrides]);

  /* ---------------- frame pump ---------------- */
  const pump = useCallback(async () => {
    const L = loop.current;
    if (!L.running) return;
    const v = videoRef.current, ws = wsRef.current;
    const due = performance.now() - L.last >= 1000 / MAX_FPS;
    if (v && ws?.readyState === 1 && !L.inflight && due && v.readyState >= 2 && v.videoWidth) {
      const w = Math.min(SEND_WIDTH, v.videoWidth), h = Math.round((v.videoHeight * w) / v.videoWidth);
      const capturedAt = performance.now();
      L.canvas = L.canvas || document.createElement("canvas");
      L.canvas.width = w; L.canvas.height = h;
      L.canvas.getContext("2d").drawImage(v, 0, 0, w, h);
      L.inflight = true;
      L.last = performance.now();
      const blob = await new Promise((res) => L.canvas.toBlob(res, "image/jpeg", 0.72));
      if (blob && ws.readyState === 1) {
        L.sentAt = performance.now();
        L.capturedAt = capturedAt;
        L.encode = L.sentAt - capturedAt;
        ws.send(await blob.arrayBuffer());
      } else L.inflight = false;
    }
    if (!L.timer) L.timer = setTimeout(() => { L.timer = null; pump(); }, 15);   // fallback poll
  }, []);
  const pumpRef = useRef(pump);
  pumpRef.current = pump;

  const onMessage = useCallback((ev) => {
    const L = loop.current;
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      setDevice(m.gpu ? `${m.device}, ${m.gpu}` : m.device); setRules(m.rules); setStatus("live");
      metrics.current.hello = { device: m.device, gpu: m.gpu, models: m.models };
      metrics.current.rules = m.rules;
      return;
    }
    if (m.type === "ack" && m.rules) { setRules(m.rules); metrics.current.rules = m.rules; return; }
    if (m.type === "error") { setError(m.error); L.inflight = false; return; }
    if (m.type !== "frame") return;
    L.inflight = false;
    // send the next frame as soon as this one is answered (no timer when we are already late:
    // background windows slow timers down to ~1 per second)
    const wait = 1000 / MAX_FPS - (performance.now() - L.last);
    if (wait <= 1) pumpRef.current(); else setTimeout(() => pumpRef.current(), wait);
    const t = performance.now();
    const rtt = t - L.sentAt;
    const M = metrics.current;
    const metric = { at: t, encode: L.encode, rtt, network: Math.max(0, rtt - m.ms.total), decode: m.ms.decode, infer: m.ms.infer,
      other: Math.max(0, m.ms.total - m.ms.decode - m.ms.infer), capturedToReply: t - L.capturedAt, render: null, e2e: null, people: m.p.length };
    M.frames.push(metric);
    if (M.frames.length > 20000) M.frames.shift();
    M.resolution = `${m.w}x${m.h}`;
    // when did each adult's phone use start (for "detected -> alert" timing)?
    for (const p of m.p) {
      if (p[7] > 0 && M.phoneStart[p[0]] == null) M.phoneStart[p[0]] = Date.now() - p[7] * 1000;
      if (p[7] === 0 && p[6] < 0.2) delete M.phoneStart[p[0]];
    }
    latest.current = { ...m, at: t, metric };
    L.frames += 1;
    if (performance.now() - L.fpsT > 1000) {
      setStats({ rtt: Math.round(rtt), infer: m.ms.infer, server: m.ms.total, fps: (L.frames * 1000) / (performance.now() - L.fpsT), size: `${m.w}x${m.h}` });
      L.frames = 0;
      L.fpsT = performance.now();
      setTracks(m.tracks);
    }
    setNow(m.t);
    for (const e of m.events) {
      const full = { ...e, room: e.room || "Live camera" };
      const start = e.rule === "R1_phone_use" ? M.phoneStart[e.track_id] : Date.now() - e.duration_s * 1000;
      M.alerts.push({ at: Date.now(), rule: e.rule, priority: e.priority, track_id: e.track_id,
        threshold_s: M.rules?.[e.rule]?.threshold_s, from_detection_s: start ? (Date.now() - start) / 1000 : null,
        system_ms: Math.round(t - L.capturedAt) });
      setEvents((es) => [full, ...es].slice(0, 50));
      onEventRef.current?.(full);
    }
  }, []);

  /* ---------------- start / stop ---------------- */
  const stop = useCallback(() => {
    loop.current.running = false;
    wsRef.current?.close();
    wsRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    const v = videoRef.current;
    if (v) { v.pause(); v.srcObject = null; }
    latest.current = null;
    setStatus("idle");
    if (metrics.current.frames.length >= 20) setReport(buildReport(metrics.current));
  }, []);
  useEffect(() => stop, [stop]);

  const connect = () => new Promise((resolve, reject) => {
    const url = `${getApiUrl().replace(/^http/, "ws")}/ws/live?preset=${preset}&roles=${allAdults ? "all_adult" : "auto"}&room=${encodeURIComponent("Live camera")}`;
    const ws = new WebSocket(url);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error("Can't connect to the analysis server. Is it running, and does it support live mode?"));
    ws.onmessage = onMessage;
    ws.onclose = (e) => {
      if (loop.current.running) {
        setError(e.reason || "Connection to the server was lost.");
        loop.current.running = false;
        setStatus("error");
      }
    };
    wsRef.current = ws;
  });

  const start = async (file) => {
    setError("");
    setEvents([]);
    setReport(null);
    metrics.current = { frames: [], alerts: [], phoneStart: {}, hello: null, rules: null, source: file ? `video file (${file.name})` : "laptop camera", resolution: "" };
    setOverrides({});
    setStatus("starting");
    const v = videoRef.current;
    try {
      if (file) {
        v.srcObject = null;
        v.src = URL.createObjectURL(file);
        v.loop = true;
        v.muted = true;
      } else {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser can't open the camera here. The camera only works on https:// pages or localhost.");
        const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
        streamRef.current = stream;
        v.removeAttribute("src");
        v.srcObject = stream;
      }
      await playWhenVisible(v);
      await connect();
      Object.assign(loop.current, { running: true, inflight: false, last: 0, frames: 0, fpsT: performance.now() });
      pump();
    } catch (e) {
      stop();
      setStatus("error");
      setError(e.name === "NotAllowedError" ? "Camera permission was denied. Allow camera access in the browser's address bar and try again." : e.message);
    }
  };

  // Browsers pause muted videos in background tabs; resume when the tab is visible again.
  useEffect(() => {
    const onVis = () => { if (!document.hidden && loop.current.running) videoRef.current?.play().catch(() => {}); };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  const send = (msg) => wsRef.current?.readyState === 1 && wsRef.current.send(JSON.stringify(msg));
  const setRole = (tid, role) => {
    setOverrides((o) => { const n = { ...o }; if (role === "auto") delete n[tid]; else n[tid] = role; return n; });
    send({ type: "role", track_id: tid, role });
  };
  const changePreset = (p) => { setPreset(p); send({ type: "rules", preset: p }); };

  /* ---------------- derived: phone meter & banner ---------------- */
  const f = latest.current;
  const r1 = rules?.R1_phone_use;
  const phoneRec = f?.p.filter((p) => (overrides[p[0]] || (p[5] === "c" ? "child" : "adult")) === "adult").sort((a, b) => b[7] - a[7])[0];
  const banner = events.find((e) => now - e.t < 5);
  const live = status === "live";

  return (
    <div className="layout">
      <div className="main-col">
        <div className="card video-card">
          <div className="video-head">
            <div className="row">
              <span className={`live-dot ${live ? "on" : ""}`} /><b>{live ? "LIVE" : "Live camera"}</b>
              <span className="muted small">{live ? `analysed on the server (${device})` : "Your camera stays in this browser; only frames are sent for analysis, and nothing is recorded"}</span>
            </div>
            <div className="row">
              {!live ? (
                <>
                  <div className="segmented small" role="group" aria-label="Source">
                    <button className={source === "camera" ? "on" : ""} onClick={() => setSource("camera")}>Laptop camera</button>
                    <button className={source === "file" ? "on" : ""} onClick={() => setSource("file")}>Video file as camera</button>
                  </div>
                  {status === "starting" && <span className="muted small">Connecting... the first start loads the AI models onto the GPU (up to ~30 s)</span>}
                  <button className="btn primary" disabled={status === "starting"}
                    onClick={() => (source === "file" ? fileInput.current.click() : start())}>
                    {status === "starting" ? "Starting..." : "Start live analysis"}
                  </button>
                  <input ref={fileInput} type="file" accept="video/*" hidden onChange={(e) => e.target.files[0] && start(e.target.files[0])} />
                </>
              ) : (
                <>
                  <button className="btn" onClick={() => setReport(buildReport(metrics.current))}>Latency report</button>
                  <button className="btn hangup" onClick={stop}>Stop</button>
                </>
              )}
            </div>
          </div>
          <div className="stage">
            <video ref={videoRef} playsInline muted />
            <canvas ref={canvasRef} className="overlay" aria-label="Live detections" />
            {!live && status !== "starting" && (
              <div className="stage-notice">
                <b>Live phone-use detection</b>
                <span>Press <b>Start live analysis</b> and allow the camera. Then hold your phone and look at it: after {r1?.threshold_s ?? 6} s on the phone (Short-clips sensitivity) the caretaker alert fires.</span>
                {error && <span className="notice-error">{error}</span>}
              </div>
            )}
            {banner && (
              <div className="banner" style={{ background: PRIORITY[banner.priority].color }} role="alert">
                <span className="banner-prio">{PRIORITY[banner.priority].label}</span>
                <span className="banner-title">{RULES[banner.rule]?.title}</span>
              </div>
            )}
          </div>
          <div className="controls">
            <div className="live-stats">
              {stats ? (
                <>
                  <span title="Time from sending a frame to receiving its analysis"><b>{stats.rtt} ms</b> round trip</span>
                  <span title="YOLO pose + phone detection time on the server"><b>{stats.infer} ms</b> AI</span>
                  <span><b>{stats.fps.toFixed(1)}</b> fps</span>
                  <span className="muted">{stats.size}</span>
                </>
              ) : <span className="muted small">Latency and speed appear here once live.</span>}
            </div>
            <div className="chips" role="group" aria-label="Overlay options">
              {[["pose", "Skeleton"], ["phones", "Phones"], ["blur", "Blur faces"]].map(([k, label]) => (
                <button key={k} className={`chip ${opts[k] ? "on" : ""}`} aria-pressed={!!opts[k]} onClick={() => setOpts((o) => ({ ...o, [k]: !o[k] }))}>
                  {opts[k] && <Icon name="check" size={14} />}{label}
                </button>
              ))}
            </div>
          </div>
          {live && phoneRec && (
            <div className="phone-panel">
              <div className="phone-meter">
                <span className="small"><b>#{phoneRec[0]} phone use</b>{" "}
                  <span className={phoneRec[6] >= (r1?.enter ?? 0.6) ? "hot-text" : "muted"}>{phoneRec[6] >= (r1?.enter ?? 0.6) ? "IN USE: timer running" : "not in use"}</span></span>
                <div className="progress"><span style={{ width: `${Math.min(100, (100 * phoneRec[7]) / (r1?.threshold_s || 6))}%`, background: phoneRec[7] > 0 ? "#D84315" : undefined }} /></div>
                <span className="small"><b>{phoneRec[7].toFixed(1)}</b> / {r1?.threshold_s ?? 6} s</span>
              </div>
              {(() => {
                const ev = f?.pe?.[String(phoneRec[0])];
                if (!ev) return null;
                const tick = (v) => (v >= 0.7 ? "yes" : v > 0 ? "partly" : "no");
                return (
                  <div className="evidence small">
                    <span>Phone seen: <b>{ev.conf ? `${Math.round(ev.conf * 100)}%` : "no"}</b></span>
                    <span>In hand: <b className={ev.hand >= 0.7 ? "ok-text" : ""}>{tick(ev.hand)}</b></span>
                    <span>Looking at it: <b className={ev.face >= 0.7 ? "ok-text" : ""}>{tick(ev.face)}</b></span>
                    <span>Score: <b>{ev.score.toFixed(2)}</b> (needs {r1?.enter ?? 0.6})</span>
                    <span className="muted">{ev.reason}</span>
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      </div>

      {report && <LatencyReport report={report} onClose={() => setReport(null)}
        framesCsv={["t_ms,encode_ms,network_ms,decode_ms,ai_ms,rules_ms,render_ms,end_to_end_ms,people",
          ...metrics.current.frames.filter((x) => x.render != null).map((x) => [Math.round(x.at), x.encode, x.network, x.decode, x.infer, x.other, x.render, x.e2e, x.people].map((v) => Math.round(v)).join(","))].join(String.fromCharCode(10))} />}
      <aside className="side-col">
        <div className="card pad">
          <h2>Sensitivity</h2>
          <div className="segmented" role="group" style={{ marginTop: 8 }}>
            {Object.entries(PRESETS).map(([k, p]) => (
              <button key={k} className={preset === k ? "on" : ""} onClick={() => changePreset(k)}>{p.label}</button>
            ))}
          </div>
          <p className="muted small">{PRESETS[preset].desc}</p>
          <label className="switch" style={{ marginTop: 10 }}>
            <input type="checkbox" checked={allAdults} onChange={(e) => { setAllAdults(e.target.checked); send({ type: "role_mode", mode: e.target.checked ? "all_adult" : "auto" }); }} />
            <span className="track"><span className="knob" /></span>
            <span className="small"><b>Everyone in view is a caretaker</b><span className="muted block">Turn on for laptop-camera tests. Turn off for a classroom camera so children are detected.</span></span>
          </label>
        </div>
        <div className="card pad">
          <h2>When an alert fires</h2>
          <p className="muted small">You always get a banner, a sound and a snapshot. Choose which alerts also call the parent (set the contact under Contacts & calling).</p>
          {[["high", "Caretaker on phone (high)"], ["critical", "Children unattended / child fell (critical)"], ["medium", "Other (medium)"]].map(([p, label]) => (
            <label key={p} className="switch" style={{ marginTop: 8 }}>
              <input type="checkbox" checked={!!autoCall?.[p]} onChange={(e) => setAutoCall({ ...autoCall, [p]: e.target.checked })} />
              <span className="track"><span className="knob" /></span>
              <span className="small">Call parent: {label}</span>
            </label>
          ))}
        </div>
        <div className="card">
          <div className="section-title pad-x" style={{ paddingTop: 14 }}><h2>People in view</h2></div>
          {tracks.length ? (
            <ul className="people-list" style={{ paddingBottom: 12 }}>
              {tracks.map((t) => {
                const role = overrides[t.track_id] || t.role;
                const cur = overrides[t.track_id] || "auto";
                return (
                  <li key={t.track_id} className="person">
                    <span className="avatar" style={{ background: ROLE[role].bg, color: ROLE[role].color }}>#{t.track_id}</span>
                    <div className="person-main"><b style={{ color: ROLE[role].color }}>{ROLE[role].label}</b></div>
                    <div className="segmented small">
                      {["adult", "child", "auto"].map((r) => (
                        <button key={r} className={cur === r ? "on" : ""} onClick={() => setRole(t.track_id, r)}>{r === "auto" ? "Auto" : ROLE[r].label}</button>
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : <p className="muted small pad-x" style={{ paddingBottom: 14 }}>Nobody in view yet.</p>}
        </div>
        <div className="card">
          <div className="section-title pad-x" style={{ paddingTop: 14 }}><h2>Live alerts</h2></div>
          {events.length ? (
            <ul className="alert-list" style={{ paddingBottom: 12 }}>
              {events.map((e) => (
                <li key={e.event_id} className="alert-card" style={{ borderLeftColor: PRIORITY[e.priority].color }}>
                  <a className="thumb" href={absUrl(e.snapshot)} target="_blank" rel="noreferrer">
                    <img src={absUrl(e.snapshot)} alt="Snapshot with faces blurred" />
                    <span className="thumb-time">{fmtTime(e.t)}</span>
                  </a>
                  <div className="alert-main">
                    <PriorityTag p={e.priority} />
                    <div className="alert-title">{RULES[e.rule]?.title}</div>
                    <div className="muted small">{new Date(e.created_at).toLocaleTimeString()}{e.track_id != null ? ` · person #${e.track_id}` : ""}</div>
                    <div className="alert-actions">
                      <button className="btn small call" onClick={() => onCall(e)}><Icon name="phone" size={14} />{calledIds.has(e.event_id) ? "Call again" : "Call parent"}</button>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          ) : <p className="muted small pad-x" style={{ paddingBottom: 14 }}>No alerts yet.</p>}
        </div>
      </aside>
    </div>
  );
}
