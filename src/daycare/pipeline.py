"""One camera end to end, in a background thread:
grab frame -> detect + track (YOLO/ByteTrack, or the simulator) -> analyzer (roles, phone, zones, rules)
-> annotated live frame + ring buffer -> events (snapshot, clip, store, dispatch)."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from pathlib import Path

import cv2

from . import annotate
from .analyzer import Analyzer
from .config import resolve
from .ingest.stream import RingBuffer, open_source, parse_spec
from .perception.zones import ZoneSet

log = logging.getLogger("daycare.pipeline")
SIM_ZONES = "configs/zones/sim.json"


def jpeg(img, quality: int = 80) -> bytes:
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


class CameraPipeline:
    def __init__(self, cam_cfg: dict, app_cfg: dict, rules_cfg: dict, store, dispatcher, presence=None):
        self.cfg, self.app_cfg, self.rules_cfg = cam_cfg, app_cfg, rules_cfg
        self.id, self.room = cam_cfg["id"], cam_cfg.get("room", cam_cfg["id"])
        self.fps_target = float(cam_cfg.get("fps", 12))
        self.store, self.dispatcher, self.presence = store, dispatcher, presence
        self.privacy = app_cfg.get("privacy", {})
        self.source_spec = parse_spec(cam_cfg.get("source", "sim"))
        self.source = None
        self.analyzer: Analyzer | None = None
        self.perception = None
        self.classifier = None
        self.action_model = None
        self.ring = RingBuffer(15.0)
        self.latest_jpeg: bytes = b""
        self.latest_raw = None
        self.frame_no = 0
        self.fps = 0.0
        self.error: str | None = None
        self.show_pose = True
        self.phase = "starting"
        self._restart = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"cam-{self.id}")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.source:
            self.source.close()

    def set_source(self, spec) -> None:
        self.source_spec = parse_spec(spec)
        self._restart.set()
        if self.source:
            self.source.close()

    @property
    def is_sim(self) -> bool:
        return self.source_spec == "sim"

    @property
    def zones_path(self) -> Path:
        return resolve(SIM_ZONES if self.is_sim else self.cfg.get("zones", f"configs/zones/{self.id}.json"))

    # ---- models (lazy: the simulator needs no YOLO) --------------------------------------------
    def _ensure_models(self) -> None:
        if self.perception is not None:
            return
        from .perception.detector import Perception
        models = self.app_cfg.get("models", {})
        self.perception = Perception(models, self.app_cfg.get("perception", {}), self.app_cfg.get("device", "auto"))
        cls_w = resolve(models.get("role_classifier", "models/role_cls.pt"))
        if cls_w.exists():
            from .perception.role import CropClassifier
            self.classifier = CropClassifier(cls_w, self.perception.device)
            log.info("%s: role classifier %s", self.id, cls_w)
        self._load_action_model(models)

    def _load_action_model(self, models: dict) -> None:
        act_w = resolve(models.get("action", "models/pose_gru.pt"))
        if act_w.exists() and self.action_model is None:
            from .actions.pose_gru import ActionRecognizer
            self.action_model = ActionRecognizer(act_w)
            log.info("%s: action model %s", self.id, act_w)

    def _new_analyzer(self) -> None:
        classifier = None if self.is_sim else self.classifier
        self.analyzer = Analyzer(self.id, self.room, self.cfg.get("role", {}), self.rules_cfg,
                                 ZoneSet.load(self.zones_path), classifier, self.action_model, self.presence)

    # ---- main loop -----------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._restart.clear()
            self.source, self.analyzer, self.latest_raw = None, None, None
            self.phase = "loading models" if not self.is_sim and self.perception is None else "starting"
            try:
                if self.is_sim:
                    self._load_action_model(self.app_cfg.get("models", {}))
                else:
                    self._ensure_models()
                    self.perception.reset()
                self._new_analyzer()
                self.source = open_source(self.source_spec, self.fps_target,
                                          self.app_cfg.get("perception", {}).get("max_width", 1280),
                                          calibration=self.cfg.get("role", {}).get("calibration"))
                self.error = None
                self.phase = "running"
                log.info("%s: source %s", self.id, self.source.label)
                self._consume(self.source)
                if self.source.error:
                    self.error = self.source.error
            except Exception as e:
                self.error = f"{type(e).__name__}: {e}"
                log.error("%s: %s\n%s", self.id, self.error, traceback.format_exc())
            if not self._stop.is_set() and not self._restart.is_set():
                self._restart.wait(2.0)

    def _consume(self, source) -> None:
        t_prev = None
        for frame, ts, persons, phones in source:
            if self._stop.is_set() or self._restart.is_set():
                break
            if persons is None:
                persons, phones = self.perception(frame)
            events = self.analyzer.process(frame, persons, phones, ts)
            now_m = time.monotonic()
            if t_prev is not None:
                inst = 1.0 / max(now_m - t_prev, 1e-3)
                self.fps = inst if self.fps == 0 else 0.9 * self.fps + 0.1 * inst
            t_prev = now_m
            img = annotate.render(frame, self.analyzer, ts, self.fps, source.label,
                                  blur=self.privacy.get("blur_live", False), show_pose=self.show_pose)
            data = jpeg(img)
            with self._lock:
                self.latest_jpeg, self.latest_raw = data, frame
                self.frame_no += 1
            self.ring.push(ts, data)
            for ev in events:
                try:
                    self._emit(ev, frame, ts)
                except Exception as e:          # a failed save/alert must never stop the camera
                    log.error("%s: emitting %s failed: %s", self.id, ev["event_id"], e)
            if source.error:
                self.error = source.error

    def _emit(self, ev: dict, frame, ts: float) -> None:
        snap = annotate.render(frame, self.analyzer, ts, self.fps, "",
                               blur=self.privacy.get("blur_snapshots", True), show_pose=False)
        self.store.save_snapshot(ev, jpeg(snap, 85))
        self.store.add(ev)
        self.dispatcher.dispatch(ev)
        # clip = 10 s before + 3 s after the threshold crossing, from the ring buffer
        threading.Timer(3.0, lambda: self.store.write_clip(ev, self.ring.since(ts - 10))).start()

    # ---- dashboard hooks ---------------------------------------------------------------------------
    def frame_jpeg(self) -> tuple[int, bytes]:
        with self._lock:
            return self.frame_no, self.latest_jpeg

    def raw_jpeg(self) -> bytes:
        with self._lock:
            return jpeg(self.latest_raw) if self.latest_raw is not None else b""

    def set_zones(self, data: dict) -> dict:
        zs = ZoneSet(data.get("zones", []))
        zs.save(self.zones_path)
        if self.analyzer:
            self.analyzer.zones = zs
        return zs.to_dict()

    def zones(self) -> dict:
        return (self.analyzer.zones if self.analyzer else ZoneSet.load(self.zones_path)).to_dict()

    def set_role(self, track_id: int, role: str) -> None:
        if self.analyzer is None:
            raise RuntimeError("camera not running")
        self.analyzer.roles.set_override(track_id, role)

    def status(self) -> dict:
        st = self.analyzer.engine.status() if self.analyzer else {"counts": {}, "timers": {}, "tracks": []}
        overrides = self.analyzer.roles.overrides if self.analyzer else {}
        for t in st["tracks"]:
            t["override"] = overrides.get(t["track_id"])
        h, w = (self.latest_raw.shape[:2] if self.latest_raw is not None else (0, 0))
        return {"id": self.id, "room": self.room, "source": str(self.source_spec),
                "source_label": self.source.label if self.source else None,
                "fps": round(self.fps, 1), "error": self.error, "frame": [w, h], "phase": self.phase,
                "models": {"yolo": self.perception is not None,
                           "device": self.perception.device if self.perception else None,
                           "role_classifier": self.classifier is not None,
                           "action_model": self.action_model is not None},
                **st}
