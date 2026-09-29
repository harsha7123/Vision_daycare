"""Cloud backend for the web app: upload a video -> analysis job -> JSON result + snapshots,
re-analysis with new zones / roles / thresholds, and parent notification (call / SMS via Twilio).

Run locally:    python run_cloud.py            (http://localhost:7860, docs at /docs)
Deploy free:    Hugging Face Spaces (Docker) - see deploy/huggingface/

Environment:
  ALLOWED_ORIGINS        comma-separated CORS origins (e.g. https://vision-daycare.vercel.app); default *
  MAX_UPLOAD_MB          default 150          MAX_VIDEO_S   default 300
  SAMPLE_FPS             default 6            JOB_TTL_S     default 3600 (uploads + results deleted after)
  JOBS_PER_HOUR          per-IP upload limit, default 8
  DAYCARE_DEVICE         auto | cpu | cuda:0
  TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM_NUMBER    enable real calls + SMS
  ALLOWED_CALL_NUMBERS   comma-separated E.164 numbers that may be called (required for real calls)
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from xml.sax.saxutils import escape

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__
from .batch import PRESETS, analyze, build_rules, detect, estimate_calibration, probe, render_snapshots
from .config import load_dotenv, resolve

load_dotenv()
log = logging.getLogger("daycare.cloud")

ENV = os.environ.get
MAX_UPLOAD_MB = float(ENV("MAX_UPLOAD_MB", 150))
MAX_VIDEO_S = float(ENV("MAX_VIDEO_S", 300))
SAMPLE_FPS = float(ENV("SAMPLE_FPS", 6))
JOB_TTL_S = float(ENV("JOB_TTL_S", 3600))
JOBS_PER_HOUR = int(ENV("JOBS_PER_HOUR", 8))
DATA = Path(ENV("DAYCARE_DATA_DIR") or resolve("data")) / "jobs"
VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".mpeg", ".mpg"}
E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class RateLimiter:
    def __init__(self, limit: int, window_s: float = 3600):
        self.limit, self.window = limit, window_s
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            q = self.hits[key]
            while q and q[0] < now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


class Job:
    def __init__(self, path: Path, options: dict):
        self.id = uuid.uuid4().hex[:12]
        self.dir = path.parent
        self.video = path
        self.options = options
        self.created = time.time()
        self.status = "queued"          # queued | detecting | analyzing | done | error
        self.progress = 0.0
        self.error: str | None = None
        self.meta: dict = {}
        self.det = None
        self.calibration = None
        self.result: dict | None = None
        self.started = self.finished = None
        self.lock = threading.Lock()

    def public(self, position: int | None = None) -> dict:
        return {"id": self.id, "status": self.status, "progress": round(self.progress, 3), "error": self.error,
                "meta": self.meta, "position": position, "created": self.created,
                "processing_s": round((self.finished or time.time()) - self.started, 1) if self.started else None}


class JobManager:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.jobs: dict[str, Job] = {}
        self.q: queue.Queue[Job] = queue.Queue()
        self._perception = None
        self._plock = threading.Lock()
        threading.Thread(target=self._worker, daemon=True, name="jobs").start()
        threading.Thread(target=self._janitor, daemon=True, name="janitor").start()

    @property
    def perception(self):
        with self._plock:
            if self._perception is None:
                from .perception.detector import Perception
                self._perception = Perception(
                    {"pose": ENV("POSE_MODEL", "models/yolo11n-pose.pt"), "detector": ENV("DET_MODEL", "models/yolo11s.pt")},
                    {"imgsz": 640, "det_imgsz": int(ENV("DET_IMGSZ", 640)), "person_conf": 0.35, "phone_conf": 0.25,
                     "tracker": "configs/bytetrack.yaml"}, ENV("DAYCARE_DEVICE", "auto"))
            return self._perception

    def submit(self, job: Job) -> int:
        self.jobs[job.id] = job
        self.q.put(job)
        return self.position(job)

    def position(self, job: Job) -> int:
        waiting = [j for j in self.jobs.values() if j.status == "queued"]
        waiting.sort(key=lambda j: j.created)
        return waiting.index(job) + 1 if job in waiting else 0

    def get(self, jid: str) -> Job:
        j = self.jobs.get(jid)
        if j is None:
            raise HTTPException(404, "job not found (results are deleted after an hour)")
        return j

    def delete(self, job: Job) -> None:
        self.jobs.pop(job.id, None)
        shutil.rmtree(job.dir, ignore_errors=True)

    def run_analysis(self, job: Job, opts: dict) -> None:
        rules = build_rules(opts.get("preset", "quick"), opts.get("rules"))
        res = analyze(job.det, rules, opts.get("zones"), opts.get("roles"), opts.get("room") or "Room 1",
                      job.calibration, start_ts=job.created)
        snaps = job.dir / "snapshots"
        shutil.rmtree(snaps, ignore_errors=True)
        render_snapshots(job.video, job.det, res, snaps)
        for e in res["events"]:
            if "snapshot_index" in e:
                e["snapshot"] = f"/api/jobs/{job.id}/snapshots/{e['snapshot_index']}.jpg?v={int(time.time())}"
        res["preset"] = opts.get("preset", "quick")
        if (job.dir / "preview.webm").exists():
            res["preview"] = f"/api/jobs/{job.id}/preview.webm"
        job.result = res
        job.options = opts

    def _worker(self):
        while True:
            job = self.q.get()
            if job.id not in self.jobs:
                continue
            job.started = time.time()
            try:
                job.status = "detecting"
                preview = job.dir / "preview.webm" if job.options.get("preview") else None
                job.det = detect(job.video, self.perception, SAMPLE_FPS,
                                 progress=lambda p: setattr(job, "progress", p * 0.9),
                                 cancelled=lambda: job.id not in self.jobs, preview=preview)
                job.calibration = estimate_calibration(job.det)
                job.status, job.progress = "analyzing", 0.92
                with job.lock:
                    self.run_analysis(job, job.options)
                job.status, job.progress = "done", 1.0
            except Exception as e:
                log.exception("job %s failed", job.id)
                job.status, job.error = "error", f"{type(e).__name__}: {e}"
            job.finished = time.time()

    def _janitor(self):
        while True:
            time.sleep(60)
            for j in list(self.jobs.values()):
                if time.time() - j.created > JOB_TTL_S and j.status in ("done", "error"):
                    self.delete(j)
            for d in DATA.glob("*"):                 # orphans from a previous process
                if d.is_dir() and time.time() - d.stat().st_mtime > JOB_TTL_S and d.name not in self.jobs:
                    shutil.rmtree(d, ignore_errors=True)


# ---- notifications (Twilio REST, no SDK) -----------------------------------------------------------
def twilio_ready() -> bool:
    return all(ENV(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))


def allowed_numbers() -> set[str]:
    return {n.strip() for n in ENV("ALLOWED_CALL_NUMBERS", "").split(",") if n.strip()}


def twilio_post(resource: str, data: dict) -> dict:
    sid = ENV("TWILIO_ACCOUNT_SID")
    r = httpx.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/{resource}.json", data=data,
                   auth=(sid, ENV("TWILIO_AUTH_TOKEN")), timeout=20)
    if r.status_code >= 400:
        raise HTTPException(502, f"Twilio error: {r.json().get('message', r.text)}")
    return r.json()


class NotifyBody(BaseModel):
    to: str = Field(..., description="E.164 phone number, e.g. +919876543210")
    message: str = Field(..., max_length=600)


# ---- app -------------------------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(title="Vision Daycare - video analysis API", version=__version__)
    origins = [o.strip() for o in ENV("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])
    mgr = JobManager()
    upload_limit = RateLimiter(JOBS_PER_HOUR)
    notify_limit = RateLimiter(10)
    app.state.manager = mgr

    def client_ip(req: Request) -> str:
        fwd = req.headers.get("x-forwarded-for")
        return fwd.split(",")[0].strip() if fwd else (req.client.host if req.client else "?")

    @app.get("/")
    def root():
        return {"service": "vision-daycare", "version": __version__, "docs": "/docs"}

    @app.get("/api/health")
    def health():
        return {"ok": True, "queued": mgr.q.qsize(),
                "busy": any(j.status in ("detecting", "analyzing") for j in mgr.jobs.values())}

    @app.get("/api/config")
    def config():
        presets = {name: build_rules(name)["rules"] for name in PRESETS}
        return {"version": __version__, "presets": presets, "sample_fps": SAMPLE_FPS,
                "limits": {"max_upload_mb": MAX_UPLOAD_MB, "max_video_s": MAX_VIDEO_S, "jobs_per_hour": JOBS_PER_HOUR,
                           "retention_s": JOB_TTL_S},
                "notify": {"twilio": twilio_ready(), "allowlist_size": len(allowed_numbers())}}

    @app.post("/api/jobs")
    async def create_job(request: Request, video: UploadFile = File(...), options: str = Form("{}")):
        try:
            opts = json.loads(options or "{}")
            build_rules(opts.get("preset", "quick"), opts.get("rules"))       # validate early
        except (ValueError, TypeError) as e:
            raise HTTPException(422, f"invalid options: {e}")
        ext = Path(video.filename or "").suffix.lower()
        if ext not in VIDEO_EXT:
            raise HTTPException(422, f"unsupported file type {ext or '(none)'}; use mp4, mov, webm, avi or mkv")
        if not upload_limit.allow(client_ip(request)):
            raise HTTPException(429, f"limit of {JOBS_PER_HOUR} videos per hour reached, try again later")
        jid_dir = DATA / uuid.uuid4().hex[:12]
        jid_dir.mkdir(parents=True)
        dest = jid_dir / f"video{ext}"
        size, cap = 0, MAX_UPLOAD_MB * 1024 * 1024
        with open(dest, "wb") as f:
            while chunk := await video.read(1 << 20):
                size += len(chunk)
                if size > cap:
                    f.close()
                    shutil.rmtree(jid_dir, ignore_errors=True)
                    raise HTTPException(413, f"file larger than {MAX_UPLOAD_MB:.0f} MB")
                f.write(chunk)
        try:
            meta = probe(dest)
        except ValueError as e:
            shutil.rmtree(jid_dir, ignore_errors=True)
            raise HTTPException(422, str(e))
        if meta["duration"] > MAX_VIDEO_S:
            shutil.rmtree(jid_dir, ignore_errors=True)
            raise HTTPException(422, f"video is {meta['duration']:.0f}s; the limit is {MAX_VIDEO_S:.0f}s. Trim it first.")
        job = Job(dest, opts)
        job.meta = {**meta, "filename": video.filename, "size_mb": round(size / 1e6, 1)}
        pos = mgr.submit(job)
        return job.public(pos)

    @app.get("/api/jobs/{jid}")
    def job_status(jid: str):
        j = mgr.get(jid)
        return j.public(mgr.position(j))

    @app.get("/api/jobs/{jid}/result")
    def job_result(jid: str):
        j = mgr.get(jid)
        if j.status != "done" or j.result is None:
            raise HTTPException(409, f"job is {j.status}")
        return j.result

    @app.post("/api/jobs/{jid}/rerun")
    def rerun(jid: str, opts: dict):
        j = mgr.get(jid)
        if j.status != "done" or j.det is None:
            raise HTTPException(409, f"job is {j.status}")
        try:
            with j.lock:
                mgr.run_analysis(j, {**j.options, **opts})
        except ValueError as e:
            raise HTTPException(422, str(e))
        return j.result

    @app.get("/api/jobs/{jid}/snapshots/{n}.jpg")
    def snapshot(jid: str, n: int):
        p = mgr.get(jid).dir / "snapshots" / f"{n}.jpg"
        if not p.exists():
            raise HTTPException(404, "snapshot not found")
        return FileResponse(p, media_type="image/jpeg")

    @app.get("/api/jobs/{jid}/preview.webm")
    def preview(jid: str):
        p = mgr.get(jid).dir / "preview.webm"
        if not p.exists():
            raise HTTPException(404, "no preview for this job")
        return FileResponse(p, media_type="video/webm")

    @app.delete("/api/jobs/{jid}")
    def delete_job(jid: str):
        mgr.delete(mgr.get(jid))
        return {"deleted": jid}

    def check_notify(req: Request, body: NotifyBody):
        if not twilio_ready():
            raise HTTPException(503, "real calls are not configured on this server (set TWILIO_* variables)")
        if not E164.match(body.to):
            raise HTTPException(422, "phone number must be in E.164 format, e.g. +919876543210")
        if body.to not in allowed_numbers():
            raise HTTPException(403, "this number is not in ALLOWED_CALL_NUMBERS on the server")
        if not notify_limit.allow(client_ip(req)):
            raise HTTPException(429, "too many calls / messages, try again later")

    @app.post("/api/notify/call")
    def call(req: Request, body: NotifyBody):
        check_notify(req, body)
        msg = escape(body.message)
        twiml = f'<Response><Say voice="Polly.Aditi">{msg}</Say><Pause length="1"/><Say voice="Polly.Aditi">{msg}</Say></Response>'
        r = twilio_post("Calls", {"To": body.to, "From": ENV("TWILIO_FROM_NUMBER"), "Twiml": twiml})
        return {"mode": "twilio", "sid": r.get("sid"), "status": r.get("status")}

    @app.post("/api/notify/sms")
    def sms(req: Request, body: NotifyBody):
        check_notify(req, body)
        r = twilio_post("Messages", {"To": body.to, "From": ENV("TWILIO_FROM_NUMBER"), "Body": body.message})
        return {"mode": "twilio", "sid": r.get("sid"), "status": r.get("status")}

    return app

