import { useState } from "react";
import { absUrl } from "../lib/api";
import { PRESETS, PRIORITY, PRIORITY_ORDER, ROLE, RULES, fmtDur, fmtTime } from "../lib/constants";
import { Icon } from "./Controls";

export function PriorityTag({ p }) {
  const c = PRIORITY[p];
  return <span className="tag" style={{ color: c.color, background: c.bg, borderColor: c.border }}>{c.label}</span>;
}

/* ------------------------------------------------------------------ alerts */
export function AlertsPanel({ events, time, focusId, acked, calledIds, onJump, onCall, onAck, noPeople }) {
  const [filter, setFilter] = useState("all");
  const shown = events.filter((e) => filter === "all" || e.priority === filter);
  const counts = Object.fromEntries(PRIORITY_ORDER.map((p) => [p, events.filter((e) => e.priority === p).length]));
  if (!events.length && noPeople) {
    return (
      <div className="empty-state">
        <div className="empty-icon" style={{ background: "#FFF6DC", color: "#A86B00" }}><Icon name="alert" size={28} /></div>
        <h3>No people detected</h3>
        <p>The AI works on real camera footage. Cartoon or animated videos, drawings and very dark or blurry shots can't be analysed.</p>
      </div>
    );
  }
  if (!events.length) {
    return (
      <div className="empty-state">
        <div className="empty-icon ok"><Icon name="shield" size={28} /></div>
        <h3>No safety issues found</h3>
        <p>Nothing in this video crossed a rule threshold. Try the <b>Rules</b> tab to make detection more sensitive, or
          correct who is a child on the <b>People</b> tab.</p>
      </div>
    );
  }
  return (
    <div className="panel-body">
      <div className="filter-row" role="group" aria-label="Filter by priority">
        <button className={`chip ${filter === "all" ? "on" : ""}`} onClick={() => setFilter("all")}>All {events.length}</button>
        {PRIORITY_ORDER.filter((p) => counts[p]).map((p) => (
          <button key={p} className={`chip ${filter === p ? "on" : ""}`} onClick={() => setFilter(p)}>
            <span className="dot" style={{ background: PRIORITY[p].color }} />{PRIORITY[p].label} {counts[p]}
          </button>
        ))}
      </div>
      <ul className="alert-list">
        {shown.map((e) => {
          const reached = time >= e.t;
          const isAcked = acked.has(e.event_id);
          return (
            <li key={e.event_id} className={`alert-card ${focusId === e.event_id ? "focus" : ""} ${reached ? "" : "upcoming"} ${isAcked ? "acked" : ""}`}
              style={{ borderLeftColor: PRIORITY[e.priority].color }}>
              <button className="thumb" onClick={() => onJump(e)} aria-label={`Jump to ${fmtTime(e.t)}`}>
                {e.snapshot ? <img src={absUrl(e.snapshot)} alt="Snapshot with faces blurred" loading="lazy" /> : <span className="thumb-ph" />}
                <span className="thumb-time">{fmtTime(e.t)}</span>
              </button>
              <div className="alert-main">
                <div className="alert-top"><PriorityTag p={e.priority} />{!reached && <span className="muted small">upcoming</span>}</div>
                <div className="alert-title">{RULES[e.rule]?.title || e.title}</div>
                <div className="muted small">
                  {fmtDur(e.duration_s)} · {e.children_in_zone} children, {e.adults_in_zone} adults in the play area
                  {e.track_id != null ? ` · person #${e.track_id}` : ""}
                </div>
                <div className="alert-actions">
                  <button className="btn small" onClick={() => onJump(e)}>Watch</button>
                  <button className="btn small call" onClick={() => onCall(e)}>
                    <Icon name="phone" size={14} />{calledIds.has(e.event_id) ? "Call again" : "Call parent"}
                  </button>
                  <button className={`btn small ${isAcked ? "done" : ""}`} onClick={() => onAck(e)} aria-pressed={isAcked}>
                    <Icon name="check" size={14} />{isAcked ? "Reviewed" : "Mark reviewed"}
                  </button>
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ people */
export function PeoplePanel({ tracks, overrides, setOverrides, selected, onSelect, onJumpTo }) {
  const people = tracks.filter((t) => t.frames >= 3);
  const set = (tid, role) => setOverrides((o) => {
    const n = { ...o };
    if (role === "auto") delete n[tid]; else n[tid] = role;
    return n;
  });
  if (!people.length) return <div className="empty-state"><h3>Nobody detected</h3><p>No people were found in this video.</p></div>;
  return (
    <div className="panel-body">
      <p className="muted small pad-x">The AI guesses who is a child from body size and proportions. Correct it here, then press <b>Re-analyze</b>. Click a person on the video to find them.</p>
      <ul className="people-list">
        {people.map((t) => {
          const current = overrides[t.track_id] || t.override || "auto";
          const role = overrides[t.track_id] || t.role;
          return (
            <li key={t.track_id} className={`person ${selected === t.track_id ? "focus" : ""}`} onClick={() => onSelect(t.track_id)}>
              <span className="avatar" style={{ background: ROLE[role].bg, color: ROLE[role].color }}>#{t.track_id}</span>
              <div className="person-main">
                <div><b style={{ color: ROLE[role].color }}>{ROLE[role].label}</b>
                  <span className="muted small"> · {current === "auto" ? `AI: ${Math.round(t.p_child * 100)}% child` : "set by you"}</span></div>
                <button className="link small" onClick={(e) => { e.stopPropagation(); onJumpTo(t.first_t); }}>
                  seen {fmtTime(t.first_t)} to {fmtTime(t.last_t)}
                </button>
              </div>
              <div className="segmented small" role="group" aria-label={`Role for person ${t.track_id}`} onClick={(e) => e.stopPropagation()}>
                {["adult", "child", "auto"].map((r) => (
                  <button key={r} className={current === r ? "on" : ""} onClick={() => set(t.track_id, r)}>{r === "auto" ? "Auto" : ROLE[r].label}</button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ rules */
export function RulesPanel({ preset, setPreset, presets, overrides, setOverrides }) {
  const base = presets?.[preset] || {};
  const val = (rule, k) => overrides[rule]?.[k] ?? base[rule]?.[k];
  const setVal = (rule, k, v) => setOverrides((o) => ({ ...o, [rule]: { ...(o[rule] || {}), [k]: v } }));
  return (
    <div className="panel-body">
      <div className="field">
        <label className="label">Sensitivity</label>
        <div className="segmented" role="group" aria-label="Sensitivity preset">
          {Object.entries(PRESETS).map(([k, p]) => (
            <button key={k} className={preset === k ? "on" : ""} onClick={() => { setPreset(k); setOverrides({}); }}>{p.label}</button>
          ))}
        </div>
        <p className="muted small">{PRESETS[preset].desc}</p>
      </div>
      {!presets && <p className="muted small">Connect to the server to load rule settings.</p>}
      {presets && Object.entries(RULES).map(([key, meta]) => {
        const enabled = val(key, "enabled") !== false;
        const thr = Number(val(key, "threshold_s") ?? 0);
        return (
          <div key={key} className={`rule-row ${enabled ? "" : "off"}`}>
            <div className="rule-head">
              <label className="switch">
                <input type="checkbox" checked={enabled} onChange={(e) => setVal(key, "enabled", e.target.checked)} />
                <span className="track"><span className="knob" /></span>
                <span><b>{meta.code}</b> {meta.title}</span>
              </label>
              <PriorityTag p={base[key]?.priority || "medium"} />
            </div>
            <p className="muted small">{meta.desc}</p>
            <div className="slider-row">
              <span className="small">Alert after</span>
              <input type="range" min="1" max={preset === "production" ? 600 : 60} step="1" value={thr} disabled={!enabled}
                onChange={(e) => setVal(key, "threshold_s", Number(e.target.value))} aria-label={`${meta.title} threshold in seconds`} />
              <span className="value">{fmtDur(thr)}</span>
            </div>
            {key === "R4_ratio" && (
              <div className="slider-row">
                <span className="small">Max children per adult</span>
                <input type="range" min="1" max="15" step="1" value={Number(val(key, "max_children_per_adult") ?? 8)} disabled={!enabled}
                  onChange={(e) => setVal(key, "max_children_per_adult", Number(e.target.value))} aria-label="Maximum children per adult" />
                <span className="value">{val(key, "max_children_per_adult")}</span>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
