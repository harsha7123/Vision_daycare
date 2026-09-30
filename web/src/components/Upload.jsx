import { useRef, useState } from "react";
import { Icon } from "./Controls";

const STEPS = [
  ["upload", "Add a video", "A day-care or CCTV clip from your computer. MP4, MOV or WebM."],
  ["users", "AI watches it", "It detects every person, works out adults and children, and follows them frame by frame."],
  ["alert", "Situations are flagged", "Unattended children, caretakers on phones, falls, too many children per adult."],
  ["phone", "The parent is called", "Critical alerts ring the parent and read out what happened."],
];

export default function Upload({ onFile, onSample, onLive, limits, serverOk }) {
  const input = useRef(null);
  const [drag, setDrag] = useState(false);

  const pick = (files) => {
    const f = files?.[0];
    if (f) onFile(f);
  };

  return (
    <div className="upload-page">
      <section className="hero">
        <h1>Check a day-care video for safety issues</h1>
        <p className="lead">Upload a clip and the AI flags when children are left alone, a caretaker is busy on a phone, or a child falls. It then calls the parent.</p>
      </section>

      <div
        className={`dropzone ${drag ? "drag" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files); }}
        onClick={() => input.current.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current.click()}
      >
        <div className="drop-icon"><Icon name="upload" size={30} /></div>
        <div className="drop-title">Drop a video here, or <span className="link">choose a file</span></div>
        <div className="muted small">
          Up to {limits?.max_upload_mb ?? 150} MB and {Math.round((limits?.max_video_s ?? 300) / 60)} minutes. Videos are processed on the analysis server and deleted after an hour.
        </div>
        {serverOk === false && <div className="warn-box small">The analysis server is offline or waking up. You can still watch the sample analysis.</div>}
        <input ref={input} type="file" accept="video/*" hidden onChange={(e) => pick(e.target.files)} />
      </div>

      <div className="or-row"><span>or</span></div>
      <div className="choice-row">
        <button className="choice" onClick={onLive}>
          <span className="step-icon live"><span className="live-dot on" /></span>
          <span><b>Live camera</b><span className="muted small block">Real-time detection from this laptop's camera. Hold up your phone to test the phone alert.</span></span>
        </button>
        <button className="choice" onClick={onSample}>
          <span className="step-icon"><Icon name="play" size={20} /></span>
          <span><b>Watch a sample analysis</b><span className="muted small block">A pre-analysed simulated classroom where every rule is triggered. Works offline.</span></span>
        </button>
      </div>

      <ol className="steps">
        {STEPS.map(([icon, title, desc], i) => (
          <li key={title}>
            <span className="step-icon"><Icon name={icon} size={20} /></span>
            <div><b>{i + 1}. {title}</b><p className="muted small">{desc}</p></div>
          </li>
        ))}
      </ol>
      <p className="muted small center privacy">Privacy: faces are blurred on snapshots, there's no face recognition (only "adult" and "child"), and uploads are deleted automatically. Only use videos you have the right to process.</p>
    </div>
  );
}
