"""Fine-tune YOLO on adult / child / cell_phone (blueprint section 7).

    python training/train_detector.py --data training/data/daycare.yaml --epochs 100 --imgsz 960
    python training/train_detector.py --export models/best.pt      # TensorRT FP16 on the target device

Larger imgsz (960-1280) helps small phones. Keep the test split from a different day and camera.
"""
import argparse

from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="training/data/daycare.yaml")
    ap.add_argument("--model", default="yolo11s.pt")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--device", default=None)
    ap.add_argument("--export", help="export these weights to a TensorRT engine instead of training")
    a = ap.parse_args()

    if a.export:
        YOLO(a.export).export(format="engine", half=True, imgsz=a.imgsz, device=a.device or 0)
        return
    model = YOLO(a.model)
    model.train(data=a.data, epochs=a.epochs, imgsz=a.imgsz, batch=a.batch, device=a.device,
                project="runs/detect", name="daycare", close_mosaic=10, patience=30)
    metrics = model.val(data=a.data, split="test", imgsz=a.imgsz)
    print("mAP50 per class:", dict(zip(metrics.names.values(), metrics.box.ap50)))
    print("targets: >= 0.85 adult/child, >= 0.6 phone")


if __name__ == "__main__":
    main()
