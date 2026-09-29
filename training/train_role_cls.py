"""Adult / child crop classifier (blueprint 5.2, recommended approach).

1) Extract person crops from your own recorded footage (with consent):
       python training/train_role_cls.py extract --video recordings/cam01_day1.mp4 --out data/crops
   then sort data/crops/unsorted/*.jpg into data/crops/{train,val}/{adult,child}/ (1-3 k per class).
2) Train:
       python training/train_role_cls.py train --data data/crops --epochs 50
   The best weights are copied to models/role_cls.pt, which the pipeline picks up automatically.
"""
import argparse
import shutil
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]


def extract(video: str, out: str, every: int = 15, min_h: int = 60):
    from ultralytics import YOLO
    model = YOLO(str(ROOT / "models" / "yolo11n-pose.pt"))
    dst = Path(out) / "unsorted"
    dst.mkdir(parents=True, exist_ok=True)
    seen: dict[int, int] = {}
    n = 0
    for i, r in enumerate(model.track(video, stream=True, classes=[0], persist=True, verbose=False,
                                      tracker=str(ROOT / "configs" / "bytetrack.yaml"))):
        if i % every or r.boxes.id is None:
            continue
        for box, tid in zip(r.boxes.xyxy.cpu().numpy().astype(int), r.boxes.id.int().tolist()):
            x1, y1, x2, y2 = box
            if y2 - y1 < min_h or seen.get(tid, 0) >= 20:       # at most 20 crops per track
                continue
            seen[tid] = seen.get(tid, 0) + 1
            cv2.imwrite(str(dst / f"{Path(video).stem}_t{tid}_{i}.jpg"), r.orig_img[max(y1, 0):y2, max(x1, 0):x2])
            n += 1
    print(f"wrote {n} crops to {dst}; sort them into train/val x adult/child")


def train(data: str, epochs: int, imgsz: int = 224):
    from ultralytics import YOLO
    model = YOLO("yolo11n-cls.pt")
    res = model.train(data=data, epochs=epochs, imgsz=imgsz, project="runs/cls", name="role")
    best = Path(res.save_dir) / "weights" / "best.pt"
    (ROOT / "models").mkdir(exist_ok=True)
    shutil.copy(best, ROOT / "models" / "role_cls.pt")
    print("saved models/role_cls.pt  (target: >= 95 % per-track accuracy after voting)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--video", required=True)
    e.add_argument("--out", default="data/crops")
    e.add_argument("--every", type=int, default=15)
    t = sub.add_parser("train")
    t.add_argument("--data", default="data/crops")
    t.add_argument("--epochs", type=int, default=50)
    a = ap.parse_args(sys.argv[1:])
    extract(a.video, a.out, a.every) if a.cmd == "extract" else train(a.data, a.epochs)
