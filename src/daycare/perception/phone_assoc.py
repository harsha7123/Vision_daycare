"""Phone <-> person association from pose keypoints (blueprint section 5.1).

A detected phone counts as "in use" by a person when two kinds of evidence agree:
  hand   - the phone is near a wrist / hand (soft distance falloff). If the hand is hidden
           behind the phone (common on laptop cameras) but the phone is on the person's
           upper body, that counts as partial hand evidence.
  face   - the phone is at the face (call posture), or in front of the chest/face below the
           nose (texting); stronger if the head is pitched down.
  score  = conf_term * (0.55 * hand + 0.45 * face) (+0.1 when both are strong)
           conf_term = 0.75 + 0.25 * min(1, conf / conf_full); the detector threshold already
           filters weak boxes, so geometry is the main evidence.
A phone on a table away from the person, or held at the hip without looking, stays below the
0.6 "enter" threshold. Without any phone box: wrists together at the chest + head down gives a
weak 0.4 signal (never enough on its own).
"""
from __future__ import annotations

import numpy as np

from ..types import L_EL, L_HIP, L_SH, L_WR, NOSE, R_EL, R_HIP, R_SH, R_WR, Phone
from .posture import head_down, kp, mid, shoulder_width

DEFAULTS = dict(hand_k=0.9, hand_far_k=1.6, face_k=1.4, torso_k=1.0, conf_full=0.5, no_phone_score=0.4)
EMPTY = {"score": 0.0, "hand": 0.0, "face": 0.0, "conf": 0.0, "reason": "no phone detected"}


def _center(xyxy) -> np.ndarray:
    return np.array([(xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2], float)


def _hands(kpts) -> list[np.ndarray]:
    """Hand positions: wrist pushed a little further along the forearm (the phone sits in the palm)."""
    out = []
    for w_i, e_i in ((L_WR, L_EL), (R_WR, R_EL)):
        w, e = kp(kpts, w_i), kp(kpts, e_i)
        if w is None:
            continue
        out.append(w + 0.25 * (w - e) if e is not None else w)
        out.append(w)
    return out


def _face_point(kpts):
    pts = [p for p in (kp(kpts, i) for i in range(5)) if p is not None]
    return np.mean(pts, axis=0) if pts else None


def phone_evidence(kpts: np.ndarray | None, person_xyxy, phones: list[Phone], cfg: dict | None = None) -> dict:
    """Best phone for this person, with the evidence behind its score (shown in the live UI)."""
    c = {**DEFAULTS, **(cfg or {})}
    if kpts is None:
        return dict(EMPTY, reason="no body keypoints")
    sw = shoulder_width(kpts, person_xyxy)
    hands = _hands(kpts)
    face_pt = _face_point(kpts)
    sh = mid(kp(kpts, L_SH), kp(kpts, R_SH))
    down = head_down(kpts, sw)
    x1, y1, x2, y2 = [float(v) for v in person_xyxy]
    bw, bh = x2 - x1, y2 - y1

    best = dict(EMPTY, reason="no phone detected" if not phones else "phone not on this person")
    for ph in phones:
        pc = _center(ph.xyxy)
        if not (x1 - 0.15 * bw <= pc[0] <= x2 + 0.15 * bw and y1 - 0.1 * bh <= pc[1] <= y2):
            continue                                   # phone belongs to someone/something else

        if hands:
            d = min(np.linalg.norm(pc - h) for h in hands)
            near, far = c["hand_k"] * sw, c["hand_far_k"] * sw
            hand = 1.0 if d <= near else max(0.0, 1.0 - (d - near) / (far - near))
        else:
            hand = 0.7 if pc[1] <= y1 + 0.75 * bh else 0.0     # hand hidden behind the phone

        face = 0.0
        if face_pt is not None:
            if np.linalg.norm(pc - face_pt) <= c["face_k"] * sw:
                face = 1.0                                     # at the ear / in front of the face
            elif pc[1] > face_pt[1]:
                chest_y = (sh[1] if sh is not None else face_pt[1] + 0.8 * sw) + c["torso_k"] * sw
                if abs(pc[0] - face_pt[0]) <= 1.2 * sw and pc[1] <= chest_y:
                    face = 1.0 if down else 0.7                # held in front of the chest, looking at it

        conf_term = 0.75 + 0.25 * min(1.0, ph.conf / c["conf_full"])
        s = conf_term * (0.55 * hand + 0.45 * face)
        if hand >= 0.7 and face >= 0.7:
            s = min(1.0, s + 0.1)
        if s > best["score"]:
            reason = "in use" if s >= 0.6 else ("not in hand" if hand < 0.5 else "not looking at it")
            best = {"score": round(float(s), 3), "hand": round(float(hand), 2), "face": round(float(face), 2),
                    "conf": round(float(ph.conf), 2), "reason": reason}

    if best["score"] == 0.0:
        wrists = [w for w in (kp(kpts, L_WR), kp(kpts, R_WR)) if w is not None]
        if len(wrists) == 2 and down and _wrists_at_chest(kpts, wrists, sw):
            best = dict(EMPTY, score=c["no_phone_score"], reason="phone-like posture, no phone seen")
    return best


def phone_score(kpts: np.ndarray | None, person_xyxy, phones: list[Phone], cfg: dict | None = None) -> float:
    return float(phone_evidence(kpts, person_xyxy, phones, cfg)["score"])


def _wrists_at_chest(kpts, wrists, sw) -> bool:
    if np.linalg.norm(wrists[0] - wrists[1]) > 0.5 * sw:
        return False
    sh = mid(kp(kpts, L_SH), kp(kpts, R_SH))
    hip = mid(kp(kpts, L_HIP), kp(kpts, R_HIP))
    if sh is None:
        return False
    y = (wrists[0][1] + wrists[1][1]) / 2
    lower = hip[1] if hip is not None else sh[1] + 1.5 * sw
    return sh[1] - 0.2 * sw <= y <= lower
