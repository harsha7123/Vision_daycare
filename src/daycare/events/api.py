"""FastAPI app: event API, live MJPEG, SSE feed, zone editor, role enrolment, rule tuning, uploads.

No authentication: bind to localhost or reach it over VPN only (blueprint section 9).
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..config import ROOT
from .dispatch import public_payload

DASHBOARD = ROOT / "dashboard"
UPLOAD_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".jpg", ".jpeg", ".png"}
TUNABLE = {"enabled", "threshold_s", "window_s", "cooldown_s", "enter", "exit", "max_children_per_adult",
           "max_motion", "min_conf"}


@dataclass
class Runtime:
    cameras: dict
    store: object
    dispatcher: object
    rules_cfg: dict
    app_cfg: dict
    profile: str = "demo"
    started_at: float = field(default_factory=time.time)


class AckBody(BaseModel):
    by: str = "admin"
    feedback: str | None = None      # "true" | "false" | None


class RoleBody(BaseModel):
    role: str                        # adult | child | auto


class SourceBody(BaseModel):
    source: str


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="Day-Care Caretaker Attention Monitor", version="0.1.0")
    app.mount("/static", StaticFiles(directory=DASHBOARD), name="static")
    app.mount("/media", StaticFiles(directory=rt.store.media), name="media")

    def cam(cid: str):
        c = rt.cameras.get(cid)
        if c is None:
            raise HTTPException(404, f"unknown camera {cid}")
        return c

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(DASHBOARD / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": all(c.error is None for c in rt.cameras.values()), "uptime_s": int(time.time() - rt.started_at),
                "cameras": {cid: {"fps": round(c.fps, 1), "error": c.error} for cid, c in rt.cameras.items()}}

    @app.get("/api/status")
    def status():
        day = time.time() - 86400
        return {"profile": rt.profile, "channels": rt.dispatcher.channel_names,
                "deliveries": rt.dispatcher.sent[-15:][::-1],
                "stats_24h": rt.store.stats(day),
                "cameras": [c.status() for c in rt.cameras.values()]}

    # ---- live video -------------------------------------------------------------------------
    @app.get("/api/cameras/{cid}/stream.mjpg")
    async def stream(cid: str, request: Request):
        c = cam(cid)

        async def gen():
            last = -1
            while not await request.is_disconnected():
                n, data = c.frame_jpeg()
                if n != last and data:
                    last = n
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                           str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                await asyncio.sleep(0.03)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/api/cameras/{cid}/frame.jpg")
    def frame(cid: str, raw: bool = False):
        c = cam(cid)
        data = c.raw_jpeg() if raw else c.frame_jpeg()[1]
        if not data:
            raise HTTPException(503, "no frame yet")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ---- camera control ------------------------------------------------------------------------
    @app.get("/api/cameras/{cid}/zones")
    def get_zones(cid: str):
        return cam(cid).zones()

    @app.put("/api/cameras/{cid}/zones")
    def put_zones(cid: str, body: dict):
        try:
            return cam(cid).set_zones(body)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(422, str(e))

    @app.post("/api/cameras/{cid}/tracks/{tid}/role")
    def set_role(cid: str, tid: int, body: RoleBody):
        try:
            cam(cid).set_role(tid, body.role)
        except (ValueError, RuntimeError) as e:
            raise HTTPException(422, str(e))
        return {"track_id": tid, "role": body.role}

    @app.post("/api/cameras/{cid}/source")
    def set_source(cid: str, body: SourceBody):
        s = body.source.strip()
        if not (s == "sim" or s.isdigit() or re.match(r"^(rtsp|rtsps|http|https)://", s) or Path(s).exists()):
            raise HTTPException(422, "source must be 'sim', a webcam index, an rtsp/http URL or an existing file")
        cam(cid).set_source(s)
        return {"camera": cid, "source": s}

    @app.post("/api/cameras/{cid}/upload")
    async def upload(cid: str, file: UploadFile = File(...)):
        c = cam(cid)
        ext = Path(file.filename or "").suffix.lower()
        if ext not in UPLOAD_EXT:
            raise HTTPException(422, f"unsupported file type {ext!r}")
        up = Path(rt.store.root) / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(file.filename).stem)[:60] + ext
        dest = up / name
        with open(dest, "wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        c.set_source(str(dest))
        return {"camera": cid, "source": str(dest)}

    @app.post("/api/cameras/{cid}/pose")
    def toggle_pose(cid: str, show: bool = True):
        cam(cid).show_pose = show
        return {"show_pose": show}

    # ---- rules -------------------------------------------------------------------------------
    @app.get("/api/rules")
    def get_rules():
        return rt.rules_cfg["rules"]

    @app.put("/api/rules")
    def put_rules(body: dict):
        rules = rt.rules_cfg["rules"]
        for key, changes in body.items():
            if key not in rules or not isinstance(changes, dict):
                raise HTTPException(422, f"unknown rule {key}")
            for k, v in changes.items():
                if k not in TUNABLE:
                    raise HTTPException(422, f"{key}.{k} is not tunable")
                if k == "enabled":
                    rules[key][k] = bool(v)
                else:
                    try:
                        v = float(v)
                    except (TypeError, ValueError):
                        raise HTTPException(422, f"{key}.{k} must be a number")
                    if v < 0:
                        raise HTTPException(422, f"{key}.{k} must be >= 0")
                    rules[key][k] = v
        return rules

    # ---- events ------------------------------------------------------------------------------
    @app.get("/api/events")
    def events(limit: int = 100, camera: str | None = None, rule: str | None = None,
               priority: str | None = None, unacked: bool = False):
        return rt.store.list(limit, camera, rule, priority, unacked)

    @app.get("/api/events/stream")
    async def event_stream(request: Request):
        q = rt.store.subscribe()

        async def gen():
            last_ping = time.time()
            try:
                while not await request.is_disconnected():
                    try:
                        msg = q.get_nowait()
                        yield f"event: {msg['type']}\ndata: {json.dumps(msg['data'])}\n\n"
                        continue
                    except queue.Empty:
                        pass
                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield ": ping\n\n"
                    await asyncio.sleep(0.25)
            finally:
                rt.store.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/events/{event_id}")
    def event(event_id: str, public: bool = False):
        ev = rt.store.get(event_id)
        if ev is None:
            raise HTTPException(404, "event not found")
        return public_payload(ev) if public else ev

    @app.post("/api/events/{event_id}/ack")
    def ack(event_id: str, body: AckBody):
        try:
            ev = rt.store.ack(event_id, body.by, body.feedback)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if ev is None:
            raise HTTPException(404, "event not found")
        return ev

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)

    return app
