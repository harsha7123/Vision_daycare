"""Option A action recognition (blueprint 5.4): 2-layer GRU on 2-3 s windows of YOLO-pose keypoints,
normalised to the hip centre. Runs on CPU, only for tracks the rule engine flags as candidates."""
from __future__ import annotations

from collections import deque
from pathlib import Path

import numpy as np

from ..perception.posture import kp, mid, torso
from ..types import L_HIP, R_HIP

CLASSES = ["phone_use", "idle_sitting", "walking", "playing_with_child", "fall"]
WINDOW = 30          # frames (~2.5 s at 12 fps)
FEATURES = 17 * 3


def normalize(kpts: np.ndarray | None, xyxy) -> np.ndarray:
    """(17,3) pixels -> (51,) hip-centred, scaled by torso length (box height fallback)."""
    out = np.zeros((17, 3), np.float32)
    if kpts is None:
        return out.ravel()
    hip = mid(kp(kpts, L_HIP), kp(kpts, R_HIP))
    if hip is None:
        hip = np.array([(xyxy[0] + xyxy[2]) / 2, (xyxy[1] + xyxy[3]) / 2])
    t = torso(kpts)
    scale = np.linalg.norm(t[0] - t[1]) if t is not None else 0.3 * (xyxy[3] - xyxy[1])
    scale = max(float(scale), 1.0)
    out[:, :2] = (kpts[:, :2] - hip) / scale
    out[:, 2] = kpts[:, 2]
    out[kpts[:, 2] < 0.3, :2] = 0
    return out.ravel()


def build_model(n_classes: int = len(CLASSES), hidden: int = 64):
    import torch.nn as nn

    class PoseGRU(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(FEATURES, hidden, num_layers=2, batch_first=True, dropout=0.1)
            self.head = nn.Linear(hidden, n_classes)

        def forward(self, x):              # x: (B, T, 51)
            _, h = self.gru(x)
            return self.head(h[-1])

    return PoseGRU()


class ActionRecognizer:
    def __init__(self, weights: str | Path, device: str = "cpu", every_n: int = 6):
        import torch
        self.torch = torch
        ck = torch.load(str(weights), map_location=device, weights_only=False)
        self.classes = ck.get("classes", CLASSES)
        self.window = ck.get("window", WINDOW)
        self.model = build_model(len(self.classes))
        self.model.load_state_dict(ck["state_dict"])
        self.model.eval().to(device)
        self.device = device
        self.every_n = every_n
        self.buf: dict[int, deque] = {}
        self.last: dict[int, dict] = {}
        self.count: dict[int, int] = {}

    def update(self, tid: int, kpts, xyxy, now: float, candidate: bool = True) -> dict | None:
        b = self.buf.setdefault(tid, deque(maxlen=self.window))
        b.append(normalize(kpts, xyxy))
        self.count[tid] = self.count.get(tid, 0) + 1
        if not candidate or len(b) < self.window:
            return self.last.get(tid)
        if self.count[tid] % self.every_n == 0 or tid not in self.last:
            with self.torch.no_grad():
                x = self.torch.from_numpy(np.stack(b)[None]).to(self.device)
                p = self.torch.softmax(self.model(x), -1)[0].cpu().numpy()
            self.last[tid] = {c: float(v) for c, v in zip(self.classes, p)}
        return self.last.get(tid)

    def forget(self, tids) -> None:
        for t in tids:
            for d in (self.buf, self.last, self.count):
                d.pop(t, None)
