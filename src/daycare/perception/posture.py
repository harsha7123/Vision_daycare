"""Keypoint geometry helpers: shoulder width, head-down, lying, seated, motion."""
from __future__ import annotations

import numpy as np

from ..types import (KPT_CONF, L_AN, L_EAR, L_EYE, L_HIP, L_SH, NOSE, R_AN, R_EAR, R_EYE,
                     R_HIP, R_SH)


def kp(k: np.ndarray | None, i: int) -> np.ndarray | None:
    """Keypoint i as float xy, or None if missing / low confidence."""
    if k is None or k[i, 2] < KPT_CONF or (k[i, 0] == 0 and k[i, 1] == 0):
        return None
    return k[i, :2].astype(float)


def mid(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (a + b) / 2


def box_hw(xyxy) -> tuple[float, float]:
    return float(xyxy[3] - xyxy[1]), float(xyxy[2] - xyxy[0])


def shoulder_width(k: np.ndarray | None, xyxy=None) -> float:
    """Shoulder width in px; floors at 18 % of box height so side views don't collapse to 0."""
    floor = 0.18 * box_hw(xyxy)[0] if xyxy is not None else 1.0
    ls, rs = kp(k, L_SH), kp(k, R_SH)
    if ls is not None and rs is not None:
        return max(float(np.linalg.norm(ls - rs)), floor, 1.0)
    return max(floor, 1.0)


def head_down(k: np.ndarray | None, sw: float) -> bool:
    """Head pitched forward: nose dropped well below the ear line, or close to the shoulder line."""
    nose = kp(k, NOSE)
    if nose is None:
        return False
    ears = [p for p in (kp(k, L_EAR), kp(k, R_EAR)) if p is not None]
    if ears and nose[1] - np.mean([e[1] for e in ears]) > 0.18 * sw:
        return True
    eyes = [p for p in (kp(k, L_EYE), kp(k, R_EYE)) if p is not None]
    if not ears and eyes and nose[1] - np.mean([e[1] for e in eyes]) > 0.3 * sw:
        return True
    sh = mid(kp(k, L_SH), kp(k, R_SH))
    return sh is not None and (sh[1] - nose[1]) < 0.4 * sw


def torso(k):
    sh = mid(kp(k, L_SH), kp(k, R_SH))
    hip = mid(kp(k, L_HIP), kp(k, R_HIP))
    if sh is None or hip is None:
        return None
    return sh, hip


def is_lying(xyxy, k: np.ndarray | None) -> bool:
    """Torso more than 55 deg from vertical, or (no torso keypoints) a wide, flat box."""
    t = torso(k)
    if t is not None:
        v = t[0] - t[1]
        if np.linalg.norm(v) > 1:
            angle = np.degrees(np.arctan2(abs(v[0]), abs(v[1])))
            return angle > 55
    h, w = box_hw(xyxy)
    return w > 1.3 * h


def is_seated(k: np.ndarray | None) -> bool:
    """Hip-to-ankle vertical extent is short compared with the torso."""
    t = torso(k)
    an = mid(kp(k, L_AN), kp(k, R_AN))
    if t is None or an is None:
        return False
    v = t[0] - t[1]
    torso_len = np.linalg.norm(v)
    if torso_len <= 1 or abs(v[0]) > abs(v[1]):    # torso not upright: lying, not seated
        return False
    legs = an[1] - t[1][1]
    return legs < 0.8 * torso_len


def motion(prev: np.ndarray | None, cur: np.ndarray | None, scale: float) -> float | None:
    """Mean displacement of keypoints visible in both frames, normalised by scale."""
    if prev is None or cur is None:
        return None
    ok = (prev[:, 2] >= KPT_CONF) & (cur[:, 2] >= KPT_CONF)
    if ok.sum() < 3:
        return None
    return float(np.linalg.norm(cur[ok, :2] - prev[ok, :2], axis=1).mean() / max(scale, 1.0))
