"""YOLO pose + ByteTrack for people, YOLO detect for phones (COCO class 67)."""
from __future__ import annotations

import numpy as np

from ..config import resolve
from ..types import Person, Phone

PERSON, CELL_PHONE = 0, 67


def pick_device(pref: str = "auto") -> str:
    if pref and pref != "auto":
        return pref
    try:
        import torch
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


class Perception:
    def __init__(self, models: dict, pcfg: dict, device: str = "auto"):
        from ultralytics import YOLO   # heavy import kept local so rules/tests don't need torch
        import supervision as sv
        self._sv = sv
        self.device = pick_device(device)
        self.half = self.device.startswith("cuda")
        # a path under models/ that doesn't exist yet is auto-downloaded there by Ultralytics
        self.pose = YOLO(str(resolve(models.get("pose", "models/yolo11n-pose.pt"))))
        self.det = YOLO(str(resolve(models.get("detector", "models/yolo11s.pt"))))
        self.cfg = pcfg
        self.tracker = str(resolve(pcfg.get("tracker", "configs/bytetrack.yaml")))

    def reset(self) -> None:
        """Drop tracker state (new video source)."""
        pred = getattr(self.pose, "predictor", None)
        if pred is not None and getattr(pred, "trackers", None):
            for t in pred.trackers:
                t.reset()

    def __call__(self, frame: np.ndarray) -> tuple[list[Person], list[Phone]]:
        c = self.cfg
        r = self.pose.track(frame, persist=True, tracker=self.tracker, classes=[PERSON],
                            conf=c.get("person_conf", 0.35), imgsz=c.get("imgsz", 640),
                            device=self.device, half=self.half, verbose=False)[0]
        people = self._sv.Detections.from_ultralytics(r)
        kpts = r.keypoints.data.cpu().numpy() if r.keypoints is not None else None
        persons = []
        if people.tracker_id is not None:
            for i, tid in enumerate(people.tracker_id):
                k = kpts[i] if kpts is not None and len(kpts) > i else None
                persons.append(Person(int(tid), people.xyxy[i].astype(float),
                                      float(people.confidence[i]), k))

        d = self.det(frame, classes=[CELL_PHONE], conf=c.get("phone_conf", 0.25),
                     imgsz=c.get("det_imgsz", 640), device=self.device, half=self.half, verbose=False)[0]
        ph = self._sv.Detections.from_ultralytics(d)
        phones = [Phone(ph.xyxy[i].astype(float), float(ph.confidence[i])) for i in range(len(ph))]
        return persons, phones
