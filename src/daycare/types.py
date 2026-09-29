"""Plain data types shared by perception, rules and events (no heavy imports)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# COCO-17 keypoint indices (YOLO-pose order)
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KN, R_KN, L_AN, R_AN = 11, 12, 13, 14, 15, 16

SKELETON = [
    (L_SH, R_SH), (L_SH, L_EL), (L_EL, L_WR), (R_SH, R_EL), (R_EL, R_WR),
    (L_SH, L_HIP), (R_SH, R_HIP), (L_HIP, R_HIP), (L_HIP, L_KN), (L_KN, L_AN),
    (R_HIP, R_KN), (R_KN, R_AN), (NOSE, L_EYE), (NOSE, R_EYE), (L_EYE, L_EAR), (R_EYE, R_EAR),
]

KPT_CONF = 0.3  # keypoints below this confidence are treated as missing


@dataclass
class Person:
    """One tracked person in one frame (pixel coordinates)."""
    track_id: int
    xyxy: np.ndarray                 # (4,)
    conf: float
    kpts: np.ndarray | None = None   # (17, 3): x, y, confidence


@dataclass
class Phone:
    xyxy: np.ndarray
    conf: float


@dataclass
class Observation:
    """What the rule engine needs to know about one visible track this frame."""
    track_id: int
    xyxy: np.ndarray
    kpts: np.ndarray | None
    role: str                        # "adult" | "child"
    p_child: float
    zones: set[str] = field(default_factory=set)   # zone *types* containing the feet point
    phone_score: float = 0.0
    actions: dict[str, float] | None = None        # optional action-model probabilities
