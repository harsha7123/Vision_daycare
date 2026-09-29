import { useEffect, useRef, useState } from "react";
import { PRIORITY, RULES, fmtDur } from "../lib/constants";
import { contentRect, drawDraft, drawOverlay, drawZones, hitTest } from "../lib/overlay";

/**
 * Video + analysis overlay canvas. In `editor.active` mode, clicks add polygon points (zone drawing);
 * otherwise clicks on a person select that track.
 */
export default function VideoStage({ src, videoRef, result, opts, editor, onPoint, onFinishShape, selected, onSelect, banner, roleOverrides, onVideoError, children }) {
  const canvasRef = useRef(null);
  const [hover, setHover] = useState(null);
  const live = useRef({});
  live.current = { result, opts, editor, selected, roleOverrides, hover };

  // keep the canvas pixel size equal to the video element size
  useEffect(() => {
    const v = videoRef.current, c = canvasRef.current;
    if (!v || !c) return;
    const fit = () => {
      const dpr = window.devicePixelRatio || 1;
      c.width = Math.round(v.clientWidth * dpr);
      c.height = Math.round(v.clientHeight * dpr);
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(v);
    return () => ro.disconnect();
  }, [videoRef, src]);

  // draw loop
  useEffect(() => {
    let raf;
    const tick = () => {
      const v = videoRef.current, c = canvasRef.current;
      if (v && c) {
        const ctx = c.getContext("2d");
        const s = live.current;
        const dpr = window.devicePixelRatio || 1;
        const scale = dpr * Math.max(0.85, Math.min(1.4, c.width / dpr / 900));
        if (s.editor?.active) {
          ctx.clearRect(0, 0, c.width, c.height);
          const r = contentRect(v, c);
          drawZones(ctx, s.editor.zones, r, scale, { editing: true });
          drawDraft(ctx, s.editor.draft, r, scale, s.hover);
        } else {
          drawOverlay(ctx, v, c, s.result, v.currentTime, { ...s.opts, selected: s.selected, roleOverrides: s.roleOverrides });
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videoRef]);

  const toNorm = (e) => {
    const v = videoRef.current, c = canvasRef.current;
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const r = contentRect(v, c);
    const x = ((e.clientX - rect.left) * dpr - r.x) / r.w;
    const y = ((e.clientY - rect.top) * dpr - r.y) / r.h;
    return x >= 0 && x <= 1 && y >= 0 && y <= 1 ? [x, y] : null;
  };

  const onClick = (e) => {
    const p = toNorm(e);
    if (!p) return;
    if (editor?.active) {
      if (editor.draft) onPoint?.(p);
      return;
    }
    const tid = hitTest(result, videoRef.current.currentTime, p[0], p[1]);
    onSelect?.(tid);
  };

  const b = banner && PRIORITY[banner.priority];
  return (
    <div className={`stage ${editor?.active ? "editing" : ""}`}>
      <video ref={videoRef} src={src} playsInline muted preload="auto" crossOrigin="anonymous" onError={onVideoError} />
      <canvas
        ref={canvasRef}
        className="overlay"
        onClick={onClick}
        onDoubleClick={() => editor?.active && onFinishShape?.()}
        onMouseMove={(e) => editor?.active && editor.draft && setHover(toNorm(e))}
        onMouseLeave={() => setHover(null)}
        aria-label={editor?.active ? "Click to add zone points" : "Analysed video. Click a person to select them"}
      />
      {banner && (
        <div className="banner" style={{ background: b.color }} role="alert">
          <span className="banner-prio">{b.label}</span>
          <span className="banner-title">{RULES[banner.rule]?.title || banner.title}</span>
          <span className="banner-meta">for {fmtDur(banner.duration_s)}{banner.track_id != null ? ` · person #${banner.track_id}` : ""}</span>
        </div>
      )}
      {editor?.active && (
        <div className="edit-hint">
          {editor.draft ? `Click to add points to "${editor.draft.name}". Double-click or press Finish shape to close it.` : "Pick a zone type below, then click on the video to draw it."}
        </div>
      )}
      {children}
    </div>
  );
}
