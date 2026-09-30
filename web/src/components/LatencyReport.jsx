import { PRIORITY, RULES } from "../lib/constants";
import { Icon } from "./Controls";

const pct = (arr, p) => {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))];
};
const avg = (arr) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : null);
const ms = (v) => (v == null ? "-" : `${Math.round(v)} ms`);

/** Summarise per-frame timings collected by the Live screen into a report object. */
export function buildReport(metrics) {
  const f = metrics.frames.filter((x) => x.render != null);
  const col = (k) => f.map((x) => x[k]);
  const stats = (k) => ({ median: pct(col(k), 50), p95: pct(col(k), 95), min: f.length ? Math.min(...col(k)) : null, max: f.length ? Math.max(...col(k)) : null, avg: avg(col(k)) });
  const dur = f.length > 1 ? (f[f.length - 1].at - f[0].at) / 1000 : 0;
  return {
    generated_at: new Date().toISOString(),
    server: metrics.hello || {},
    browser: navigator.userAgent,
    source: metrics.source,
    resolution: metrics.resolution,
    duration_s: Math.round(dur),
    frames: f.length,
    fps: dur ? +(f.length / dur).toFixed(1) : 0,
    end_to_end: stats("e2e"),
    breakdown: { encode: stats("encode"), network: stats("network"), decode: stats("decode"), ai: stats("infer"), rules_and_reply: stats("other"), render: stats("render") },
    alerts: metrics.alerts,
  };
}

function Row({ label, s, hint }) {
  return (
    <tr>
      <td>{label}{hint && <div className="muted small">{hint}</div>}</td>
      <td className="num">{ms(s.median)}</td><td className="num">{ms(s.p95)}</td><td className="num">{ms(s.min)}</td><td className="num">{ms(s.max)}</td>
    </tr>
  );
}

export default function LatencyReport({ report, framesCsv, onClose }) {
  const b = report.breakdown;
  const parts = [["Encode", b.encode.median, "#607D8B"], ["Network", b.network.median, "#EF6C00"], ["AI", b.ai.median, "#1565C0"],
    ["Rules + reply", (b.decode.median || 0) + (b.rules_and_reply.median || 0), "#2E7D32"], ["Render", b.render.median, "#6D4C41"]];
  const total = parts.reduce((a, p) => a + (p[1] || 0), 0) || 1;
  const download = (name, text, type) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };
  const stamp = report.generated_at.slice(0, 19).replace(/[:T]/g, "-");

  return (
    <div className="modal-backdrop report-backdrop" role="dialog" aria-modal="true" aria-label="Latency report">
      <div className="report card">
        <div className="drawer-head">
          <h2>Live latency report</h2>
          <button className="btn icon-btn ghost no-print" onClick={onClose} aria-label="Close"><Icon name="x" /></button>
        </div>
        <div className="report-body">
          <div className="report-meta small muted">
            {new Date(report.generated_at).toLocaleString()} · {report.source} · {report.resolution} sent · {report.duration_s} s · {report.frames} frames<br />
            Server: <b>{report.server.gpu || report.server.device || "unknown"}</b> ({report.server.device}) · models {report.server.models?.pose?.split("/").pop()} + {report.server.models?.detector?.split("/").pop()}
          </div>

          <div className="report-kpis">
            <div className="stat"><div className="stat-value">{ms(report.end_to_end.median)}</div><div className="muted small">Typical delay (median), camera frame to box on screen</div></div>
            <div className="stat"><div className="stat-value">{ms(report.end_to_end.p95)}</div><div className="muted small">Slowest 5% of frames (p95)</div></div>
            <div className="stat"><div className="stat-value">{report.fps}</div><div className="muted small">Frames analysed per second</div></div>
            <div className="stat"><div className="stat-value">{ms(b.ai.median)}</div><div className="muted small">AI time per frame (YOLO pose + phone)</div></div>
          </div>

          <h3>Where the time goes (median frame)</h3>
          <div className="stackbar" aria-label="Latency breakdown">
            {parts.map(([label, v, color]) => v > 0 && (
              <span key={label} style={{ width: `${(100 * v) / total}%`, background: color }} title={`${label}: ${Math.round(v)} ms`}>
                {(100 * v) / total > 9 ? `${label} ${Math.round(v)}` : ""}
              </span>
            ))}
          </div>
          <table className="rtable">
            <thead><tr><th>Step</th><th>Median</th><th>p95</th><th>Min</th><th>Max</th></tr></thead>
            <tbody>
              <Row label="End to end" s={report.end_to_end} hint="Frame captured in the browser until its boxes are drawn" />
              <Row label="Encode JPEG" s={b.encode} hint="Browser" />
              <Row label="Network (upload + reply)" s={b.network} hint="Browser to server and back" />
              <Row label="Decode frame" s={b.decode} hint="Server" />
              <Row label="AI detection" s={b.ai} hint="Server GPU/CPU" />
              <Row label="Rules + reply" s={b.rules_and_reply} hint="Server" />
              <Row label="Draw on screen" s={b.render} hint="Browser" />
            </tbody>
          </table>

          <h3>Alerts: how long until the alert appeared</h3>
          {report.alerts.length ? (
            <table className="rtable">
              <thead><tr><th>Time</th><th>Alert</th><th>Rule waits</th><th>Measured</th><th>System delay</th></tr></thead>
              <tbody>
                {report.alerts.map((a, i) => (
                  <tr key={i}>
                    <td>{new Date(a.at).toLocaleTimeString()}</td>
                    <td><span className="dot" style={{ background: PRIORITY[a.priority]?.color }} /> {RULES[a.rule]?.title || a.rule}{a.track_id != null ? ` (#${a.track_id})` : ""}</td>
                    <td className="num">{a.threshold_s != null ? `${a.threshold_s} s` : "-"}</td>
                    <td className="num">{a.from_detection_s != null ? `${a.from_detection_s.toFixed(1)} s` : "-"}</td>
                    <td className="num">{ms(a.system_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="muted small">No alerts in this session. Hold a phone and look at it until the phone meter fills up to measure alert delay.</p>}
          <p className="muted small">
            <b>Rule waits</b> is the deliberate waiting time (e.g. 6 s on the phone before alerting). <b>Measured</b> is from the moment the
            behaviour was first detected to the alert on screen. <b>System delay</b> is the end-to-end delay of the frame that triggered it.
            Camera hardware adds another ~30-100 ms before the browser receives a frame; browsers can't measure that part.
          </p>
        </div>
        <div className="call-actions no-print" style={{ padding: "0 20px 18px", justifyContent: "flex-end" }}>
          <button className="btn" onClick={() => download(`latency-report-${stamp}.json`, JSON.stringify(report, null, 2), "application/json")}>Download JSON</button>
          <button className="btn" onClick={() => download(`latency-frames-${stamp}.csv`, framesCsv, "text/csv")}>Download per-frame CSV</button>
          <button className="btn primary" onClick={() => window.print()}>Print / save as PDF</button>
        </div>
      </div>
    </div>
  );
}
