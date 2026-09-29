# models/

Weights live here but are **not committed** (use DVC or a model registry).

| File | Produced by | Used for |
|---|---|---|
| `yolo11n-pose.pt`, `yolo11s.pt` | downloaded automatically by Ultralytics on first run | person + keypoints, cell phone |
| `role_cls.pt` | `training/train_role_cls.py` | adult / child crop classifier (optional) |
| `pose_gru.pt` | `training/train_actions.py` | phone / idle / fall action model (optional) |
| `*.engine` | `yolo export format=engine half=True` on the edge device | TensorRT FP16 inference |
