"""R1..R6 evaluation with timers, hysteresis, cooldowns and schedule (blueprint sections 1 & 6)."""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, time as dtime
from statistics import mean

from ..types import Observation
from .state import TrackState

RULE_TITLES = {
    "R1_phone_use": "Caretaker on phone",
    "R2_left_zone": "Caretaker left the child zone",
    "R3_unattended": "Children unattended",
    "R4_ratio": "Adult : child ratio exceeded",
    "R5_fall": "Child fall / lying still",
    "R6_idle": "Caretaker idle / asleep",
}


def get_tz(name: str | None):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name) if name else None
    except Exception:          # no tz database (Windows without tzdata): fall back to local time
        return None


def _parse_range(s: str | None):
    if not s:
        return None
    a, b = s.split("-")
    return dtime.fromisoformat(a.strip()), dtime.fromisoformat(b.strip())


def _in_range(t: dtime, rng) -> bool:
    a, b = rng
    return a <= t <= b if a <= b else (t >= a or t <= b)


class RoomPresence:
    """Adults in the play zone per (room, camera): a caretaker counts if ANY camera in the room sees them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._d: dict[tuple[str, str], tuple[int, float]] = {}

    def report(self, room: str, cam: str, adults: int, now: float) -> None:
        with self._lock:
            self._d[(room, cam)] = (adults, now)

    def adults_elsewhere(self, room: str, cam: str, now: float, fresh_s: float = 2.0) -> int:
        with self._lock:
            return sum(n for (r, c), (n, t) in self._d.items() if r == room and c != cam and now - t <= fresh_s)


class RuleEngine:
    def __init__(self, cfg: dict, camera_id: str, room: str, presence: RoomPresence | None = None):
        self.cfg = cfg
        self.camera_id, self.room = camera_id, room
        self.presence = presence
        sched = cfg.get("schedule", {})
        self.tz = get_tz(sched.get("timezone"))
        self.active_hours = _parse_range(sched.get("active_hours"))
        self.nap_hours = _parse_range(sched.get("nap_hours"))
        self.skip_during_nap = set(sched.get("skip_zones_during_nap", []))
        self.tracks: dict[int, TrackState] = {}
        self.unattended_t = 0.0
        self.ratio_t = 0.0
        self.cooldown_until: dict[tuple, float] = {}
        self.last_step: float | None = None
        self.forgotten: list[int] = []
        self.counts = {"children": 0, "adults": 0, "ratio": 0.0}
        self.active = True
        self.recent: deque = deque(maxlen=20)

    # ---- helpers ---------------------------------------------------------------------------
    def rule(self, key: str) -> dict | None:
        r = self.cfg["rules"].get(key)
        return r if r and r.get("enabled", True) else None

    def local_dt(self, now: float) -> datetime:
        return datetime.fromtimestamp(now, self.tz) if self.tz else datetime.fromtimestamp(now).astimezone()

    def schedule_state(self, now: float) -> tuple[bool, bool]:
        t = self.local_dt(now).time()
        active = self.active_hours is None or _in_range(t, self.active_hours)
        nap = self.nap_hours is not None and _in_range(t, self.nap_hours)
        return active, nap

    def _fire(self, rule_key: str, now: float, key, duration: float, confidence: float,
              track: TrackState | None = None) -> dict | None:
        r = self.cfg["rules"][rule_key]
        ck = (rule_key, key)
        if now < self.cooldown_until.get(ck, 0.0):
            return None
        self.cooldown_until[ck] = now + float(r.get("cooldown_s", 600))
        ts = self.local_dt(now)
        rid = rule_key.split("_")[0]
        eid = f"evt_{ts:%Y%m%d_%H%M%S}_{self.camera_id}_{rid}" + (f"_t{track.track_id}" if track else "")
        ev = {
            "event_id": eid, "rule": rule_key, "title": RULE_TITLES.get(rule_key, rule_key),
            "priority": r.get("priority", "medium"), "camera": self.camera_id, "room": self.room,
            "track_id": track.track_id if track else None, "role": track.role if track else None,
            "started_at": self.local_dt(now - duration).isoformat(timespec="seconds"),
            "created_at": ts.isoformat(timespec="seconds"), "ts": now,
            "duration_s": int(round(duration)),
            "children_in_zone": self.counts["children"], "adults_in_zone": self.counts["adults"],
            "confidence": round(float(confidence), 2),
            "snapshot_url": None, "clip_url": None, "ack": None,
            "box": [round(float(v)) for v in track.xyxy] if track is not None and track.xyxy is not None else None,
        }
        if track is not None:
            track.alerted[rule_key] = now
        self.recent.append(ev)
        return ev

    # ---- main step ---------------------------------------------------------------------------
    def step(self, obs: list[Observation], now: float) -> list[dict]:
        tc = self.cfg.get("tracking", {})
        max_dt = tc.get("max_dt_s", 1.0)
        room_dt = 0.0 if self.last_step is None else min(max(now - self.last_step, 0.0), max_dt)
        self.last_step = now

        for o in obs:
            st = self.tracks.get(o.track_id)
            if st is None:
                st = self.tracks[o.track_id] = TrackState(o.track_id, now, now)
                dt = 0.0
            else:
                dt = min(max(now - st.last_seen, 0.0), max_dt)
            st.update(o, now, dt, self.cfg)

        self.forgotten = [t for t, s in self.tracks.items() if now - s.last_seen > tc.get("forget_after_s", 10)]
        for t in self.forgotten:
            del self.tracks[t]

        grace = tc.get("presence_grace_s", 1.0)
        present = [s for s in self.tracks.values() if now - s.last_seen <= grace]
        kids = [s for s in present if s.role == "child" and "play" in s.zones]
        adults = [s for s in present if s.role == "adult" and "play" in s.zones]
        n_adults = len(adults)
        if self.presence:
            self.presence.report(self.room, self.camera_id, n_adults, now)
            n_adults += self.presence.adults_elsewhere(self.room, self.camera_id, now)
        self.counts = {"children": len(kids), "adults": n_adults,
                       "ratio": round(len(kids) / n_adults, 1) if n_adults else (None if kids else 0.0)}

        self.active, nap = self.schedule_state(now)
        if not self.active:
            self.unattended_t = self.ratio_t = 0.0
            return []
        play_on = not (nap and "play" in self.skip_during_nap)
        kid_conf = mean(s.p_child for s in kids) if kids else 0.0
        events: list[dict | None] = []

        # R3 children unattended (room level)
        self.unattended_t = self.unattended_t + room_dt if play_on and kids and n_adults == 0 else 0.0
        r = self.rule("R3_unattended")
        if r and self.unattended_t >= r["threshold_s"]:
            events.append(self._fire("R3_unattended", now, self.camera_id, self.unattended_t, kid_conf))

        # R4 ratio (room level; zero adults is R3's job)
        r = self.rule("R4_ratio")
        over = r and play_on and n_adults > 0 and len(kids) / n_adults > r.get("max_children_per_adult", 8)
        self.ratio_t = self.ratio_t + room_dt if over else 0.0
        if r and self.ratio_t >= r["threshold_s"]:
            events.append(self._fire("R4_ratio", now, self.camera_id, self.ratio_t, kid_conf))

        for s in present:
            if s.role == "adult":
                r = self.rule("R1_phone_use")
                if r and s.phone_t >= r["threshold_s"]:
                    events.append(self._fire("R1_phone_use", now, s.track_id, s.phone_t, s.phone_ema, s))
                r = self.rule("R2_left_zone")
                if r and kids and s.out_t >= r["threshold_s"]:
                    events.append(self._fire("R2_left_zone", now, s.track_id, s.out_t, 1 - s.p_child, s))
                r = self.rule("R6_idle")
                if r and kids and s.idle_t >= r["threshold_s"]:
                    still = s.motion_ema or 0.0
                    conf = max(0.0, 1 - still / r.get("max_motion", 0.03))
                    events.append(self._fire("R6_idle", now, s.track_id, s.idle_t, conf, s))
            else:
                r = self.rule("R5_fall")
                if r and s.lying_t >= r["threshold_s"]:
                    conf = max(0.8 if s.lying else 0.0, s.actions.get("fall", 0.0))
                    events.append(self._fire("R5_fall", now, s.track_id, s.lying_t, conf, s))
        return [e for e in events if e]

    def status(self) -> dict:
        rules = self.cfg["rules"]
        return {
            "active": self.active, "counts": self.counts,
            "timers": {
                "R3_unattended": {"value": round(self.unattended_t, 1), "threshold": rules["R3_unattended"]["threshold_s"]},
                "R4_ratio": {"value": round(self.ratio_t, 1), "threshold": rules["R4_ratio"]["threshold_s"]},
            },
            "tracks": [s.to_dict() for s in sorted(self.tracks.values(), key=lambda s: s.track_id)],
        }

