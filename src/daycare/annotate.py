"""Drawing: zones, tracks, skeletons, phones, HUD, alert banner, and face blurring for snapshots."""
from __future__ import annotations

import time

import cv2
import numpy as np

from .perception.posture import kp, shoulder_width
from .types import KPT_CONF, SKELETON, Person

BGR = {
    "adult": (230, 150, 40), "child": (70, 190, 70), "alert": (50, 50, 235), "phone": (0, 215, 255),
    "play": (80, 190, 80), "nap": (200, 120, 170), "exit": (30, 140, 245), "staff_only": (70, 70, 210),
}
PRIORITY_BGR = {"critical": (40, 40, 220), "high": (0, 120, 245), "medium": (0, 190, 230), "low": (160, 160, 160)}
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _text(img, s, org, scale=0.5, color=(255, 255, 255), bg=None, th=1):
    (w, h), base = cv2.getTextSize(s, FONT, scale, th)
    x, y = int(org[0]), int(org[1])
    if bg is not None:
        cv2.rectangle(img, (x - 3, y - h - 4), (x + w + 3, y + base), bg, -1)
    cv2.putText(img, s, (x, y), FONT, scale, color, th, cv2.LINE_AA)
    return w


def draw_zones(img, zones) -> None:
    h, w = img.shape[:2]
    overlay = img.copy()
    for z, poly in zones.polygons_px(w, h):
        cv2.fillPoly(overlay, [poly], BGR.get(z["type"], (200, 200, 200)))
    cv2.addWeighted(overlay, 0.13, img, 0.87, 0, img)
    for z, poly in zones.polygons_px(w, h):
        c = BGR.get(z["type"], (200, 200, 200))
        cv2.polylines(img, [poly], True, c, 2, cv2.LINE_AA)
        x, y = poly.min(axis=0)
        _text(img, z["name"], (x + 6, y + 18), 0.5, (255, 255, 255), c)


