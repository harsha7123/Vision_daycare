"""Measure live-mode latency the way the browser experiences it, and write a report.

Streams frames from the laptop camera (or a video file) to the live endpoint, one frame in
flight at a time, and times every step. Writes latency-report-<time>.json and .html.

    python tools/latency_benchmark.py                       # laptop camera 0, 30 s, local server
    python tools/latency_benchmark.py --source clip.mp4     # a video file
    python tools/latency_benchmark.py --url wss://<ec2-host> --seconds 60
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2


def pct(a, p):
    s = sorted(a)
    return s[min(len(s) - 1, int(p / 100 * len(s)))] if s else None


def summary(a):
    return {"median": pct(a, 50), "p95": pct(a, 95), "min": min(a) if a else None, "max": max(a) if a else None,
            "avg": st.mean(a) if a else None}


async def run(args):
    import websockets
    src = int(args.source) if str(args.source).isdigit() else args.source
    cap = cv2.VideoCapture(src, cv2.CAP_DSHOW) if isinstance(src, int) and sys.platform == "win32" else cv2.VideoCapture(src)
    if not cap.isOpened():
        sys.exit(f"cannot open source {args.source}")
    url = args.url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + \
        f"/ws/live?preset={args.preset}&roles=all_adult"
    frames, alerts, phone_start = [], [], {}
    async with websockets.connect(url, max_size=None, open_timeout=90) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "hello":
            sys.exit(f"server error: {hello}")
        print(f"connected: {hello.get('gpu') or hello['device']} ({hello['device']}); streaming for {args.seconds} s ...")
        rules = hello["rules"]
        t_end = time.time() + args.seconds
        while time.time() < t_end:
            t0 = time.perf_counter()
            ok, img = cap.read()
            if not ok:                                   # end of a video file: start it again
                cap.release()
                cap = cv2.VideoCapture(src)
                continue
            h, w = img.shape[:2]
            if w > args.width:
                img = cv2.resize(img, (args.width, int(h * args.width / w)), interpolation=cv2.INTER_AREA)
            jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])[1].tobytes()
            t1 = time.perf_counter()
            await ws.send(jpg)
            m = json.loads(await ws.recv())
            t2 = time.perf_counter()
            if m.get("type") != "frame":
                continue
            rtt = (t2 - t1) * 1000
            frames.append({"capture_encode": (t1 - t0) * 1000, "network": max(0.0, rtt - m["ms"]["total"]),
                           "decode": m["ms"]["decode"], "ai": m["ms"]["infer"],
                           "rules": max(0, m["ms"]["total"] - m["ms"]["decode"] - m["ms"]["infer"]),
                           "rtt": rtt, "e2e": (t2 - t0) * 1000, "people": len(m["p"]), "at": time.time()})
            for p in m["p"]:
                if p[7] > 0 and p[0] not in phone_start:
                    phone_start[p[0]] = time.time() - p[7]
                if p[7] == 0 and p[6] < 0.2:
                    phone_start.pop(p[0], None)
            for e in m["events"]:
                start = phone_start.get(e["track_id"]) if e["rule"] == "R1_phone_use" else time.time() - e["duration_s"]
                alerts.append({"at": datetime.now().isoformat(timespec="seconds"), "rule": e["rule"], "title": e["title"],
                               "priority": e["priority"], "track_id": e["track_id"],
                               "threshold_s": rules.get(e["rule"], {}).get("threshold_s"),
                               "from_detection_s": round(time.time() - start, 2) if start else None,
                               "system_ms": round((t2 - t0) * 1000)})
                print(f"  ALERT {e['priority']}: {e['title']}")
            if len(frames) % 20 == 0:
                print(f"  {len(frames)} frames, last end-to-end {frames[-1]['e2e']:.0f} ms, AI {m['ms']['infer']} ms")
    cap.release()
    frames = frames[5:]                                    # skip warm-up
    dur = frames[-1]["at"] - frames[0]["at"] if len(frames) > 1 else 0
    col = lambda k: [f[k] for f in frames]
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"), "server": {k: hello.get(k) for k in ("device", "gpu", "models")},
        "source": str(args.source), "send_width": args.width, "frames": len(frames), "duration_s": round(dur, 1),
        "fps": round(len(frames) / dur, 1) if dur else 0,
        "end_to_end": summary(col("e2e")),
        "breakdown": {k: summary(col(k)) for k in ("capture_encode", "network", "decode", "ai", "rules")},
        "alerts": alerts,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"latency-report-{stamp}.json").write_text(json.dumps(report, indent=2))
    (out / f"latency-report-{stamp}.html").write_text(html(report), encoding="utf-8")
    print_report(report)
    print(f"\nsaved {out / f'latency-report-{stamp}.html'} and .json")


def fmt(v):
    return "-" if v is None else f"{v:.0f} ms"


def print_report(r):
    print("\n=== Live latency report ===")
    print(f"server: {r['server']['gpu'] or r['server']['device']}  | source: {r['source']} | {r['frames']} frames in {r['duration_s']} s = {r['fps']} fps")
    e = r["end_to_end"]
    print(f"END TO END (capture -> result): median {fmt(e['median'])}, p95 {fmt(e['p95'])}, min {fmt(e['min'])}, max {fmt(e['max'])}")
    for k, s in r["breakdown"].items():
        print(f"  {k:15s} median {fmt(s['median']):>8}   p95 {fmt(s['p95']):>8}")
    for a in r["alerts"]:
        print(f"  alert {a['title']}: rule waits {a['threshold_s']} s, measured {a['from_detection_s']} s from detection, system {a['system_ms']} ms")


def html(r):
    e, b = r["end_to_end"], r["breakdown"]
    rows = "".join(f"<tr><td>{k.replace('_', ' ')}</td><td>{fmt(s['median'])}</td><td>{fmt(s['p95'])}</td><td>{fmt(s['min'])}</td><td>{fmt(s['max'])}</td></tr>"
                   for k, s in [("end_to_end", e), *b.items()])
    al = "".join(f"<tr><td>{a['at']}</td><td>{a['title']}</td><td>{a['threshold_s']} s</td><td>{a['from_detection_s']} s</td><td>{a['system_ms']} ms</td></tr>"
                 for a in r["alerts"]) or "<tr><td colspan=5>No alerts in this run</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Live latency report</title>
<style>body{{font:14px system-ui;margin:32px;color:#1f2328;max-width:900px}}h1{{font-size:22px}}table{{border-collapse:collapse;width:100%;margin:10px 0 24px}}
td,th{{border-bottom:1px solid #dde2e7;padding:6px 8px;text-align:left}}th{{color:#5b6670}}.k{{display:inline-block;margin:0 24px 12px 0}}.k b{{font-size:26px;display:block}}</style></head>
<body><h1>Vision Daycare: live latency report</h1>
<p>{r['generated_at']} · server <b>{r['server']['gpu'] or r['server']['device']}</b> ({r['server']['device']}) · models {r['server']['models']} · source {r['source']} · frames sent at {r['send_width']} px wide</p>
<div class="k"><b>{fmt(e['median'])}</b>typical delay (median)</div><div class="k"><b>{fmt(e['p95'])}</b>slowest 5% (p95)</div>
<div class="k"><b>{r['fps']}</b>frames per second</div><div class="k"><b>{fmt(b['ai']['median'])}</b>AI per frame</div>
<h2>Breakdown</h2><table><tr><th>Step</th><th>Median</th><th>p95</th><th>Min</th><th>Max</th></tr>{rows}</table>
<h2>Alerts</h2><table><tr><th>Time</th><th>Alert</th><th>Rule waits</th><th>Measured from detection</th><th>System delay</th></tr>{al}</table>
<p style="color:#5b6670">End to end = frame captured until its analysis is back. Camera hardware adds ~30-100 ms before a frame is available; that part can't be measured in software.</p>
</body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="0", help="camera index (0 = laptop camera) or video file")
    ap.add_argument("--url", default="http://localhost:7860")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--preset", default="quick")
    ap.add_argument("--out", default="reports")
    asyncio.run(run(ap.parse_args()))
