"""Per-track state machine, timers and EMAs (blueprint section 5.5).

NORMAL --(phone_ema > enter)--> PHONE_SUSPECTED --(cumulative threshold_s in window_s)--> ALERT
   ^                                  |
   +--------(phone_ema < exit)--------+          (cooldown handled by the engine)
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from ..perception.posture import head_down, is_lying, is_seated, motion, shoulder_width
from ..types import Observation

NORMAL, PHONE_SUSPECTED = "NORMAL", "PHONE_SUSPECTED"


@dataclass
class TrackState:
    track_id: int
    first_seen: float
    last_seen: float
    role: str = "adult"
    p_child: float = 0.0
    zones: set = field(default_factory=set)
    xyxy: np.ndarray | None = None
    kpts: np.ndarray | None = None

    phone_score: float = 0.0
    phone_ema: float = 0.0
    phone_state: str = NORMAL
    phone_samples: deque = field(default_factory=deque)   # (t, dt, active)
    phone_t: float = 0.0          # seconds on phone within the sliding window
    out_t: float = 0.0            # adult: seconds continuously outside the play zone
    lying_t: float = 0.0          # child: seconds lying outside the nap zone
    idle_t: float = 0.0           # adult: seconds seated, head down, not moving
    motion_ema: float | None = None
    lying: bool = False
    seated: bool = False
    head_down: bool = False
    actions: dict = field(default_factory=dict)
    alerted: dict = field(default_factory=dict)            # rule -> time last fired

    def update(self, o: Observation, now: float, dt: float, cfg: dict) -> None:
        rules, pc = cfg["rules"], cfg.get("phone", {})
        sw = shoulder_width(o.kpts, o.xyxy)
        m = motion(self.kpts, o.kpts, sw)
        if m is not None:
            self.motion_ema = m if self.motion_ema is None else 0.8 * self.motion_ema + 0.2 * m
        self.lying = bool(is_lying(o.xyxy, o.kpts))
        self.seated = bool(is_seated(o.kpts))
        self.head_down = bool(head_down(o.kpts, sw))
        self.actions = o.actions or {}
        self.xyxy, self.kpts = o.xyxy, o.kpts
        self.role, self.p_child, self.zones = o.role, o.p_child, set(o.zones)
        self.last_seen = now
        adult = self.role == "adult"

        # R1: phone score -> EMA -> hysteresis -> cumulative time in a sliding window
        r1 = rules.get("R1_phone_use", {})
        score = max(o.phone_score, self.actions.get("phone_use", 0.0))
        self.phone_score = score
        a = pc.get("ema_alpha", 0.2)
        self.phone_ema = (1 - a) * self.phone_ema + a * score
        if self.phone_state == NORMAL and self.phone_ema > r1.get("enter", 0.6):
            self.phone_state = PHONE_SUSPECTED
        elif self.phone_state == PHONE_SUSPECTED and self.phone_ema < r1.get("exit", 0.4):
            self.phone_state = NORMAL
        self.phone_samples.append((now, dt, adult and self.phone_state == PHONE_SUSPECTED))
        window = r1.get("window_s", 1.25 * r1.get("threshold_s", 120))
        while self.phone_samples and self.phone_samples[0][0] < now - window:
            self.phone_samples.popleft()
        self.phone_t = sum(d for _, d, act in self.phone_samples if act)

        # R2: adult outside the play zone
        self.out_t = self.out_t + dt if adult and "play" not in self.zones else 0.0

        # R5: child lying outside the nap zone (pose heuristic or action model)
        r5 = rules.get("R5_fall", {})
        fall_p = self.actions.get("fall", 0.0)
        down = self.lying or (r5.get("model") == "pose_gru" and fall_p >= r5.get("min_conf", 0.7))
        if not adult and down and "nap" not in self.zones:
            self.lying_t += dt
        else:
            self.lying_t = max(0.0, self.lying_t - 2 * dt)

        # R6: adult seated, head down, near-zero motion
        r6 = rules.get("R6_idle", {})
        still = self.motion_ema is not None and self.motion_ema < r6.get("max_motion", 0.03)
        idle = (self.seated and self.head_down and still) or self.actions.get("idle_sitting", 0.0) >= 0.8
        self.idle_t = self.idle_t + dt if adult and idle else 0.0

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id, "role": self.role, "p_child": round(self.p_child, 2),
            "zones": sorted(self.zones), "phone_ema": round(self.phone_ema, 2),
            "phone_state": self.phone_state, "phone_t": round(self.phone_t, 1),
            "out_t": round(self.out_t, 1), "lying_t": round(self.lying_t, 1), "idle_t": round(self.idle_t, 1),
            "lying": bool(self.lying), "seated": bool(self.seated), "head_down": bool(self.head_down),
            "box": [round(float(v)) for v in self.xyxy] if self.xyxy is not None else None,
        }
