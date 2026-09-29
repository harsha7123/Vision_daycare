import { useMemo, useRef, useState } from "react";
import { PRIORITY, RULES, ROLE, fmtTime } from "../lib/constants";

const W = 1000, H = 64, TOP = 8, BASE = 50;

/** Seekable timeline: children / adults in the play area over time, plus one marker per alert. */
export default function Timeline({ result, duration, time, onSeek, focusId }) {
  const ref = useRef(null);
  const [hover, setHover] = useState(null);
  const dur = duration || result?.video.duration || 1;

  const paths = useMemo(() => {
    if (!result?.frames.length) return null;
    const max = Math.max(1, ...result.frames.map((f) => Math.max(f.c[0], f.c[1])));
    const y = (v) => BASE - (v / max) * (BASE - TOP);
    const line = (idx) => result.frames.map((f, i) => `${i ? "L" : "M"}${((f.t / dur) * W).toFixed(1)},${y(f.c[idx]).toFixed(1)}`).join("");
    const kids = line(0);
    return { kids, kidsArea: `${kids}L${W},${BASE}L0,${BASE}Z`, adults: line(1), max };
  }, [result, dur]);

  const seekFromEvent = (e) => {
    const rect = ref.current.getBoundingClientRect();
    const x = Math.min(Math.max((e.clientX - rect.left) / rect.width, 0), 1);
    return x * dur;
  };

  return (
    <div className="timeline">
      <div className="timeline-legend">
        <span><i style={{ background: ROLE.child.color }} /> children in play area</span>
        <span><i style={{ background: ROLE.adult.color }} /> adults in play area</span>
        {paths && <span className="muted">scale 0-{paths.max}</span>}
      </div>
      <div
        className="timeline-track"
        ref={ref}
        onClick={(e) => onSeek(seekFromEvent(e))}
        onMouseMove={(e) => setHover(seekFromEvent(e))}
        onMouseLeave={() => setHover(null)}
        role="slider"
        aria-label="Video timeline"
        aria-valuemin={0}
        aria-valuemax={Math.round(dur)}
        aria-valuenow={Math.round(time)}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "ArrowRight") onSeek(Math.min(dur, time + 5));
          if (e.key === "ArrowLeft") onSeek(Math.max(0, time - 5));
        }}
      >
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
          <rect x="0" y={TOP} width={W} height={BASE - TOP} fill="#F3F5F7" />
          {paths && (
            <>
              <path d={paths.kidsArea} fill={ROLE.child.color} opacity="0.12" />
              <path d={paths.kids} fill="none" stroke={ROLE.child.color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
              <path d={paths.adults} fill="none" stroke={ROLE.adult.color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
            </>
          )}
          <rect x="0" y={BASE + 2} width={(time / dur) * W} height="4" fill="#1565C0" />
          <rect x={(time / dur) * W} y={BASE + 2} width={W - (time / dur) * W} height="4" fill="#D5DBE1" />
          <line x1={(time / dur) * W} x2={(time / dur) * W} y1="0" y2={H} stroke="#1F2328" strokeWidth="2" vectorEffect="non-scaling-stroke" />
          {hover != null && <line x1={(hover / dur) * W} x2={(hover / dur) * W} y1="0" y2={H} stroke="#1F2328" strokeOpacity="0.25" vectorEffect="non-scaling-stroke" />}
        </svg>
        {result?.events.map((e) => (
          <button
            key={e.event_id}
            className={`marker ${focusId === e.event_id ? "focus" : ""}`}
            style={{ left: `${(e.t / dur) * 100}%`, background: PRIORITY[e.priority].color }}
            title={`${fmtTime(e.t)} · ${PRIORITY[e.priority].label} · ${RULES[e.rule]?.title}`}
            aria-label={`${RULES[e.rule]?.title} at ${fmtTime(e.t)}`}
            onClick={(ev) => { ev.stopPropagation(); onSeek(Math.max(0, e.t - 3), e); }}
          >
            {RULES[e.rule]?.code}
          </button>
        ))}
        {hover != null && <div className="hover-time" style={{ left: `${(hover / dur) * 100}%` }}>{fmtTime(hover)}</div>}
      </div>
    </div>
  );
}
