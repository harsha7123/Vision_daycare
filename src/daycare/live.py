"""Live analysis of frames streamed from a browser (laptop camera or phone) over a WebSocket.

Each connection gets its own tracker + rule state. The browser sends one JPEG, waits for the
answer, then sends the next frame, so frames never queue up and latency stays at
one network round trip + one inference (~40-120 ms on a GPU server close to the user).
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from .analyzer import Analyzer
from .annotate import BGR, PRIORITY_BGR, _text, blur_faces, draw_zones
from .batch import build_rules
from .perception.zones import ZoneSet

MAX_SIDE = 1280          # frames larger than this are downscaled before inference


class LiveSession:
    def __init__(self, perception, out_dir: Path, preset: str = "quick", room: str = "Live camera",
                 rules: dict | None = None, zones: list | None = None, role_mode: str = "auto"):
        self.id = uuid.uuid4().hex[:12]
        self.dir = out_dir / self.id
        self.perception = perception
        self.perception.reset()
        self.preset, self.room = preset, room
        self.rules_cfg = build_rules(preset, rules)
        self.analyzer = Analyzer("live", room, {"mode": role_mode if role_mode in ("auto", "all_adult") else "auto"}, self.rules_cfg, ZoneSet(zones or []))
        self.started = time.time()
        self.frames = 0
        self.events: list[dict] = []

    # ---- controls from the browser --------------------------------------------------------
    def control(self, msg: dict) -> dict:
        kind = msg.get("type")
        if kind == "role":
            self.analyzer.roles.set_override(int(msg["track_id"]), msg.get("role", "auto"))
        elif kind == "role_mode":
            mode = msg.get("mode", "auto")
            if mode not in ("auto", "all_adult"):
                raise ValueError("role mode must be auto or all_adult")
            self.analyzer.roles.mode = mode
        elif kind == "zones":
            self.analyzer.zones = ZoneSet(msg.get("zones") or [])
        elif kind == "rules":
            cfg = build_rules(msg.get("preset", self.preset), msg.get("rules"))
            self.preset = msg.get("preset", self.preset)
            self.rules_cfg["rules"].clear()
            self.rules_cfg["rules"].update(cfg["rules"])      # engine holds a reference to this dict
        else:
            raise ValueError(f"unknown control message {kind!r}")
        out = {"type": "ack", "for": kind}
        if kind == "rules":
            out["rules"] = self.rules_cfg["rules"]
        return out

    # ---- one frame ----------------------------------------------------------------------------
    def process(self, jpeg: bytes) -> dict:
        t0 = time.perf_counter()
        frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("frame is not a valid JPEG")
        h, w = frame.shape[:2]
        if max(h, w) > MAX_SIDE:
            s = MAX_SIDE / max(h, w)
            frame = cv2.resize(frame, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
            h, w = frame.shape[:2]
        now = time.time()
        t1 = time.perf_counter()
        persons, phones = self.perception(frame)
        t2 = time.perf_counter()
        new_events = self.analyzer.process(frame, persons, phones, now)
        eng = self.analyzer.engine
        recs, kps = [], []
        for p in persons:
            s = eng.tracks.get(p.track_id)
            if s is None:
                continue
            alert = any(now - ts < 4 for ts in s.alerted.values())
            recs.append([p.track_id, *[int(v) for v in p.xyxy], "c" if s.role == "child" else "a",
                         round(s.phone_ema, 2), round(s.phone_t, 1), round(s.out_t, 1), round(s.lying_t, 1),
                         round(s.idle_t, 1), 1 if alert else 0])
            kps.append(np.round(p.kpts[:, :2]).astype(int).ravel().tolist() if p.kpts is not None else [])
        for e in new_events:
            e["t"] = round(now - self.started, 2)
            e["snapshot"] = self._snapshot(frame, persons, e, len(self.events))
            self.events.append(e)
        self.frames += 1
        c = eng.counts
        return {
            "type": "frame", "w": w, "h": h, "t": round(now - self.started, 2),
            "p": recs, "k": kps, "ph": [[*[int(v) for v in ph.xyxy], round(ph.conf, 2)] for ph in phones],
            "c": [c["children"], c["adults"]], "u": round(eng.unattended_t, 1),
            "events": new_events,
            "tracks": [{"track_id": s.track_id, "role": s.role, "p_child": round(s.p_child, 2),
                        "override": self.analyzer.roles.overrides.get(s.track_id)}
                       for s in eng.tracks.values() if now - s.last_seen < 2],
            "ms": {"decode": round((t1 - t0) * 1000), "infer": round((t2 - t1) * 1000),
                   "total": round((time.perf_counter() - t0) * 1000)},
        }

    def _snapshot(self, frame, persons, e: dict, i: int) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        img = blur_faces(frame, persons)
        draw_zones(img, self.analyzer.zones)
        for p in persons:
            s = self.analyzer.engine.tracks.get(p.track_id)
            role = s.role if s else "adult"
            hot = e.get("track_id") == p.track_id or (e.get("track_id") is None and role == "child")
            x1, y1, x2, y2 = p.xyxy.astype(int)
            cv2.rectangle(img, (x1, y1), (x2, y2), BGR["alert"] if hot else BGR[role], 3)
            _text(img, f"#{p.track_id} {role}", (x1, max(y1 - 6, 14)), 0.55, (255, 255, 255), BGR["alert"] if hot else BGR[role])
        h, w = img.shape[:2]
        cv2.rectangle(img, (0, h - 40), (w, h), PRIORITY_BGR.get(e["priority"], (0, 0, 200)), -1)
        _text(img, f"{e['priority'].upper()}  {e['title']}  -  {e['duration_s']}s", (12, h - 13), 0.7, (255, 255, 255), None, 2)
        cv2.imwrite(str(self.dir / f"{i}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        return f"/api/live/{self.id}/snapshots/{i}.jpg"
