import { fmtTime } from "../lib/constants";

export function Icon({ name, size = 18 }) {
  const paths = {
    play: "M8 5v14l11-7z",
    pause: "M6 5h4v14H6zM14 5h4v14h-4z",
    prev: "M6 6h2v12H6zm3.5 6 8.5 6V6z",
    next: "M16 6h2v12h-2zM6 18l8.5-6L6 6z",
    phone: "M6.6 10.8a15.1 15.1 0 0 0 6.6 6.6l2.2-2.2a1 1 0 0 1 1-.25 11.4 11.4 0 0 0 3.6.57 1 1 0 0 1 1 1V20a1 1 0 0 1-1 1A17 17 0 0 1 3 4a1 1 0 0 1 1-1h3.5a1 1 0 0 1 1 1c0 1.25.2 2.45.57 3.57a1 1 0 0 1-.25 1z",
    upload: "M5 20h14v-2H5zm7-16-5 5h3v6h4V9h3z",
    gear: "M19.4 13a7.5 7.5 0 0 0 0-2l2.1-1.6-2-3.4-2.5 1a7.3 7.3 0 0 0-1.7-1L15 3.3h-4l-.4 2.7a7.3 7.3 0 0 0-1.7 1l-2.5-1-2 3.4L6.6 11a7.5 7.5 0 0 0 0 2l-2.1 1.6 2 3.4 2.5-1a7.3 7.3 0 0 0 1.7 1l.4 2.7h4l.4-2.7a7.3 7.3 0 0 0 1.7-1l2.5 1 2-3.4zM13 15.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7z",
    users: "M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.3 0-7 1.2-7 3.5V19h14v-2.5C15 14.2 10.3 13 8 13zm8 0c-.3 0-.6 0-1 .1a4.2 4.2 0 0 1 2 3.4V19h6v-2.5c0-2.3-4.7-3.5-7-3.5z",
    check: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
    x: "M19 6.4 17.6 5 12 10.6 6.4 5 5 6.4 10.6 12 5 17.6 6.4 19 12 13.4 17.6 19 19 17.6 13.4 12z",
    alert: "M1 21h22L12 2zm12-3h-2v-2h2zm0-4h-2v-4h2z",
    sms: "M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM7 9h10v2H7zm6 5H7v-2h6zm4-6H7V6h10z",
    shield: "M12 1 3 5v6c0 5.6 3.8 10.7 9 12 5.2-1.3 9-6.4 9-12V5z",
    refresh: "M17.7 6.3A8 8 0 1 0 19.7 14h-2.1a6 6 0 1 1-1.4-6.2L13 11h7V4z",
  };
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" className="icon">
      <path fill="currentColor" d={paths[name]} />
    </svg>
  );
}

export default function Controls({ video, playing, time, duration, rate, setRate, opts, setOpts, onPrev, onNext, hasEvents }) {
  const toggle = (k) => setOpts((o) => ({ ...o, [k]: !o[k] }));
  const chips = [["zones", "Zones"], ["pose", "Skeleton"], ["phones", "Phones"], ["blur", "Blur faces"]];
  return (
    <div className="controls">
      <div className="controls-left">
        <button className="btn icon-btn primary" onClick={() => (video.current?.paused ? video.current.play() : video.current.pause())}
          aria-label={playing ? "Pause" : "Play"}>
          <Icon name={playing ? "pause" : "play"} />
        </button>
        <button className="btn icon-btn" onClick={onPrev} disabled={!hasEvents} aria-label="Previous alert" title="Previous alert"><Icon name="prev" /></button>
        <button className="btn icon-btn" onClick={onNext} disabled={!hasEvents} aria-label="Next alert" title="Next alert"><Icon name="next" /></button>
        <span className="time">{fmtTime(time)} / {fmtTime(duration)}</span>
        <select className="select small" value={rate} onChange={(e) => setRate(Number(e.target.value))} aria-label="Playback speed">
          {[0.5, 1, 1.5, 2, 4].map((r) => <option key={r} value={r}>{r}x</option>)}
        </select>
      </div>
      <div className="chips" role="group" aria-label="Overlay options">
        {chips.map(([k, label]) => (
          <button key={k} className={`chip ${opts[k] ? "on" : ""}`} aria-pressed={!!opts[k]} onClick={() => toggle(k)}>
            {opts[k] && <Icon name="check" size={14} />}{label}
          </button>
        ))}
      </div>
    </div>
  );
}
