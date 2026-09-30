import { ALERT_COLOR, PHONE_COLOR, ROLE, ZONES } from "./constants";

// COCO-17 skeleton (keypoints are flattened x,y pairs)
const BONES = [[5, 6], [5, 7], [7, 9], [6, 8], [8, 10], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14], [14, 16], [0, 1], [0, 2], [1, 3], [2, 4]];

/** Where the video picture sits inside the element (object-fit: contain), in canvas pixels. */
export function contentRect(video, canvas) {
  const vw = video.videoWidth || 16, vh = video.videoHeight || 9;
  const cw = canvas.width, ch = canvas.height;
  const s = Math.min(cw / vw, ch / vh);
  return { x: (cw - vw * s) / 2, y: (ch - vh * s) / 2, w: vw * s, h: vh * s };
}

/** Last analysed frame at or before t (binary search), or null if too far away. */
export function frameAt(frames, t, sampleFps) {
  if (!frames?.length) return null;
  let lo = 0, hi = frames.length - 1;
  if (t < frames[0].t) return null;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (frames[mid].t <= t) lo = mid; else hi = mid - 1;
  }
  const f = frames[lo];
  return t - f.t <= 2.5 / (sampleFps || 6) ? f : null;
}

function pill(ctx, text, x, y, bg, scale) {
  ctx.font = `600 ${12 * scale}px Inter, system-ui, sans-serif`;
  const w = ctx.measureText(text).width + 10 * scale;
  const h = 18 * scale;
  const top = Math.max(y - h - 2 * scale, 0);
  ctx.fillStyle = bg;
  ctx.beginPath();
  ctx.roundRect(x, top, w, h, 4 * scale);
  ctx.fill();
  ctx.fillStyle = "#fff";
  ctx.fillText(text, x + 5 * scale, top + 13 * scale);
}

export function drawZones(ctx, zones, r, scale, { editing = false } = {}) {
  for (const z of zones || []) {
    const c = ZONES[z.type]?.color || "#607D8B";
    ctx.beginPath();
    z.polygon.forEach(([x, y], i) => (i ? ctx.lineTo(r.x + x * r.w, r.y + y * r.h) : ctx.moveTo(r.x + x * r.w, r.y + y * r.h)));
    ctx.closePath();
    ctx.fillStyle = c + (editing ? "33" : "1f");
    ctx.fill();
    ctx.lineWidth = 2 * scale;
    ctx.setLineDash(editing ? [] : [6 * scale, 4 * scale]);
    ctx.strokeStyle = c;
    ctx.stroke();
    ctx.setLineDash([]);
    const [x0, y0] = z.polygon[0];
    pill(ctx, z.name || ZONES[z.type]?.label, r.x + x0 * r.w + 4 * scale, r.y + y0 * r.h + 22 * scale, c, scale);
    if (editing) {
      for (const [x, y] of z.polygon) {
        ctx.fillStyle = "#fff";
        ctx.fillRect(r.x + x * r.w - 3 * scale, r.y + y * r.h - 3 * scale, 6 * scale, 6 * scale);
        ctx.strokeRect(r.x + x * r.w - 3 * scale, r.y + y * r.h - 3 * scale, 6 * scale, 6 * scale);
      }
    }
  }
}

export function drawDraft(ctx, draft, r, scale, hover) {
  if (!draft?.polygon.length) return;
  const c = ZONES[draft.type]?.color || "#1565C0";
  const pts = draft.polygon.map(([x, y]) => [r.x + x * r.w, r.y + y * r.h]);
  if (hover) pts.push([r.x + hover[0] * r.w, r.y + hover[1] * r.h]);
  ctx.beginPath();
  pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  ctx.strokeStyle = c;
  ctx.lineWidth = 2.5 * scale;
  ctx.setLineDash([5 * scale, 4 * scale]);
  ctx.stroke();
  ctx.setLineDash([]);
  for (const [x, y] of pts.slice(0, draft.polygon.length)) {
    ctx.beginPath();
    ctx.arc(x, y, 5 * scale, 0, Math.PI * 2);
    ctx.fillStyle = c;
    ctx.fill();
  }
}

