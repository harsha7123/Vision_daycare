"""Train the keypoint-sequence GRU (blueprint 5.4, Option A).

Real data:   an .npz with X (N, T, 51) from daycare.actions.pose_gru.normalize and y (N,) class ids
             (build it from labelled 2-3 s clips: run YOLO-pose + ByteTrack, cut windows per track).
Smoke test:  --synthetic generates windows from the simulator's skeletons so the full
             train -> models/pose_gru.pt -> live inference loop can be exercised today.

    python training/train_actions.py --synthetic --epochs 15
    python training/train_actions.py --data data/actions.npz --epochs 60
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daycare.actions.pose_gru import CLASSES, WINDOW, build_model, normalize  # noqa: E402
from daycare.sim import SimScene  # noqa: E402

POSE_FOR = {"phone_use": ("adult", "phone"), "idle_sitting": ("adult", "sit"), "walking": ("adult", "walk"),
            "playing_with_child": ("child", "play"), "fall": ("child", "lie")}


def synthetic(n_per_class: int = 300, seed: int = 0):
    rng = np.random.default_rng(seed)
    sim = SimScene(seed=seed)
    X, y = [], []
    for ci, cls in enumerate(CLASSES):
        role, pose = POSE_FOR[cls]
        for _ in range(n_per_class):
            fx, fy = rng.uniform(200, 1000), rng.uniform(350, 700)
            hgt = sim.adult_height_px(fy / 720) * (0.55 if role == "child" else 1.0) * rng.uniform(0.85, 1.15)
            phase0, speed = rng.uniform(0, 6.28), rng.uniform(0.3, 0.7)
            seq = []
            for t in range(WINDOW):
                k = sim.skeleton(role, pose, fx + (4 * t if pose == "walk" else 0), fy, hgt, phase0 + speed * t)
                k[:, :2] += rng.normal(0, 1.2, (17, 2))
                drop = rng.random(17) < 0.05                      # simulate missing keypoints
                k[drop, 2] = 0.0
                seq.append(normalize(k, sim._box(k, hgt, role)))
            X.append(seq)
            y.append(ci)
    return np.asarray(X, np.float32), np.asarray(y, np.int64)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help=".npz with X (N,T,51) and y (N,)")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "models" / "pose_gru.pt"))
    args = ap.parse_args()

    import torch
    from torch import nn

    if args.synthetic:
        X, y = synthetic()
    elif args.data:
        d = np.load(args.data)
        X, y = d["X"].astype(np.float32), d["y"].astype(np.int64)
    else:
        ap.error("pass --data or --synthetic")

    idx = np.random.default_rng(1).permutation(len(X))
    cut = int(0.8 * len(X))
    tr, va = idx[:cut], idx[cut:]
    Xt, yt = torch.from_numpy(X), torch.from_numpy(y)
    model = build_model(len(CLASSES))
    opt = torch.optim.AdamW(model.parameters(), lr=2e-3, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()

    for ep in range(args.epochs):
        model.train()
        perm = torch.from_numpy(np.random.permutation(tr))
        for b in perm.split(64):
            opt.zero_grad()
            loss = loss_fn(model(Xt[b]), yt[b])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            pred = model(Xt[va]).argmax(-1)
            acc = (pred == yt[va]).float().mean().item()
        print(f"epoch {ep + 1:3d}  loss {loss.item():.3f}  val_acc {acc:.3f}")

    with torch.no_grad():
        pred = model(Xt[va]).argmax(-1).numpy()
    cm = np.zeros((len(CLASSES), len(CLASSES)), int)
    for t, p in zip(y[va], pred):
        cm[t, p] += 1
    print("confusion matrix (rows = truth):", *CLASSES, sep="\n  ")
    print(cm)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": model.state_dict(), "classes": CLASSES, "window": WINDOW}, args.out)
    print("saved", args.out)


if __name__ == "__main__":
    main()
