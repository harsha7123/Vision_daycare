"""Synthetic day-care scene for demos and replay tests.

Renders a room with scripted actors and emits the same Person / Phone objects the YOLO
perception stage would (tracked boxes + COCO-17 keypoints + phone boxes). Everything after
perception -- role voting, phone association, zones, rules, events, alerts -- runs for real.

The 150 s script (demo thresholds) walks through every rule:
  ~20 s  R1  Priya texts on her phone
  ~38 s  R4  Ravi goes to the kitchen: 5 children, 1 adult
  ~45 s  R2  Ravi still outside the play zone
  ~50 s  R3  Priya leaves through the door: children unattended
 ~105 s  R5  a child falls and stays down (the napping child on the mat never alerts)
 ~117 s  R6  Ravi sits head-down, motionless
A phone lying on the table (hard negative) never alerts.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .types import Person, Phone

W, H = 1280, 720
LOOP_S = 150.0
KP_CONF = 0.92

# proportions as fractions of the actor's height, measured up from the feet
BODY = {
    "adult": dict(an=.03, kn=.27, hip=.50, sh=.81, nose=.93, eye=.95, ear=.94, top=1.0,
                  shx=.12, hipx=.07, legx=.06, eyex=.025, earx=.05, head=.07),
    "child": dict(an=.03, kn=.22, hip=.42, sh=.70, nose=.85, eye=.88, ear=.87, top=1.0,
                  shx=.13, hipx=.08, legx=.06, eyex=.04, earx=.08, head=.13),
}

# (t0, t1, pose, from_xy, to_xy)   positions are normalised feet points
ACTORS = [
    dict(id=1, name="Priya", role="adult", shirt=(170, 120, 40), script=[
        (0, 8, "walk", (0.30, 0.62), (0.25, 0.55)),
        (8, 34, "phone", (0.25, 0.55), None),
        (34, 42, "walk", (0.25, 0.55), (0.46, 0.33)),
        (42, 78, "hidden", None, None),
        (78, 86, "walk", (0.46, 0.33), (0.35, 0.60)),
        (86, 150, "stand", (0.35, 0.60), None)]),
    dict(id=2, name="Ravi", role="adult", shirt=(60, 110, 190), script=[
        (0, 26, "stand", (0.55, 0.75), None),
        (26, 32, "walk", (0.55, 0.75), (0.84, 0.50)),
        (32, 62, "stand", (0.84, 0.50), None),
        (62, 70, "walk", (0.84, 0.50), (0.50, 0.70)),
        (70, 96, "stand", (0.50, 0.70), None),
        (96, 140, "sit", (0.50, 0.70), None),
        (140, 150, "stand", (0.50, 0.70), None)]),
    dict(id=3, role="child", shirt=(60, 200, 240), script=[(0, 150, "play", (0.15, 0.72), None)]),
    dict(id=4, role="child", shirt=(200, 90, 220), script=[(0, 150, "play", (0.30, 0.86), None)]),
    dict(id=5, role="child", shirt=(80, 200, 90), script=[
        (0, 100, "play", (0.45, 0.62), None),
        (100, 112, "lie", (0.45, 0.62), None),
        (112, 150, "play", (0.45, 0.62), None)]),
    dict(id=6, role="child", shirt=(40, 140, 250), script=[(0, 150, "play", (0.20, 0.50), None)]),
    dict(id=7, role="child", shirt=(230, 170, 60), script=[(0, 150, "play", (0.58, 0.88), None)]),
    dict(id=8, role="child", shirt=(150, 150, 230), script=[(0, 150, "lie", (0.86, 0.82), None)]),  # napping
]

TABLE_PHONE = (0.135, 0.405)   # hard negative: phone on a table, nobody holding it


class SimScene:
    def __init__(self, calibration: dict | None = None, seed: int = 0):
        cal = calibration or {"near": [1.0, 0.42], "far": [0.3, 0.24]}
        (self.y0, self.h0), (self.y1, self.h1) = cal["far"], cal["near"]
        self.rng = np.random.default_rng(seed)
        self.background = self._draw_background()

    def adult_height_px(self, feet_y: float) -> float:
        return (self.h0 + (feet_y - self.y0) * (self.h1 - self.h0) / (self.y1 - self.y0)) * H

    # ---- scripting ---------------------------------------------------------------------------
    @staticmethod
    def _segment(actor, t):
        for seg in actor["script"]:
            if seg[0] <= t < seg[1]:
                return seg
        return actor["script"][-1]

    def actor_state(self, actor, t: float):
        t0, t1, pose, a, b = self._segment(actor, t)
        if pose == "hidden":
            return None
        a = np.array(a, float)
        if b is not None:
            pos = a + (np.array(b) - a) * (t - t0) / (t1 - t0)
        else:
            pos = a.copy()
        if pose == "play":
            ph = actor["id"] * 1.7
            pos += [0.04 * math.sin(0.35 * t + ph), 0.025 * math.cos(0.27 * t + ph)]
        elif pose == "stand":
            pos += [0.004 * math.sin(0.5 * t + actor["id"]), 0.0]
        return pose, pos

    # ---- skeleton ------------------------------------------------------------------------------
    def skeleton(self, role: str, pose: str, fx: float, fy: float, hgt: float, phase: float) -> np.ndarray:
        b = BODY[role]
        k = np.zeros((17, 3))
        k[:, 2] = KP_CONF

        def put(i, dx, up):
            k[i, 0], k[i, 1] = fx + dx * hgt, fy - up * hgt

        if pose == "lie":
            y = .05
            span = [(15, -.47, .03), (16, -.47, -.03), (13, -.25, .03), (14, -.25, -.03),
                    (11, -.05, .04), (12, -.05, -.04), (5, .25, .06), (6, .25, -.06),
                    (7, .12, .09), (8, .12, -.09), (9, .0, .10), (10, .0, -.10),
                    (0, .40, 0), (1, .42, .02), (2, .42, -.02), (3, .38, .05), (4, .38, -.05)]
            for i, dx, dy in span:
                put(i, dx, y + dy)
            return k

        sit = pose == "sit"
        hip = .24 if sit else b["hip"]
        sh = hip + (b["sh"] - b["hip"])
        drop = .06 if pose in ("phone", "sit") else 0.0     # head pitched down
        nose, eye, ear = (sh + b[n] - b["sh"] for n in ("nose", "eye", "ear"))
        swing = math.sin(phase) if pose in ("walk", "play") else 0.0

        put(0, 0, nose - drop)
        put(1, b["eyex"], eye - drop); put(2, -b["eyex"], eye - drop)
        put(3, b["earx"], ear); put(4, -b["earx"], ear)
        put(5, b["shx"], sh); put(6, -b["shx"], sh)
        put(11, b["hipx"], hip); put(12, -b["hipx"], hip)
        if sit:
            put(13, .08, .22); put(14, -.08, .22); put(15, .07, b["an"]); put(16, -.07, b["an"])
        else:
            put(13, b["legx"] + .04 * swing, b["kn"]); put(14, -b["legx"] - .04 * swing, b["kn"])
            put(15, b["legx"] + .07 * swing, b["an"]); put(16, -b["legx"] - .07 * swing, b["an"])

        if pose == "phone":
            put(7, .10, sh - .22); put(8, -.10, sh - .22)
            put(9, .02, sh - .17); put(10, -.02, sh - .17)
        elif sit:
            put(7, .13, sh - .16); put(8, -.13, sh - .16)
            put(9, .10, hip + .02); put(10, -.10, hip + .02)
        elif pose == "play":
            wave = math.sin(phase * 0.7)
            put(7, .17, sh - .05 + .08 * wave); put(8, -.17, sh - .05 - .08 * wave)
            put(9, .22, sh + .02 + .15 * wave); put(10, -.22, sh + .02 - .15 * wave)
        else:
            put(7, .14, sh - .16); put(8, -.14, sh - .16)
            put(9, .15 + .03 * swing, sh - .32); put(10, -.15 - .03 * swing, sh - .32)
        return k

    # ---- frame ---------------------------------------------------------------------------------
    def render(self, t: float, loop: int = 0) -> tuple[np.ndarray, list[Person], list[Phone]]:
        frame = self.background.copy()
        actors = []
        for a in ACTORS:
            st = self.actor_state(a, t)
            if st is None:
                continue
            pose, (x, y) = st
            hgt = self.adult_height_px(y) * (0.55 if a["role"] == "child" else 1.0)
            k = self.skeleton(a["role"], pose, x * W, y * H, hgt, t * 6 + a["id"])
            actors.append((y, a, pose, k, hgt))

        persons, phones = [], []
        for _, a, pose, k, hgt in sorted(actors, key=lambda r: r[0]):     # painter's order
            self._draw_actor(frame, a, pose, k, hgt)
            noisy = k.copy()
            noisy[:, :2] += self.rng.normal(0, 0.4, (17, 2))
            persons.append(Person(a["id"] + 100 * loop, self._box(k, hgt, a["role"]), 0.9, noisy))
            if pose == "phone":
                c = (k[9, :2] + k[10, :2]) / 2 - [0, 0.02 * hgt]
                box = np.array([c[0] - .018 * hgt, c[1] - .028 * hgt, c[0] + .018 * hgt, c[1] + .028 * hgt])
                self._draw_phone(frame, box)
                phones.append(Phone(box, float(np.clip(0.72 + self.rng.normal(0, 0.05), 0.3, 0.95))))

        tx, ty = TABLE_PHONE[0] * W, TABLE_PHONE[1] * H
        tbox = np.array([tx - 9, ty - 5, tx + 9, ty + 5])
        phones.append(Phone(tbox, 0.55))
        cv2.putText(frame, "SIMULATED FEED - synthetic actors", (W - 390, H - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (90, 90, 90), 1, cv2.LINE_AA)
        return frame, persons, phones

    @staticmethod
    def _box(k: np.ndarray, hgt: float, role: str) -> np.ndarray:
        head = BODY[role]["head"] * hgt
        x1, y1 = k[:, 0].min(), k[:, 1].min()
        x2, y2 = k[:, 0].max(), k[:, 1].max()
        top = min(y1, k[0, 1] - head * 1.1)
        pad = 0.03 * hgt
        return np.array([x1 - pad, top, x2 + pad, y2 + pad * 0.3])

    def _draw_actor(self, f, a, pose, k, hgt):
        p = lambda i: (int(k[i, 0]), int(k[i, 1]))
        th = max(3, int(hgt * (0.045 if a["role"] == "adult" else 0.06)))
        pants = (70, 60, 60) if a["role"] == "adult" else (120, 80, 60)
        for i, j in [(11, 13), (13, 15), (12, 14), (14, 16)]:
            cv2.line(f, p(i), p(j), pants, th, cv2.LINE_AA)
        torso = np.array([p(5), p(6), p(12), p(11)], np.int32)
        cv2.fillConvexPoly(f, torso, a["shirt"], cv2.LINE_AA)
        for i, j in [(5, 7), (7, 9), (6, 8), (8, 10)]:
            cv2.line(f, p(i), p(j), a["shirt"], max(2, th - 1), cv2.LINE_AA)
        for i in (9, 10):
            cv2.circle(f, p(i), max(2, th // 2), (150, 180, 220), -1, cv2.LINE_AA)
        r = int(BODY[a["role"]]["head"] * hgt * (0.55 if a["role"] == "adult" else 0.45))
        hc = (int((k[3, 0] + k[4, 0]) / 2), int((k[0, 1] + k[3, 1]) / 2 - r * 0.3)) if pose != "lie" \
            else (int(k[0, 0]), int(k[0, 1]))
        cv2.circle(f, hc, r, (150, 180, 220), -1, cv2.LINE_AA)
        cv2.ellipse(f, (hc[0], hc[1] - r // 3), (r, int(r * 0.75)), 0, 180, 360, (40, 40, 60), -1, cv2.LINE_AA)

    @staticmethod
    def _draw_phone(f, box):
        x1, y1, x2, y2 = box.astype(int)
        cv2.rectangle(f, (x1, y1), (x2, y2), (30, 30, 30), -1)
        cv2.rectangle(f, (x1 + 2, y1 + 3), (x2 - 2, y2 - 3), (230, 200, 120), -1)

    @staticmethod
    def _draw_background() -> np.ndarray:
        f = np.zeros((H, W, 3), np.uint8)
        wall_h = int(0.27 * H)
        f[:wall_h] = (215, 228, 236)
        for y in range(wall_h, H):                      # floor gradient
            v = 0.85 + 0.15 * (y - wall_h) / (H - wall_h)
            f[y] = (int(170 * v), int(200 * v), int(222 * v))
        cv2.line(f, (0, wall_h), (W, wall_h), (150, 160, 170), 2)
        cv2.rectangle(f, (int(.41 * W), int(.07 * H)), (int(.51 * W), wall_h), (90, 120, 150), -1)   # door
        cv2.circle(f, (int(.495 * W), int(.18 * H)), 4, (40, 60, 80), -1)
        cv2.putText(f, "EXIT", (int(.44 * W), int(.06 * H)), cv2.FONT_HERSHEY_SIMPLEX, .6, (40, 60, 160), 2)
        cv2.rectangle(f, (int(.75 * W), int(.18 * H)), (int(.98 * W), int(.30 * H)), (160, 170, 175), -1)  # counter
        cv2.putText(f, "kitchen", (int(.80 * W), int(.25 * H)), cv2.FONT_HERSHEY_SIMPLEX, .6, (70, 70, 70), 1)
        for i in range(3):                              # nap mats
            x = int((.77 + i * .07) * W)
            cv2.rectangle(f, (x, int(.68 * H)), (x + int(.06 * W), int(.95 * H)), (200, 160, 120), -1)
        cv2.rectangle(f, (int(.08 * W), int(.38 * H)), (int(.20 * W), int(.46 * H)), (80, 120, 160), -1)  # table
        tx, ty = int(TABLE_PHONE[0] * W), int(TABLE_PHONE[1] * H)
        cv2.rectangle(f, (tx - 9, ty - 5), (tx + 9, ty + 5), (30, 30, 30), -1)
        for cx, cy, c in [(.30, .45, (80, 80, 220)), (.60, .50, (60, 180, 220)), (.12, .90, (200, 120, 60))]:
            cv2.circle(f, (int(cx * W), int(cy * H)), 12, c, -1, cv2.LINE_AA)   # toys
        return f
