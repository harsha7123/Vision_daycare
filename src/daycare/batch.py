"""Offline analysis of an uploaded video (web / cloud mode).

Pass 1  detect   YOLO pose + phone detector + ByteTrack over frames sampled at `sample_fps` (slow, done once)
         calibrate  fit the standing-adult height vs floor position from the video itself
Pass 2  analyze  roles, phone association, zones and R1..R6 over the cached detections (fast, re-runnable
                 when the user changes zones, roles or thresholds)
Pass 3  snapshots  face-blurred, annotated JPEG per event (seeks the video)

The result is JSON the browser overlays on the original video: per-frame boxes, keypoints and
state, the event list with video timestamps, and a per-track summary.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .analyzer import Analyzer
from .annotate import BGR, PRIORITY_BGR, _text, blur_faces, draw_zones
from .config import deep_merge, load_rules
from .perception.posture import is_lying, is_seated
from .perception.zones import ZoneSet
from .types import Person, Phone

TUNABLE = {"enabled", "threshold_s", "window_s", "cooldown_s", "enter", "exit",
           "max_children_per_adult", "max_motion", "min_conf"}

# thresholds for short internet clips; "demo" = rules.demo.yaml; "production" = rules.yaml
PRESETS: dict[str, dict] = {
    "quick": {
        "R1_phone_use": {"threshold_s": 6, "window_s": 9, "cooldown_s": 30},
        "R2_left_zone": {"threshold_s": 8, "cooldown_s": 30},
        "R3_unattended": {"threshold_s": 4, "cooldown_s": 30},
        "R4_ratio": {"max_children_per_adult": 4, "threshold_s": 5, "cooldown_s": 30},
        "R5_fall": {"threshold_s": 2, "cooldown_s": 30},
        "R6_idle": {"enabled": True, "threshold_s": 12, "cooldown_s": 60},
    },
    "demo": None,        # filled from configs/rules.demo.yaml
    "production": {},
}


def build_rules(preset: str = "quick", overrides: dict | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {list(PRESETS)}")
    cfg = load_rules("demo" if preset == "demo" else "production")
    if PRESETS[preset]:
        cfg["rules"] = deep_merge(cfg["rules"], PRESETS[preset])
    for key, changes in (overrides or {}).items():
        if key not in cfg["rules"] or not isinstance(changes, dict):
            raise ValueError(f"unknown rule {key}")
        for k, v in changes.items():
            if k not in TUNABLE:
                raise ValueError(f"{key}.{k} is not tunable")
            if k == "enabled":
                cfg["rules"][key][k] = bool(v)
            else:
                v = float(v)
                if v < 0:
                    raise ValueError(f"{key}.{k} must be >= 0")
                cfg["rules"][key][k] = v
    r1 = cfg["rules"]["R1_phone_use"]
    r1["window_s"] = max(float(r1.get("window_s", 0)), float(r1["threshold_s"]) * 1.25)   # window must fit the threshold
    cfg["schedule"]["active_hours"] = None        # uploaded clips are analysed regardless of time of day
    cfg["schedule"]["nap_hours"] = None
    return cfg


@dataclass
class Detections:
    width: int
    height: int
    src_fps: float
    duration: float
    sample_fps: float
    frames: list[tuple[float, int, list[Person], list[Phone]]] = field(default_factory=list)  # (t, frame_idx, ...)


# ---- pass 1 ------------------------------------------------------------------------------------
def probe(path: str | Path) -> dict:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError("could not open the video (unsupported codec or corrupt file)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if w == 0 or h == 0:
        raise ValueError("video has no readable frames")
    return {"width": w, "height": h, "fps": fps, "frames": n, "duration": n / fps if n else 0.0}


def detect(path: str | Path, perception, sample_fps: float = 6.0, progress=None, cancelled=None,
           preview: str | Path | None = None, preview_width: int = 960) -> Detections:
    """preview: also write the sampled frames as a browser-playable VP8 WebM (for files the
    browser can't decode, e.g. HEVC or MPEG-4 Part 2). Frame k is at t = k / sample_fps, as in the result."""
    meta = probe(path)
    cap = cv2.VideoCapture(str(path))
    fps = meta["fps"]
    step = max(1, round(fps / sample_fps))
    perception.reset()
    det = Detections(meta["width"], meta["height"], fps, meta["duration"], fps / step)
    writer, size = None, None
    if preview:
        pw = min(preview_width, meta["width"]) // 2 * 2
        size = (pw, int(meta["height"] * pw / meta["width"]) // 2 * 2)
        writer = cv2.VideoWriter(str(preview), cv2.VideoWriter_fourcc(*"VP80"), fps / step, size)
    idx = 0
    while True:
        if cancelled and cancelled():
            break
        if not cap.grab():
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            if writer is not None:
                writer.write(cv2.resize(frame, size, interpolation=cv2.INTER_AREA))
            persons, phones = perception(frame)
            for p in persons:                       # compact storage
                if p.kpts is not None:
                    p.kpts = p.kpts.astype(np.float32)
            det.frames.append((idx / fps, idx, persons, phones))
            if progress and meta["frames"]:
                progress(min(idx / meta["frames"], 1.0))
        idx += 1
    cap.release()
    if writer is not None:
        writer.release()
    if not meta["duration"]:
        det.duration = idx / fps
    return det


def estimate_calibration(det: Detections) -> dict | None:
    """Upper envelope of standing-person box height vs feet position -> adult reference line."""
    ys, hs = [], []
    for _, _, persons, _ in det.frames:
        for p in persons:
            if is_seated(p.kpts) or is_lying(p.xyxy, p.kpts):
                continue
            feet, top = p.xyxy[3] / det.height, p.xyxy[1] / det.height
            if feet > 0.98 or top < 0.01:          # cut off by the frame: height unknown
                continue
            ys.append(feet)
            hs.append((p.xyxy[3] - p.xyxy[1]) / det.height)
    if len(ys) < 20:
        return None
    ys, hs = np.array(ys), np.array(hs)
    # Perspective: box height grows linearly with feet y, and adults and children share that line
    # up to a scale factor. Alternate: split log(height / line) into a tall and a short cluster,
    # rescale the short cluster onto the tall one, refit the line on everything.
    def fit(y, h):
        if np.ptp(y) < 0.05:
            return 0.0, float(np.median(h))
        a, b = np.polyfit(y, h, 1)
        return (a, b) if a >= 0 else (0.0, float(np.median(h)))

    a, b = fit(ys, hs)
    tall = np.ones(len(ys), bool)
    for _ in range(4):
        lr = np.log(hs / np.maximum(a * ys + b, 1e-3))
        lo, hi = np.quantile(lr, 0.2), np.quantile(lr, 0.9)
        for _ in range(10):                        # 1-D two-means
            tall = np.abs(lr - hi) < np.abs(lr - lo)
            if tall.all() or not tall.any():
                break
            lo, hi = lr[~tall].mean(), lr[tall].mean()
        if tall.all() or tall.sum() < 5 or hi - lo < np.log(1.25):
            tall[:] = True                         # one population (e.g. only adults in view)
            scaled = hs
        else:
            scaled = np.where(tall, hs, hs * np.exp(hi - lo))
        a, b = fit(ys, scaled)
    k = float(np.median(hs[tall] / np.maximum(a * ys[tall] + b, 1e-3)))
    ref = lambda y: float(max(k * (a * y + b), 0.03))
    y0, y1 = float(ys.min()), float(ys.max())
    return {"far": [y0, ref(y0)], "near": [max(y1, y0 + 1e-3), ref(y1)]}


# ---- pass 2 ------------------------------------------------------------------------------------
def analyze(det: Detections, rules_cfg: dict, zones: list[dict] | None = None, roles: dict | None = None,
            room: str = "Room 1", calibration: dict | None = None, start_ts: float | None = None,
            include_kpts: bool = True) -> dict:
    start_ts = start_ts or time.time()
    zs = ZoneSet(zones or [])
    role_cfg = {"mode": "auto", "child_height_ratio": 0.7, "calibration": calibration}
    an = Analyzer("upload", room, role_cfg, rules_cfg, zs)
    an.roles.overrides = {int(k): v for k, v in (roles or {}).items() if v in ("adult", "child")}
    stub = np.empty((det.height, det.width, 3), np.uint8)

    frames, events, tracks = [], [], {}
    for t, fidx, persons, phones in det.frames:
        evs = an.process(stub, persons, phones, start_ts + t)
        now = start_ts + t
        recs, kp = [], []
        for p in persons:
            s = an.engine.tracks.get(p.track_id)
            if s is None:
                continue
            alert = any(now - ts < 4 for ts in s.alerted.values())
            recs.append([p.track_id, *[int(v) for v in p.xyxy], "c" if s.role == "child" else "a",
                         round(s.phone_ema, 2), round(s.phone_t, 1), round(s.out_t, 1), round(s.lying_t, 1),
                         round(s.idle_t, 1), 1 if alert else 0])
            if include_kpts:
                kp.append(np.round(p.kpts[:, :2]).astype(int).ravel().tolist()
                          if p.kpts is not None else [])
            tr = tracks.setdefault(p.track_id, {"track_id": p.track_id, "first_t": t, "frames": 0, "p_sum": 0.0})
            tr.update(last_t=t, role=s.role)
            tr["frames"] += 1
            tr["p_sum"] += s.p_child
        c = an.engine.counts
        frames.append({"t": round(t, 3), "p": recs, "k": kp,
                       "ph": [[*[int(v) for v in ph.xyxy], round(ph.conf, 2)] for ph in phones],
                       "c": [c["children"], c["adults"]],
                       "u": round(an.engine.unattended_t, 1)})
        for e in evs:
            e["t"] = round(t, 2)
            e["frame_idx"] = fidx
            e["clip"] = [round(max(0.0, t - 10), 2), round(min(det.duration, t + 3), 2)]
            events.append(e)

    for tr in tracks.values():
        tr["p_child"] = round(tr.pop("p_sum") / max(tr["frames"], 1), 2)
        tr["override"] = an.roles.overrides.get(tr["track_id"])
        tr["first_t"], tr["last_t"] = round(tr["first_t"], 2), round(tr["last_t"], 2)

    by_prio: dict[str, int] = {}
    for e in events:
        by_prio[e["priority"]] = by_prio.get(e["priority"], 0) + 1
    counted = [tr for tr in tracks.values() if tr["frames"] >= 3]     # ignore flicker tracks
    summary = {
        "adults": sum(tr["role"] == "adult" for tr in counted),
        "children": sum(tr["role"] == "child" for tr in counted),
        "max_children_in_zone": max((f["c"][0] for f in frames), default=0),
        "max_adults_in_zone": max((f["c"][1] for f in frames), default=0),
        "events": len(events), "by_priority": by_prio,
        "worst": next((p for p in ("critical", "high", "medium", "low") if by_prio.get(p)), None),
        "frames_with_people": round(sum(1 for f in frames if f["p"]) / max(len(frames), 1), 2),
    }
    # Tell the user when the AI could not see people, instead of a misleading "no issues found".
    if not counted:
        summary["warning"] = "no_people"
    elif summary["frames_with_people"] < 0.25:
        summary["warning"] = "few_people"
    return {
        "video": {"width": det.width, "height": det.height, "duration": round(det.duration, 2),
                  "sample_fps": round(det.sample_fps, 2), "src_fps": round(det.src_fps, 2)},
        "room": room, "zones": zs.to_dict()["zones"], "calibration": calibration,
        "rules": rules_cfg["rules"], "frames": frames, "events": events,
        "tracks": sorted(tracks.values(), key=lambda x: x["track_id"]), "summary": summary,
    }


# ---- pass 3 ------------------------------------------------------------------------------------
def render_snapshots(path: str | Path, det: Detections, result: dict, out_dir: Path, max_width: int = 960) -> None:
    """One face-blurred annotated JPEG per event, written as out_dir/<i>.jpg."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_idx = {fidx: (persons, phones) for _, fidx, persons, phones in det.frames}
    frame_rec = {round(f["t"], 3): f for f in result["frames"]}
    zs = ZoneSet(result["zones"])
    cap = cv2.VideoCapture(str(path))
    for i, e in enumerate(result["events"]):
        cap.set(cv2.CAP_PROP_POS_FRAMES, e["frame_idx"])
        ok, frame = cap.read()
        if not ok:
            continue
        persons, _ = by_idx.get(e["frame_idx"], ([], []))
        rec = {r[0]: r for r in frame_rec.get(round(e["t"], 3), {}).get("p", [])}
        img = blur_faces(frame, persons)
        draw_zones(img, zs)
        for p in persons:
            r = rec.get(p.track_id)
            role = "child" if r and r[5] == "c" else "adult"
            hot = e.get("track_id") == p.track_id or (e["track_id"] is None and role == "child")
            color = BGR["alert"] if hot else BGR[role]
            x1, y1, x2, y2 = p.xyxy.astype(int)
            th = max(2, img.shape[1] // 400)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, th + (1 if hot else 0))
            _text(img, f"#{p.track_id} {role}", (x1, max(y1 - 6, 14)), max(0.5, img.shape[1] / 1600), (255, 255, 255), color)
        h, w = img.shape[:2]
        bar = max(36, h // 16)
        cv2.rectangle(img, (0, h - bar), (w, h), PRIORITY_BGR.get(e["priority"], (0, 0, 200)), -1)
        _text(img, f"{e['priority'].upper()}  {e['title']}  -  {e['duration_s']}s  -  t={e['t']:.1f}s",
              (12, h - bar // 3), max(0.6, w / 1400), (255, 255, 255), None, 2)
        if w > max_width:
            img = cv2.resize(img, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out_dir / f"{i}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        e["snapshot_index"] = i
    cap.release()