def blur_faces(img: np.ndarray, persons: list[Person]) -> np.ndarray:
    """Blur each person's head region (from face keypoints, or the top of the box)."""
    out = img.copy()
    h, w = img.shape[:2]
    for p in persons:
        pts = [q for q in (kp(p.kpts, i) for i in range(5)) if q is not None]
        sw = shoulder_width(p.kpts, p.xyxy)
        if pts:
            c = np.mean(pts, axis=0)
            spread = max(np.ptp([q[0] for q in pts]), np.ptp([q[1] for q in pts])) if len(pts) > 1 else 0
            r = max(0.45 * sw, 0.9 * spread, 8)
        else:
            x1, y1, x2, y2 = p.xyxy
            c = np.array([(x1 + x2) / 2, y1 + 0.1 * (y2 - y1)])
            r = max(0.25 * (x2 - x1), 8)
        x1, y1 = int(max(c[0] - r, 0)), int(max(c[1] - r * 1.2, 0))
        x2, y2 = int(min(c[0] + r, w)), int(min(c[1] + r, h))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        roi = out[y1:y2, x1:x2]
        k = max(3, (int(r) // 2) * 2 + 1)
        blurred = cv2.GaussianBlur(roi, (k, k), 0)
        mask = np.zeros(roi.shape[:2], np.uint8)
        cv2.ellipse(mask, ((x2 - x1) // 2, (y2 - y1) // 2), ((x2 - x1) // 2, (y2 - y1) // 2), 0, 0, 360, 255, -1)
        roi[mask > 0] = blurred[mask > 0]
    return out


def draw_tracks(img, analyzer, now: float, show_pose: bool = True) -> None:
    eng = analyzer.engine
    for p in analyzer.persons:
        s = eng.tracks.get(p.track_id)
        if s is None:
            continue
        alerting = any(now - t < 8 for t in s.alerted.values())
        c = BGR["alert"] if alerting else BGR[s.role]
        x1, y1, x2, y2 = p.xyxy.astype(int)
        cv2.rectangle(img, (x1, y1), (x2, y2), c, 3 if alerting else 2, cv2.LINE_AA)
        if show_pose and p.kpts is not None:
            for i, j in SKELETON:
                if p.kpts[i, 2] >= KPT_CONF and p.kpts[j, 2] >= KPT_CONF:
                    cv2.line(img, tuple(p.kpts[i, :2].astype(int)), tuple(p.kpts[j, :2].astype(int)),
                             (255, 255, 255), 1, cv2.LINE_AA)
        tags = [f"#{p.track_id} {s.role}"]
        if s.role == "adult":
            if s.phone_state == "PHONE_SUSPECTED" or s.phone_t > 0:
                tags.append(f"phone {s.phone_t:.0f}s")
            if s.out_t > 0:
                tags.append(f"out {s.out_t:.0f}s")
            if s.idle_t > 0:
                tags.append(f"idle {s.idle_t:.0f}s")
        elif s.lying_t > 0:
            tags.append(f"down {s.lying_t:.0f}s")
        _text(img, " | ".join(tags), (x1, max(y1 - 6, 14)), 0.45, (255, 255, 255), c)
    for ph in analyzer.phones:
        x1, y1, x2, y2 = ph.xyxy.astype(int)
        cv2.rectangle(img, (x1, y1), (x2, y2), BGR["phone"], 2)
        _text(img, f"phone {ph.conf:.2f}", (x1, y2 + 14), 0.4, (0, 0, 0), BGR["phone"])


def draw_hud(img, analyzer, now: float, fps: float, source_label: str) -> None:
    eng = analyzer.engine
    c = eng.counts
    lines = [
        f"{analyzer.camera_id} | {analyzer.room} | {eng.local_dt(now):%H:%M:%S} | {fps:4.1f} fps | {source_label}",
        f"Children in zone: {c['children']}   Adults in zone: {c['adults']}   "
        f"Ratio: {c['ratio'] if c['ratio'] is not None else '--'}",
    ]
    rules = eng.cfg["rules"]
    for key, val, label in [("R3_unattended", eng.unattended_t, "Unattended"), ("R4_ratio", eng.ratio_t, "Ratio over")]:
        if val > 0:
            lines.append(f"{label}: {val:4.1f}s / {rules[key]['threshold_s']}s")
    if not eng.active:
        lines.append("Outside active hours - monitoring paused")
    pad, lh = 8, 20
    width = max(cv2.getTextSize(s, FONT, 0.5, 1)[0][0] for s in lines) + 2 * pad
    overlay = img.copy()
    cv2.rectangle(overlay, (8, 8), (8 + width, 8 + pad + lh * len(lines)), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.6, img, 0.4, 0, img)
    for i, s in enumerate(lines):
        _text(img, s, (8 + pad, 8 + pad + 12 + i * lh), 0.5)


def draw_banner(img, analyzer, now: float) -> None:
    recent = [e for e in analyzer.engine.recent if now - e["ts"] < 6]
    if not recent:
        return
    e = max(recent, key=lambda e: ["low", "medium", "high", "critical"].index(e["priority"]))
    h, w = img.shape[:2]
    cv2.rectangle(img, (0, h - 44), (w, h), PRIORITY_BGR.get(e["priority"], (0, 0, 200)), -1)
    _text(img, f"ALERT  {e['priority'].upper()}  {e['rule'].split('_')[0]}  {e['title']}  ({e['duration_s']}s)",
          (16, h - 15), 0.75, (255, 255, 255), None, 2)


def render(frame, analyzer, now: float, fps: float = 0.0, source_label: str = "",
           blur: bool = False, show_pose: bool = True) -> np.ndarray:
    img = blur_faces(frame, analyzer.persons) if blur else frame.copy()
    draw_zones(img, analyzer.zones)
    draw_tracks(img, analyzer, now, show_pose)
    draw_hud(img, analyzer, now or time.time(), fps, source_label)
    draw_banner(img, analyzer, now)
    return img
