import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import CallModal from "./components/CallModal";
import Controls, { Icon } from "./components/Controls";
import { AlertsPanel, PeoplePanel, PriorityTag, RulesPanel } from "./components/Panels";
import SettingsDrawer, { DEFAULT_SETTINGS } from "./components/SettingsDrawer";
import Timeline from "./components/Timeline";
import Toasts from "./components/Toasts";
import Upload from "./components/Upload";
import VideoStage from "./components/VideoStage";
import ZoneToolbar from "./components/ZoneToolbar";
import { absUrl, api } from "./lib/api";
import { PRESETS, PRIORITY, PRIORITY_ORDER, RULES, fmtDur, fmtTime } from "./lib/constants";
import { alertBeep } from "./lib/sound";
import { useStored } from "./lib/useStored";

const EMPTY_EDITOR = { active: false, zones: [], draft: null };

export default function App() {
  const videoRef = useRef(null);
  const prevT = useRef(0);
  const [stage, setStage] = useState("upload");            // upload | setup | processing | review
  const [file, setFile] = useState(null);
  const [videoUrl, setVideoUrl] = useState(null);
  const [sample, setSample] = useState(false);
  const [playable, setPlayable] = useState(true);
  const [job, setJob] = useState(null);
  const [uploadPct, setUploadPct] = useState(0);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [serverOk, setServerOk] = useState(null);
  const [serverCfg, setServerCfg] = useState(null);
  const [room, setRoom] = useState("Toddlers room");
  const [preset, setPreset] = useState("quick");
  const [ruleOv, setRuleOv] = useState({});
  const [roleOv, setRoleOv] = useState({});
  const [editor, setEditor] = useState(EMPTY_EDITOR);
  const [applied, setApplied] = useState("");
  const [busy, setBusy] = useState(false);
  const [opts, setOpts] = useStored("vd.overlay", { zones: true, pose: true, phones: true, blur: true });
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [rate, setRate] = useState(1);
  const [focusId, setFocusId] = useState(null);
  const [selected, setSelected] = useState(null);
  const [tab, setTab] = useState("alerts");
  const [toasts, setToasts] = useState([]);
  const [call, setCall] = useState(null);
  const [drawer, setDrawer] = useState(false);
  const [settings, setSettings] = useStored("vd.settings", DEFAULT_SETTINGS);
  const [callLog, setCallLog] = useStored("vd.callLog", []);
  const [acked, setAcked] = useState(() => new Set());
  const [called, setCalled] = useState(() => new Set());

  /* ---------------- server ---------------- */
  const checkServer = useCallback(async () => {
    try {
      await api.health();
      setServerOk(true);
      setServerCfg(await api.config());
    } catch {
      setServerOk(false);
    }
  }, []);
  useEffect(() => {
    checkServer();
    const id = setInterval(checkServer, 30000);
    return () => clearInterval(id);
  }, [checkServer]);

  /* ---------------- toasts ---------------- */
  const toast = useCallback((t) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((ts) => [...ts.slice(-3), { id, ...t }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), t.ms || 7000);
  }, []);
  const dismiss = (id) => setToasts((ts) => ts.filter((x) => x.id !== id));

  /* ---------------- options sent to the server ---------------- */
  const currentOptions = useMemo(
    () => ({ preset, rules: ruleOv, zones: editor.zones, roles: roleOv, room }),
    [preset, ruleOv, editor.zones, roleOv, room],
  );
  const pending = stage === "review" && applied && JSON.stringify(currentOptions) !== applied;

  /* ---------------- file / sample ---------------- */
  const resetReview = () => {
    setResult(null); setJob(null); setRoleOv({}); setRuleOv({}); setAcked(new Set()); setCalled(new Set());
    setFocusId(null); setSelected(null); setError(""); setTab("alerts"); setDuration(0); setTime(0); setPlayable(true);
    prevT.current = 0;
  };
  const onFile = (f) => {
    if (!f.type.startsWith("video/") && !/\.(mp4|mov|webm|mkv|avi|m4v)$/i.test(f.name)) {
      toast({ title: "Not a video file", text: "Choose an MP4, MOV, WebM, AVI or MKV file.", priority: "high" });
      return;
    }
    if (videoUrl?.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
    resetReview();
    setFile(f);
    if (sample) setRoom("Toddlers room");
    setSample(false);
    setVideoUrl(URL.createObjectURL(f));
    setEditor({ ...EMPTY_EDITOR, active: true });
    setStage("setup");
  };
  const onSample = async () => {
    try {
      const res = await fetch("/sample/result.json").then((r) => r.json());
      resetReview();
      setSample(true);
      setFile(null);
      setVideoUrl("/sample/sample.webm");
      setResult(res);
      setRoom(res.room);
      setPreset(res.preset || "demo");
      setEditor({ ...EMPTY_EDITOR, zones: res.zones });
      setApplied(JSON.stringify({ preset: res.preset || "demo", rules: {}, zones: res.zones, roles: {}, room: res.room }));
      setStage("review");
      toast({ title: "Sample loaded", text: "Press play. Alerts pop up as they happen, and the parent is called for critical ones." });
    } catch {
      toast({ title: "Sample not available", priority: "high" });
    }
  };
  const newVideo = () => {
    if (job?.id && !sample) api.remove(job.id).catch(() => {});
    if (videoUrl?.startsWith("blob:")) URL.revokeObjectURL(videoUrl);
    resetReview();
    setVideoUrl(null);
    setFile(null);
    setSample(false);
    setEditor(EMPTY_EDITOR);
    setStage("upload");
  };

  /* ---------------- analysis ---------------- */
  const limitS = serverCfg?.limits?.max_video_s ?? 300;
  const tooLong = stage === "setup" && duration > limitS;
  const analyze = async () => {
    setError("");
    setEditor((e) => ({ ...e, active: false, draft: null }));
    setStage("processing");
    setUploadPct(0);
    try {
      const j = await api.upload(file, { ...currentOptions, zones: editor.zones, preview: !playable }, setUploadPct);
      setJob(j);
    } catch (e) {
      setError(e.message);
      setStage("setup");
      setEditor((ed) => ({ ...ed, active: true }));
    }
  };

  useEffect(() => {
    if (stage !== "processing" || !job?.id) return;
    let stop = false;
    const tick = async () => {
      try {
        const j = await api.job(job.id);
        if (stop) return;
        setJob(j);
        if (j.status === "done") {
          const res = await api.result(job.id);
          if (stop) return;
          setResult(res);
          if (res.preview) setVideoUrl(absUrl(res.preview));
          setEditor({ ...EMPTY_EDITOR, zones: res.zones });
          setApplied(JSON.stringify({ ...currentOptions, zones: res.zones }));
          setStage("review");
          const worst = res.summary.worst;
          toast(worst
            ? { priority: worst, title: `${res.events.length} situation${res.events.length > 1 ? "s" : ""} flagged`, text: "Press play to watch them happen." }
            : { title: "Analysis complete", text: "No safety issues found in this video." });
        } else if (j.status === "error") {
          setError(j.error || "Analysis failed");
          setStage("setup");
          setEditor((ed) => ({ ...ed, active: true }));
        }
      } catch (e) {
        if (!stop) setError(e.message);
      }
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(id); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, job?.id]);

  const rerun = async () => {
    if (sample) return;
    setBusy(true);
    try {
      const res = await api.rerun(job.id, currentOptions);
      setResult(res);
      setApplied(JSON.stringify(currentOptions));
      setEditor((e) => ({ ...e, active: false, draft: null }));
      setCalled(new Set());
      toast({ title: "Re-analyzed", text: `${res.events.length} alert${res.events.length === 1 ? "" : "s"} with your changes.`, priority: res.summary.worst || undefined });
    } catch (e) {
      toast({ title: "Re-analysis failed", text: e.message, priority: "high" });
    }
    setBusy(false);
  };

  /* ---------------- calls ---------------- */
  const startCall = useCallback((e) => {
    const contact = settings.contacts.find((c) => c.id === settings.primary) || settings.contacts[0];
    if (!contact) {
      setDrawer(true);
      toast({ title: "Add a parent contact first", priority: "medium" });
      return;
    }
    videoRef.current?.pause();
    const mode = settings.mode === "real" && serverCfg?.notify?.twilio ? "real" : "simulated";
    setCall({ contact, event: { ...e, room: result?.room || room }, mode });
    setCalled((s) => new Set(s).add(e.event_id));
  }, [settings, serverCfg, result, room, toast]);

  /* ---------------- playback & live alerts ---------------- */
  const live = useRef({});
  live.current = { result, stage, settings, called, startCall, toast, room };
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => {
      const t = v.currentTime;
      setTime(t);
      const prev = prevT.current;
      prevT.current = t;
      const s = live.current;
      if (s.stage !== "review" || !s.result || v.paused || t < prev || t - prev > 1.5) return;
      for (const e of s.result.events) {
        if (e.t > prev && e.t <= t) {
          alertBeep(e.priority);
          setFocusId(e.event_id);
          const auto = s.settings.autoCall?.[e.priority] && !s.called.has(e.event_id);
          s.toast({
            priority: e.priority, title: RULES[e.rule]?.title || e.title,
            text: `${fmtTime(e.t)} · ${s.result.room}${auto ? " · calling the parent..." : ""}`,
            action: auto ? null : { label: "Call parent", fn: () => live.current.startCall(e) },
          });
          if (auto) s.startCall(e);
        }
      }
    };
    const onMeta = () => {
      setDuration(v.duration || 0);
      if (live.current.stage === "setup") v.currentTime = Math.min(0.5, (v.duration || 1) / 2);
    };
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    if (v.readyState >= 1) onMeta();                   // metadata may load before this effect runs
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeking", onTime);
    v.addEventListener("loadedmetadata", onMeta);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    return () => {
      v.removeEventListener("timeupdate", onTime);
      v.removeEventListener("seeking", onTime);
      v.removeEventListener("loadedmetadata", onMeta);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
    };
  }, [videoUrl, stage]);

  useEffect(() => { if (videoRef.current) videoRef.current.playbackRate = rate; }, [rate, videoUrl]);

  const banner = useMemo(() => {
    if (!result || stage !== "review") return null;
    const active = result.events.filter((e) => e.t <= time && time - e.t < 5);
    active.sort((a, b) => PRIORITY_ORDER.indexOf(a.priority) - PRIORITY_ORDER.indexOf(b.priority));
    return active[0] || null;
  }, [result, time, stage]);

  const seek = (t, e) => {
    const v = videoRef.current;
    if (!v) return;
    prevT.current = t;                                  // don't re-fire alerts we jump over
    v.currentTime = t;
    if (e) setFocusId(e.event_id);
  };
  const jump = (e) => {
    seek(Math.max(0, e.t - 3), e);
    videoRef.current?.play();
  };
  const sorted = useMemo(() => (result?.events || []).slice().sort((a, b) => a.t - b.t), [result]);
  const nextAlert = () => { const e = sorted.find((x) => x.t > time + 0.5); if (e) jump(e); };
  const prevAlert = () => { const e = [...sorted].reverse().find((x) => x.t < time - 3.5); if (e) jump(e); };

  const toggleAck = (e) => setAcked((s) => {
    const n = new Set(s);
    if (n.has(e.event_id)) n.delete(e.event_id); else n.add(e.event_id);
    return n;
  });

  /* ---------------- zone editor ---------------- */
  const addPoint = (p) => setEditor((e) => (e.draft ? { ...e, draft: { ...e.draft, polygon: [...e.draft.polygon, p.map((v) => +v.toFixed(4))] } } : e));
  const finishShape = () => setEditor((e) => (e.draft && e.draft.polygon.length >= 3 ? { ...e, zones: [...e.zones, e.draft], draft: null } : e));
  const toggleZoneEdit = () => {
    videoRef.current?.pause();
    setEditor((e) => ({ ...e, active: !e.active, draft: null }));
  };

  /* ---------------- render ---------------- */
  const summary = result?.summary;
  const worst = summary?.worst;
  const people = result?.tracks.filter((t) => t.frames >= 3) || [];

  return (
    <div className="app">
      <header className="topbar">
        <button className="brand" onClick={newVideo} aria-label="Vision Daycare home">
          <span className="logo"><svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 4.5C7 4.5 2.7 7.6 1 12c1.7 4.4 6 7.5 11 7.5s9.3-3.1 11-7.5c-1.7-4.4-6-7.5-11-7.5Zm0 12.5a5 5 0 1 1 0-10 5 5 0 0 1 0 10Zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" /></svg></span>
          <span><b>Vision Daycare</b><span className="brand-sub">Video safety review</span></span>
        </button>
        <div className="topbar-right">
          <span className={`status-pill ${serverOk === true ? "ok" : serverOk === false ? "down" : ""}`}
            title={serverOk === false ? "The free server may be asleep. It wakes on the first request (about 1 min)." : ""}>
            <span className="dot" />{serverOk === true ? "Server online" : serverOk === false ? "Server offline" : "Checking server"}
          </span>
          <button className="btn" onClick={() => setDrawer(true)}><Icon name="users" size={16} /> Contacts & calling</button>
        </div>
      </header>

      <main>
        {stage === "upload" && <Upload onFile={onFile} onSample={onSample} limits={serverCfg?.limits} serverOk={serverOk} />}

        {(stage === "setup" || stage === "processing") && (
          <div className="layout">
            <div className="main-col">
              <div className="card video-card">
                <VideoStage src={videoUrl} videoRef={videoRef} result={null} opts={opts}
                  editor={stage === "setup" && playable ? editor : { ...editor, active: playable, draft: null }}
                  onPoint={addPoint} onFinishShape={finishShape} onVideoError={() => setPlayable(false)}>
                  {!playable && (
                    <div className="stage-notice">
                      <b>Your browser can't play this video format</b> (for example HEVC or MPEG-4 Part 2).
                      That's fine: the server will analyze it and also make a playable preview. You can draw zones after the analysis.
                    </div>
                  )}
                </VideoStage>
                {stage === "setup" && playable && (
                  <div className="card-section">
                    <div className="section-title"><h2>Mark the areas <span className="muted">(optional)</span></h2></div>
                    <ZoneToolbar editor={editor} setEditor={setEditor} />
                  </div>
                )}
              </div>
            </div>
            <aside className="side-col">
              <div className="card pad">
                {stage === "setup" ? (
                  <>
                    <h2>Analysis settings</h2>
                    <div className="file-row">
                      <div><b>{file?.name}</b><div className="muted small">{(file?.size / 1e6).toFixed(1)} MB{duration ? ` · ${fmtTime(duration)}` : ""}</div></div>
                      <button className="link small" onClick={newVideo}>Change</button>
                    </div>
                    <label className="field"><span className="label">Room name</span>
                      <input className="input" value={room} onChange={(e) => setRoom(e.target.value)} /></label>
                    <div className="field">
                      <span className="label">How quickly to flag</span>
                      <div className="preset-list">
                        {Object.entries(PRESETS).map(([k, p]) => (
                          <label key={k} className={`preset ${preset === k ? "on" : ""}`}>
                            <input type="radio" name="preset" checked={preset === k} onChange={() => { setPreset(k); setRuleOv({}); }} />
                            <span><b>{p.label}</b><span className="muted small block">{p.desc}</span></span>
                          </label>
                        ))}
                      </div>
                    </div>
                    <div className="call-summary">
                      <Icon name="phone" size={16} />
                      <span className="small">
                        {Object.entries(settings.autoCall).filter(([, v]) => v).map(([k]) => PRIORITY[k].label).join(", ") || "No"} alerts call{" "}
                        <b>{(settings.contacts.find((c) => c.id === settings.primary) || settings.contacts[0])?.name || "nobody yet"}</b>
                        {" "}({settings.mode === "real" ? "real call" : "simulated"})
                      </span>
                      <button className="link small" onClick={() => setDrawer(true)}>Edit</button>
                    </div>
                    {tooLong && <div className="warn-box small">This video is {fmtTime(duration)} long; the server limit is {fmtTime(limitS)}. Trim it first.</div>}
                    {serverOk === false && <div className="warn-box small">The analysis server is offline. If it's a free Hugging Face Space it may be waking up; wait a minute and retry. You can change the server address under Contacts & calling.</div>}
                    {error && <div className="error-box small">{error}</div>}
                    <button className="btn primary large block" onClick={analyze} disabled={!file || tooLong || serverOk === false}>
                      Analyze video
                    </button>
                  </>
                ) : (
                  <ProcessingCard job={job} uploadPct={uploadPct} error={error} onCancel={newVideo} />
                )}
              </div>
            </aside>
          </div>
        )}

        {stage === "review" && result && (
          <div className="layout">
            <div className="main-col">
              <div className="summary">
                <div className="verdict" style={worst ? { background: PRIORITY[worst].bg, borderColor: PRIORITY[worst].border, color: PRIORITY[worst].color } : undefined}>
                  <Icon name={worst ? "alert" : "shield"} size={26} />
                  <div>
                    <div className="verdict-title">
                      {worst ? `${result.events.length} situation${result.events.length > 1 ? "s" : ""} flagged` : "No safety issues found"}
                    </div>
                    <div className="small">
                      {worst ? PRIORITY_ORDER.filter((p) => summary.by_priority[p]).map((p) => `${summary.by_priority[p]} ${PRIORITY[p].label.toLowerCase()}`).join(" · ") : "Every rule stayed below its threshold."}
                    </div>
                  </div>
                </div>
                <Stat label="Adults" value={summary.adults} />
                <Stat label="Children" value={summary.children} />
                <Stat label="Most children in play area" value={summary.max_children_in_zone} />
                <Stat label="Video" value={fmtTime(result.video.duration)} />
              </div>

              <div className="card video-card">
                <div className="video-head">
                  <div>
                    <b>{result.room}</b>
                    <span className="muted small"> · {sample ? "sample video (simulated classroom)" : file?.name} · {PRESETS[result.preset || preset]?.label} sensitivity</span>
                  </div>
                  <div className="row">
                    <button className={`btn small ${editor.active ? "primary" : ""}`} onClick={toggleZoneEdit}>{editor.active ? "Done editing zones" : "Edit zones"}</button>
                    <button className="btn small" onClick={newVideo}>New video</button>
                  </div>
                </div>
                <VideoStage src={videoUrl} videoRef={videoRef} result={result} opts={opts} editor={editor}
                  onPoint={addPoint} onFinishShape={finishShape} selected={selected}
                  onSelect={(tid) => { setSelected(tid); if (tid != null) setTab("people"); }}
                  banner={editor.active ? null : banner} roleOverrides={roleOv} />
                {editor.active ? (
                  <div className="card-section"><ZoneToolbar editor={editor} setEditor={setEditor} /></div>
                ) : (
                  <>
                    <Controls video={videoRef} playing={playing} time={time} duration={duration || result.video.duration}
                      rate={rate} setRate={setRate} opts={opts} setOpts={setOpts} onPrev={prevAlert} onNext={nextAlert} hasEvents={!!sorted.length} />
                    <Timeline result={result} duration={duration || result.video.duration} time={time} onSeek={seek} focusId={focusId} />
                  </>
                )}
              </div>
            </div>

            <aside className="side-col">
              <div className="card side-card">
                <div className="tabs" role="tablist">
                  {[["alerts", `Alerts ${result.events.length}`], ["people", `People ${people.length}`], ["rules", "Rules"]].map(([k, label]) => (
                    <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "on" : ""}`} onClick={() => setTab(k)}>{label}</button>
                  ))}
                </div>
                {tab === "alerts" && (
                  <AlertsPanel events={sorted} time={time} focusId={focusId} acked={acked} calledIds={called}
                    onJump={jump} onCall={startCall} onAck={toggleAck} />
                )}
                {tab === "people" && (
                  <PeoplePanel tracks={result.tracks} overrides={roleOv} setOverrides={setRoleOv} selected={selected}
                    onSelect={setSelected} onJumpTo={(t) => seek(t)} />
                )}
                {tab === "rules" && (
                  <RulesPanel preset={preset} setPreset={setPreset} presets={serverCfg?.presets || (sample ? { [preset]: result.rules } : null)}
                    overrides={ruleOv} setOverrides={setRuleOv} />
                )}
                {pending && (
                  <div className="pending-bar">
                    <span className="small">{sample ? "The sample can't be re-analyzed. Upload your own video to try your changes." : "You changed the settings. Re-analyze to update the alerts."}</span>
                    {!sample && <button className="btn primary" onClick={rerun} disabled={busy}>{busy ? "Re-analyzing..." : "Re-analyze"}</button>}
                  </div>
                )}
              </div>
              {focusId && (() => {
                const e = result.events.find((x) => x.event_id === focusId);
                return e ? (
                  <div className="card pad focus-card">
                    <div className="row between"><PriorityTag p={e.priority} /><span className="muted small">{fmtTime(e.t)}</span></div>
                    <h3>{RULES[e.rule]?.title}</h3>
                    <p className="muted small">{RULES[e.rule]?.desc} It lasted {fmtDur(e.duration_s)}, with {e.children_in_zone} children and {e.adults_in_zone} adults in the play area.
                      {" "}Confidence {Math.round(e.confidence * 100)}%.</p>
                  </div>
                ) : null;
              })()}
            </aside>
          </div>
        )}
      </main>

      <SettingsDrawer open={drawer} onClose={() => setDrawer(false)} settings={settings} setSettings={setSettings}
        callLog={callLog} clearLog={() => setCallLog([])} serverCfg={serverCfg} onServerChange={checkServer} />
      {call && <CallModal call={call} settings={settings} onClose={() => setCall(null)} onLog={(c) => setCallLog((l) => [...l.slice(-49), c])} />}
      <Toasts toasts={toasts} dismiss={dismiss} />
    </div>
  );
}

function Stat({ label, value }) {
  return <div className="stat"><div className="stat-value">{value}</div><div className="muted small">{label}</div></div>;
}

function ProcessingCard({ job, uploadPct, error, onCancel }) {
  const status = job?.status;
  const steps = [
    ["Uploading video", !job ? "active" : "done", !job ? `${Math.round(uploadPct * 100)}%` : ""],
    ["Waiting for the server", status === "queued" ? "active" : job && status !== "queued" ? "done" : "", status === "queued" && job.position > 1 ? `#${job.position} in line` : ""],
    ["Finding and tracking people", status === "detecting" ? "active" : ["analyzing", "done"].includes(status) ? "done" : "", status === "detecting" ? `${Math.round((job.progress / 0.9) * 100)}%` : ""],
    ["Checking safety rules", status === "analyzing" ? "active" : status === "done" ? "done" : "", ""],
  ];
  const pct = !job ? uploadPct * 0.1 : 0.1 + (job.progress || 0) * 0.9;
  return (
    <div className="processing">
      <h2>Analyzing...</h2>
      <div className="progress"><span style={{ width: `${Math.max(3, pct * 100)}%` }} /></div>
      <ol className="proc-steps">
        {steps.map(([label, st, extra]) => (
          <li key={label} className={st}>
            <span className="proc-dot">{st === "done" ? <Icon name="check" size={14} /> : null}</span>
            <span>{label}</span>
            {extra && <span className="muted small">{extra}</span>}
          </li>
        ))}
      </ol>
      <p className="muted small">On the free CPU server a 1-minute video takes about 1-3 minutes. You can keep this tab open while it works.</p>
      {error && <div className="error-box small">{error}</div>}
      <button className="btn" onClick={onCancel}>Cancel</button>
    </div>
  );
}