function blurHead(ctx, video, r, k, box, sx, sy) {
  const face = [];
  for (let i = 0; i < 5; i++) {
    const x = k?.[2 * i], y = k?.[2 * i + 1];
    if (x > 0 || y > 0) face.push([x, y]);
  }
  let cx, cy, rad;
  const bw = (box[2] - box[0]) * sx;
  if (face.length >= 2) {
    cx = face.reduce((a, p) => a + p[0], 0) / face.length * sx;
    cy = face.reduce((a, p) => a + p[1], 0) / face.length * sy;
    const spread = Math.max(...face.map((p) => Math.hypot(p[0] * sx - cx, p[1] * sy - cy)));
    rad = Math.max(spread * 1.9, bw * 0.16, 6);
  } else {
    cx = (box[0] + box[2]) / 2 * sx;
    cy = box[1] * sy + (box[3] - box[1]) * sy * 0.1;
    rad = Math.max(bw * 0.22, 6);
  }
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(r.x + cx, r.y + cy, rad, rad * 1.15, 0, 0, Math.PI * 2);
  ctx.clip();
  ctx.filter = `blur(${Math.max(6, rad / 3)}px)`;
  ctx.drawImage(video, r.x, r.y, r.w, r.h);
  ctx.restore();
}

/** Draw the analysis for time t over the video. `result.video.width` is the coordinate space. */
export function drawOverlay(ctx, video, canvas, result, t, opts) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!result) return;
  const r = contentRect(video, canvas);
  const dpr = window.devicePixelRatio || 1;
  const scale = dpr * Math.max(0.85, Math.min(1.4, r.w / dpr / 900));
  if (opts.zones) drawZones(ctx, result.zones, r, scale);
  const f = frameAt(result.frames, t, result.video.sample_fps);
  if (f) drawFrame(ctx, video, r, scale, f, result.video.width, result.video.height, opts);
}

/** Draw one analysed frame (boxes, skeletons, labels, phones, face blur) into content rect r. */
export function drawFrame(ctx, video, r, scale, f, width, height, opts) {
  const sx = r.w / width, sy = r.h / height;
  const roleOf = opts.roleOverrides || {};

  f.p.forEach((p, i) => {
    const [tid, x1, y1, x2, y2, rc, , phoneT, outT, lyingT, idleT, alert] = p;
    const role = roleOf[tid] || (rc === "c" ? "child" : "adult");
    const k = f.k?.[i];
    if (opts.blur) blurHead(ctx, video, r, k, [x1, y1, x2, y2], sx, sy);
    const color = alert ? ALERT_COLOR : ROLE[role].color;
    const selected = opts.selected === tid;
    ctx.lineWidth = (alert || selected ? 3.5 : 2.2) * scale;
    ctx.strokeStyle = color;
    ctx.strokeRect(r.x + x1 * sx, r.y + y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
    if (opts.pose && k?.length === 34) {
      ctx.strokeStyle = "rgba(255,255,255,0.85)";
      ctx.lineWidth = 1.6 * scale;
      for (const [a, b] of BONES) {
        const ax = k[2 * a], ay = k[2 * a + 1], bx = k[2 * b], by = k[2 * b + 1];
        if ((ax || ay) && (bx || by)) {
          ctx.beginPath();
          ctx.moveTo(r.x + ax * sx, r.y + ay * sy);
          ctx.lineTo(r.x + bx * sx, r.y + by * sy);
          ctx.stroke();
        }
      }
    }
    const tags = [`#${tid} ${ROLE[role].label}`];
    if (role === "adult") {
      if (phoneT > 0) tags.push(`phone ${Math.round(phoneT)}s`);
      if (outT > 0) tags.push(`away ${Math.round(outT)}s`);
      if (idleT > 0) tags.push(`idle ${Math.round(idleT)}s`);
    } else if (lyingT > 0) tags.push(`down ${Math.round(lyingT)}s`);
    pill(ctx, tags.join(" · "), r.x + x1 * sx, r.y + y1 * sy, color, scale);
  });

  if (opts.phones) {
    for (const [x1, y1, x2, y2, conf] of f.ph) {
      ctx.lineWidth = 2 * scale;
      ctx.strokeStyle = PHONE_COLOR;
      ctx.strokeRect(r.x + x1 * sx, r.y + y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy);
      if (conf >= 0.4) pill(ctx, `phone ${Math.round(conf * 100)}%`, r.x + x1 * sx, r.y + y2 * sy + 20 * scale, "#8D6E00", scale * 0.85);
    }
  }
}

/** Track id under a click (normalised video coords), for selecting people. */
export function hitTest(result, t, nx, ny) {
  const f = frameAt(result?.frames, t, result?.video.sample_fps);
  if (!f) return null;
  const x = nx * result.video.width, y = ny * result.video.height;
  const hits = f.p.filter(([, x1, y1, x2, y2]) => x >= x1 && x <= x2 && y >= y1 && y <= y2);
  hits.sort((a, b) => (a[3] - a[1]) * (a[4] - a[2]) - (b[3] - b[1]) * (b[4] - b[2]));
  return hits[0]?.[0] ?? null;
}
