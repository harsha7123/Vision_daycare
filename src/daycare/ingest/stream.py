"""Frame sources (sim / webcam / RTSP / video file / image) and the clip ring buffer.

Every source yields (frame_bgr, timestamp, persons_or_None, phones_or_None). Only the
simulator provides detections; real sources return None and go through YOLO.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


class RingBuffer:
    """Last `seconds` of JPEG-encoded frames, for pre-event clips."""

    def __init__(self, seconds: float = 15.0):
        self.seconds = seconds
        self._q: deque[tuple[float, bytes]] = deque()
        self._lock = threading.Lock()

    def push(self, ts: float, jpeg: bytes) -> None:
        with self._lock:
            self._q.append((ts, jpeg))
            while self._q and self._q[0][0] < ts - self.seconds:
                self._q.popleft()

    def since(self, ts: float) -> list[tuple[float, bytes]]:
        with self._lock:
            return [x for x in self._q if x[0] >= ts]


def _fit(frame: np.ndarray, max_width: int) -> np.ndarray:
    h, w = frame.shape[:2]
    if max_width and w > max_width:
        frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
    return frame


class _Paced:
    def __init__(self, fps: float):
        self.period = 1.0 / max(fps, 0.1)
        self.next = time.monotonic()

    def wait(self) -> None:
        self.next += self.period
        delay = self.next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            self.next = time.monotonic()      # running behind: don't try to catch up


class Source:
    kind = "base"
    provides_detections = False

    def __init__(self, spec, fps: float, max_width: int = 1280):
        self.spec, self.fps, self.max_width = spec, fps, max_width
        self._stop = threading.Event()
        self.error: str | None = None

    @property
    def label(self) -> str:
        return f"{self.kind}:{Path(str(self.spec)).name}" if self.kind in ("file", "image") else f"{self.kind}:{self.spec}"

    def close(self) -> None:
        self._stop.set()

    def __iter__(self):
        raise NotImplementedError


class SimSource(Source):
    kind = "sim"
    provides_detections = True

    def __init__(self, fps: float, calibration: dict | None = None, **_):
        super().__init__("sim", fps)
        from ..sim import LOOP_S, SimScene
        self.scene, self.loop_s = SimScene(calibration), LOOP_S

    @property
    def label(self) -> str:
        return "sim"

    def __iter__(self):
        start, pace = time.time(), _Paced(self.fps)
        while not self._stop.is_set():
            now = time.time()
            t = now - start
            frame, persons, phones = self.scene.render(t % self.loop_s, int(t // self.loop_s))
            yield frame, now, persons, phones
            pace.wait()


class CaptureSource(Source):
    """Webcam index or RTSP/HTTP URL. A grabber thread keeps only the newest frame (no lag build-up)."""

    def __init__(self, spec, fps: float, max_width: int = 1280, **_):
        super().__init__(spec, fps, max_width)
        self.kind = "webcam" if isinstance(spec, int) else "stream"
        self._latest: tuple[float, np.ndarray] | None = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._grab, daemon=True)
        self._thread.start()

    def _open(self):
        if isinstance(self.spec, int) and sys.platform == "win32":
            return cv2.VideoCapture(self.spec, cv2.CAP_DSHOW)
        if isinstance(self.spec, str) and self.spec.startswith("rtsp"):
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        return cv2.VideoCapture(self.spec)

    def _grab(self):
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.error = f"cannot open {self.spec}; retrying"
                time.sleep(2)
                continue
            self.error = None
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    self.error = f"{self.spec} stopped delivering frames; reconnecting"
                    break
                with self._lock:
                    self._latest = (time.time(), frame)
            cap.release()
            time.sleep(1)

    def __iter__(self):
        pace, last = _Paced(self.fps), None
        while not self._stop.is_set():
            with self._lock:
                item = self._latest
            if item is not None and item[0] != last:
                last = item[0]
                yield _fit(item[1], self.max_width), item[0], None, None
            pace.wait()


class FileSource(Source):
    """Video file, sampled down to `fps`, paced to real time and looped (demo friendly)."""
    kind = "file"

    def __init__(self, spec, fps: float, max_width: int = 1280, realtime: bool = True, loop: bool = True, **_):
        super().__init__(spec, fps, max_width)
        self.realtime, self.loop = realtime, loop

    def __iter__(self):
        t0 = time.time()
        offset = 0.0
        while not self._stop.is_set():
            cap = cv2.VideoCapture(str(self.spec))
            if not cap.isOpened():
                self.error = f"cannot open {self.spec}"
                return
            src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            step = max(1, round(src_fps / self.fps))
            pace, idx, dur = _Paced(src_fps / step), 0, 0.0
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                if idx % step == 0:
                    dur = idx / src_fps
                    ts = time.time() if self.realtime else t0 + offset + dur
                    yield _fit(frame, self.max_width), ts, None, None
                    if self.realtime:
                        pace.wait()
                idx += 1
            cap.release()
            offset += dur + 1.0 / src_fps
            if not self.loop:
                return


class ImageSource(Source):
    kind = "image"

    def __iter__(self):
        img = cv2.imread(str(self.spec))
        if img is None:
            self.error = f"cannot read {self.spec}"
            return
        img, pace = _fit(img, self.max_width), _Paced(self.fps)
        while not self._stop.is_set():
            yield img.copy(), time.time(), None, None
            pace.wait()


def parse_spec(spec):
    if isinstance(spec, int):
        return spec
    s = str(spec).strip()
    return int(s) if s.isdigit() else s


def open_source(spec, fps: float = 12, max_width: int = 1280, calibration: dict | None = None,
                realtime: bool = True, loop: bool = True) -> Source:
    spec = parse_spec(spec)
    if spec == "sim":
        return SimSource(fps, calibration)
    if isinstance(spec, int) or "://" in spec:
        return CaptureSource(spec, fps, max_width)
    p = Path(spec)
    if not p.exists():
        raise FileNotFoundError(f"video source not found: {spec}")
    if p.suffix.lower() in IMAGE_EXT:
        return ImageSource(p, fps, max_width)
    return FileSource(p, fps, max_width, realtime=realtime, loop=loop)
