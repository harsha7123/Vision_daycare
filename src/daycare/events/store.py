"""Event persistence: SQLite rows + snapshot/clip files under <storage>/events/<date>/<event_id>/.

Also a tiny pub/sub so the dashboard gets new events and acknowledgements over SSE.
"""
from __future__ import annotations

import json
import queue
import shutil
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY, ts REAL, rule TEXT, priority TEXT, camera TEXT, room TEXT,
    track_id INTEGER, role TEXT, payload TEXT,
    ack_at REAL, ack_by TEXT, feedback TEXT, escalated_at REAL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS audit (ts REAL, actor TEXT, action TEXT, detail TEXT);
"""
CLIP_CODECS = [(".webm", "VP80"), (".mp4", "avc1"), (".mp4", "mp4v")]


class EventStore:
    def __init__(self, root: str | Path, public_base_url: str = "http://localhost:8000"):
        self.root = Path(root)
        self.media = self.root / "events"
        self.media.mkdir(parents=True, exist_ok=True)
        self.base_url = public_base_url.rstrip("/")
        self._db = sqlite3.connect(self.root / "events.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._codec: tuple[str, str] | None = None

    # ---- pub/sub -------------------------------------------------------------------------------
    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=200)
        self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def publish(self, kind: str, data: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait({"type": kind, "data": data})
            except queue.Full:
                pass

    # ---- media -------------------------------------------------------------------------------
    def event_dir(self, ev: dict) -> Path:
        day = datetime.fromtimestamp(ev["ts"]).strftime("%Y-%m-%d")
        d = self.media / day / ev["event_id"]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _rel(self, p: Path) -> str:
        return "/media/" + p.relative_to(self.media).as_posix()

    def save_snapshot(self, ev: dict, jpeg: bytes) -> None:
        p = self.event_dir(ev) / "snap.jpg"
        p.write_bytes(jpeg)
        ev["snapshot"] = self._rel(p)
        ev["snapshot_url"] = self.base_url + ev["snapshot"]

    def write_clip(self, ev: dict, frames: list[tuple[float, bytes]]) -> None:
        """Encode ring-buffer JPEGs into a browser-playable clip (VP8 webm when available)."""
        if len(frames) < 2:
            return
        imgs = [cv2.imdecode(np.frombuffer(b, np.uint8), cv2.IMREAD_COLOR) for _, b in frames]
        h, w = imgs[0].shape[:2]
        fps = max(1.0, min(30.0, (len(frames) - 1) / max(frames[-1][0] - frames[0][0], 1e-3)))
        d = self.event_dir(ev)
        for ext, fourcc in ([self._codec] if self._codec else CLIP_CODECS):
            path = d / f"clip{ext}"
            vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), fps, (w, h))
            if not vw.isOpened():
                continue
            for im in imgs:
                vw.write(cv2.resize(im, (w, h)) if im.shape[:2] != (h, w) else im)
            vw.release()
            if path.exists() and path.stat().st_size > 1000:
                self._codec = (ext, fourcc)
                rel = self._rel(path)
                self.update_payload(ev["event_id"], clip=rel, clip_url=self.base_url + rel)
                return

    # ---- rows ----------------------------------------------------------------------------------
    def add(self, ev: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO events(event_id, ts, rule, priority, camera, room, track_id, role, payload)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (ev["event_id"], ev["ts"], ev["rule"], ev["priority"], ev["camera"], ev["room"],
                 ev.get("track_id"), ev.get("role"), json.dumps(ev)))
            self._db.commit()
        self.publish("event", self.get(ev["event_id"]))

    def update_payload(self, event_id: str, **fields) -> None:
        with self._lock:
            row = self._db.execute("SELECT payload FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row is None:
                return
            payload = {**json.loads(row["payload"]), **fields}
            self._db.execute("UPDATE events SET payload=? WHERE event_id=?", (json.dumps(payload), event_id))
            self._db.commit()
        self.publish("update", self.get(event_id))

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        ev = json.loads(r["payload"])
        ev["ack"] = {"at": datetime.fromtimestamp(r["ack_at"]).astimezone().isoformat(timespec="seconds"),
                     "by": r["ack_by"], "feedback": r["feedback"]} if r["ack_at"] else None
        ev["escalated"] = bool(r["escalated_at"])
        return ev

    def get(self, event_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, limit: int = 100, camera: str | None = None, rule: str | None = None,
             priority: str | None = None, unacked: bool = False, since: float | None = None) -> list[dict]:
        q, args = "SELECT * FROM events WHERE 1=1", []
        for col, val in (("camera", camera), ("rule", rule), ("priority", priority)):
            if val:
                q += f" AND {col}=?"
                args.append(val)
        if unacked:
            q += " AND ack_at IS NULL"
        if since:
            q += " AND ts>=?"
            args.append(since)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(int(limit))
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [self._row(r) for r in rows]

    def ack(self, event_id: str, by: str = "admin", feedback: str | None = None) -> dict | None:
        if feedback not in (None, "true", "false"):
            raise ValueError("feedback must be 'true', 'false' or null")
        with self._lock:
            cur = self._db.execute(
                "UPDATE events SET ack_at=COALESCE(ack_at, ?), ack_by=COALESCE(ack_by, ?), "
                "feedback=COALESCE(?, feedback) WHERE event_id=?", (time.time(), by, feedback, event_id))
            self._db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), by, "ack", event_id))
            self._db.commit()
        if cur.rowcount == 0:
            return None
        ev = self.get(event_id)
        self.publish("update", ev)
        return ev

    def mark_escalated(self, event_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE events SET escalated_at=? WHERE event_id=?", (time.time(), event_id))
            self._db.commit()
        self.publish("update", self.get(event_id))

    def due_for_escalation(self, age_s: float, priorities=("high", "medium")) -> list[dict]:
        marks = ",".join("?" * len(priorities))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM events WHERE ack_at IS NULL AND escalated_at IS NULL AND ts<=? "
                f"AND priority IN ({marks})", (time.time() - age_s, *priorities)).fetchall()
        return [self._row(r) for r in rows]

    def stats(self, since: float) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT rule, priority, COUNT(*) n, SUM(ack_at IS NOT NULL) acked, SUM(feedback='true') tp, "
                "SUM(feedback='false') fp FROM events WHERE ts>=? GROUP BY rule, priority", (since,)).fetchall()
        return {"by_rule": [dict(r) for r in rows], "total": sum(r["n"] for r in rows),
                "unacked": sum(r["n"] - (r["acked"] or 0) for r in rows)}

    def purge(self, retention_days: float) -> int:
        """Delete events (rows + media) older than the retention period."""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            n = self._db.execute("DELETE FROM events WHERE ts<?", (cutoff,)).rowcount
            self._db.commit()
        for day in self.media.glob("*"):
            try:
                if day.is_dir() and datetime.strptime(day.name, "%Y-%m-%d").timestamp() < cutoff - 86400:
                    shutil.rmtree(day, ignore_errors=True)
            except ValueError:
                pass
        return n

    def close(self) -> None:
        with self._lock:
            self._db.close()
