"""Adult vs child (blueprint section 5.2).

Per frame we estimate p(child) by combining whatever signals are available:
  * crop classifier (YOLO-cls trained on your cameras)     weight 0.6
  * height vs calibrated adult height at that floor spot     weight 0.3
  * head-to-torso keypoint proportion (children: big heads)  weight 0.2
then take a rolling vote per track with hysteresis so roles don't flicker.
Staff can be enrolled manually from the dashboard (override always wins).
"""
from __future__ import annotations

import math
from collections import deque
from pathlib import Path

import numpy as np

from ..types import NOSE, Person
from .posture import is_lying, is_seated, kp, torso

PRIOR_P_CHILD = 0.35   # no usable signal: lean adult (a missed adult is what causes false R3 alerts)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class CropClassifier:
    """Optional YOLO-cls model with classes {adult, child}."""

    def __init__(self, weights: str | Path, device: str = "cpu"):
        from ultralytics import YOLO
        self.model = YOLO(str(weights))
        self.device = device
        names = {v.lower(): k for k, v in self.model.names.items()}
        if "child" not in names:
            raise ValueError(f"role classifier must have a 'child' class, got {self.model.names}")
        self.child_idx = names["child"]

    def __call__(self, frame: np.ndarray, xyxy) -> float | None:
        x1, y1, x2, y2 = [int(v) for v in xyxy]
        crop = frame[max(y1, 0):y2, max(x1, 0):x2]
        if crop.size == 0:
            return None
        r = self.model(crop, imgsz=224, device=self.device, verbose=False)[0]
        return float(r.probs.data[self.child_idx])


class RoleResolver:
    def __init__(self, cfg: dict | None = None, classifier: CropClassifier | None = None,
                 vote_window: int = 20, recheck_s: float = 30.0):
        cfg = cfg or {}
        self.mode = cfg.get("mode", "auto")
        self.child_ratio = float(cfg.get("child_height_ratio", 0.7))
        self.calib = cfg.get("calibration")
        self.classifier = classifier
        self.recheck_s = recheck_s
        self.votes: dict[int, deque] = {}
        self.roles: dict[int, str] = {}
        self.overrides: dict[int, str] = {}
        self._cls_cache: dict[int, tuple[float, float]] = {}   # tid -> (time, p)
        self.vote_window = vote_window

    # ---- signals -------------------------------------------------------------------------
    def adult_height_at(self, feet_y: float) -> float | None:
        """Expected standing-adult box height (fraction of frame) at normalised feet y."""
        if not self.calib:
            return None
        (y0, h0), (y1, h1) = self.calib["far"], self.calib["near"]
        if y1 == y0:
            return h1
        return max(h0 + (feet_y - y0) * (h1 - h0) / (y1 - y0), 1e-3)

    def height_signal(self, p: Person, frame_h: int) -> float | None:
        if is_seated(p.kpts):
            return None
        feet_y = p.xyxy[3] / frame_h
        if feet_y > 0.995:           # cut off by the bottom of the frame: height is meaningless
            return None
        ref = self.adult_height_at(feet_y)
        if ref is None:
            return None
        bh, bw = p.xyxy[3] - p.xyxy[1], p.xyxy[2] - p.xyxy[0]
        length = max(bh, bw) if is_lying(p.xyxy, p.kpts) else bh   # lying: body length is the box width
        ratio = length / frame_h / ref
        return _sigmoid((self.child_ratio - ratio) * 15)

    @staticmethod
    def proportion_signal(p: Person) -> float | None:
        t = torso(p.kpts)
        nose = kp(p.kpts, NOSE)
        if t is None or nose is None or is_lying(p.xyxy, p.kpts):
            return None
        torso_len = np.linalg.norm(t[0] - t[1])
        if torso_len < 5:
            return None
        r = np.linalg.norm(nose - t[0]) / torso_len
        return _sigmoid((r - 0.55) * 15)

    def p_child(self, p: Person, frame: np.ndarray, now: float) -> float | None:
        """Weighted p(child) from the signals available this frame, or None if there are none."""
        signals = []
        if self.classifier is not None:
            t, cp = self._cls_cache.get(p.track_id, (-1e9, None))
            n_votes = len(self.votes.get(p.track_id, ()))
            if n_votes < self.vote_window or now - t > self.recheck_s:
                cp = self.classifier(frame, p.xyxy)
                self._cls_cache[p.track_id] = (now, cp)
            if cp is not None:
                signals.append((cp, 0.6))
        h = self.height_signal(p, frame.shape[0])
        if h is not None:
            signals.append((h, 0.3))
        # head/torso proportions are unreliable on people cut off by the frame (close-up webcams)
        k = self.proportion_signal(p) if p.xyxy[3] / frame.shape[0] < 0.97 else None
        if k is not None:
            signals.append((k, 0.2))
        if not signals:
            return None
        return sum(v * w for v, w in signals) / sum(w for _, w in signals)

    # ---- per-track vote --------------------------------------------------------------------
    def resolve(self, p: Person, frame: np.ndarray, now: float) -> tuple[str, float]:
        tid = p.track_id
        if tid in self.overrides:
            role = self.overrides[tid]
            return role, 1.0 if role == "child" else 0.0
        if self.mode == "all_adult":
            return "adult", 0.0
        if self.mode == "all_child":
            return "child", 1.0
        v = self.votes.setdefault(tid, deque(maxlen=self.vote_window))
        pc = self.p_child(p, frame, now)
        if pc is not None:           # frames without any usable signal don't vote
            v.append(pc)
        mean = float(np.mean(v)) if v else PRIOR_P_CHILD
        prev = self.roles.get(tid)
        if prev is None:
            role = "child" if mean > 0.5 else "adult"
        elif prev == "adult" and mean > 0.6:
            role = "child"
        elif prev == "child" and mean < 0.4:
            role = "adult"
        else:
            role = prev
        self.roles[tid] = role
        return role, mean

    def set_override(self, tid: int, role: str | None) -> None:
        if role in (None, "auto"):
            self.overrides.pop(tid, None)
        elif role in ("adult", "child"):
            self.overrides[tid] = role
        else:
            raise ValueError("role must be adult, child or auto")

    def forget(self, tids) -> None:
        for t in tids:
            for d in (self.votes, self.roles, self._cls_cache):
                d.pop(t, None)
