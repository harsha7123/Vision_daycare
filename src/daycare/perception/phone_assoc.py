"""Phone <-> person association from pose keypoints (blueprint section 5.1).

A phone box merely overlapping a person box (phone on a table) must not count.
Per phone:  score = hand_test * (0.5 + 0.5 * face_test) * min(1, conf / conf_full)
Without any phone box: wrists together in front of the chest + head down -> weaker score.
"""
from __future__ import annotations

import numpy as np

from ..types import L_HIP, L_SH, L_WR, NOSE, R_HIP, R_SH, R_WR, Phone
from .posture import head_down, kp, mid, shoulder_width

DEFAULTS = dict(hand_k=0.6, face_k=1.0, conf_full=0.5, no_phone_score=0.4)


def _center(xyxy) -> np.ndarray:
    return np.array([(xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2], float)


def phone_score(kpts: np.ndarray | None, person_xyxy, phones: list[Phone], cfg: dict | None = None) -> float:
    c = {**DEFAULTS, **(cfg or {})}
    if kpts is None:
        return 0.0
    sw = shoulder_width(kpts, person_xyxy)
    wrists = [w for w in (kp(kpts, L_WR), kp(kpts, R_WR)) if w is not None]
    nose = kp(kpts, NOSE)
    down = head_down(kpts, sw)

    best = 0.0
    for ph in phones:
        pc = _center(ph.xyxy)
        if not wrists:
            continue
        hand = min(np.linalg.norm(pc - w) for w in wrists) < c["hand_k"] * sw
        if not hand:
            continue
        call = nose is not None and np.linalg.norm(pc - nose) < c["face_k"] * sw
        screen = down and nose is not None and pc[1] > nose[1]
        face = 1.0 if (call or screen) else 0.0
        conf = min(1.0, ph.conf / c["conf_full"])
        best = max(best, (0.5 + 0.5 * face) * conf)

    if best == 0.0 and len(wrists) == 2 and down and _wrists_at_chest(kpts, wrists, sw):
        best = c["no_phone_score"]
    return float(best)


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
