"""Build the offline sample for the web app (web/public/sample/): a simulated classroom video,
its analysis JSON (same format as the cloud API) and face-blurred snapshots.

    python tools/make_web_sample.py
"""
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from daycare.batch import Detections, analyze, build_rules, estimate_calibration, render_snapshots  # noqa: E402
from daycare.sim import H, W, SimScene  # noqa: E402

FPS, SECONDS, OUT_W = 10, 125, 960
OUT = ROOT / "web" / "public" / "sample"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sim = SimScene()
    det = Detections(W, H, FPS, SECONDS, FPS)
    tmp = Path(tempfile.mkdtemp())
    full = cv2.VideoWriter(str(tmp / "full.avi"), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    web = cv2.VideoWriter(str(OUT / "sample.webm"), cv2.VideoWriter_fourcc(*"VP80"), FPS, (OUT_W, OUT_W * H // W))
    for i in range(SECONDS * FPS):
        t = i / FPS
        frame, persons, phones = sim.render(t)
        det.frames.append((t, i, persons, phones))
        full.write(frame)
        web.write(cv2.resize(frame, (OUT_W, OUT_W * H // W), interpolation=cv2.INTER_AREA))
    full.release()
    web.release()

    zones = json.loads((ROOT / "configs" / "zones" / "sim.json").read_text())["zones"]
    start = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0).timestamp()
    res = analyze(det, build_rules("demo"), zones, room="Toddlers A (simulated)",
                  calibration=estimate_calibration(det), start_ts=start)
    snaps = OUT / "snap"
    shutil.rmtree(snaps, ignore_errors=True)
    render_snapshots(tmp / "full.avi", det, res, snaps)
    for e in res["events"]:
        e["snapshot"] = f"/sample/snap/{e['snapshot_index']}.jpg"
    res["preset"] = "demo"
    (OUT / "result.json").write_text(json.dumps(res, separators=(",", ":")))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"{len(res['events'])} events:", [(e["rule"], e["t"]) for e in res["events"]])
    for p in sorted(OUT.rglob("*.*")):
        print(f"  {p.relative_to(OUT)}  {p.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    t0 = time.time()
    main()
    print(f"done in {time.time() - t0:.0f}s")
