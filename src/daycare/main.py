"""Run the edge box: camera pipelines + event service + dashboard in one process.

    python -m daycare                       # synthetic demo scene (no camera, no GPU)
    python -m daycare --source 0            # webcam
    python -m daycare --source clip.mp4     # recorded footage (looped, real time)
    python -m daycare --source rtsp://user:pass@cam/stream --profile production
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import webbrowser

import uvicorn

from .config import load_dotenv, load_rules, load_yaml, resolve
from .events.api import Runtime, create_app
from .events.dispatch import Dispatcher
from .events.store import EventStore
from .pipeline import CameraPipeline
from .rules.engine import RoomPresence


def build_runtime(config: str = "configs/cameras.yaml", profile: str = "demo", source: str | None = None,
                  device: str | None = None, port: int = 8000) -> Runtime:
    load_dotenv()
    app_cfg = load_yaml(config)
    if device:
        app_cfg["device"] = device
    app_cfg["public_base_url"] = app_cfg.get("public_base_url", f"http://localhost:{port}")
    rules_cfg = load_rules(profile)
    alerts_cfg = load_yaml("configs/alerts.yaml")
    data_dir = os.environ.get("DAYCARE_DATA_DIR") or app_cfg.get("storage", {}).get("dir", "data")
    store = EventStore(resolve(data_dir), app_cfg["public_base_url"])
    store.purge(app_cfg.get("privacy", {}).get("retention_days", 30))
    dispatcher = Dispatcher(alerts_cfg, rules_cfg, store)
    presence = RoomPresence()
    cams = app_cfg.get("cameras") or [{"id": "cam01", "room": "Room 1"}]
    if source is not None:
        cams[0]["source"] = source
    pipelines = {c["id"]: CameraPipeline(c, app_cfg, rules_cfg, store, dispatcher, presence) for c in cams}
    return Runtime(pipelines, store, dispatcher, rules_cfg, app_cfg, profile)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="daycare", description="Day-care caretaker attention monitor")
    ap.add_argument("--source", help="sim | webcam index | video/image path | rtsp:// URL (overrides cam 1)")
    ap.add_argument("--config", default="configs/cameras.yaml")
    ap.add_argument("--profile", default="demo", help="rules profile: demo (short thresholds) | production")
    ap.add_argument("--device", help="auto | cpu | cuda:0")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    rt = build_runtime(args.config, args.profile, args.source, args.device, args.port)
    for c in rt.cameras.values():
        c.start()
    url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{args.port}"
    logging.getLogger("daycare").info("dashboard: %s  (profile=%s, alert channels=%s)",
                                      url, args.profile, ",".join(rt.dispatcher.channel_names))
    if args.open:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(create_app(rt), host=args.host, port=args.port, log_level="warning")
    finally:
        for c in rt.cameras.values():
            c.stop()


if __name__ == "__main__":
    main()
