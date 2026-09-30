"""Steps 4-7 of the runtime workflow for one camera, with no I/O or threads:
classify role -> associate phone -> update state -> evaluate rules."""
from __future__ import annotations

import numpy as np

from .perception.phone_assoc import EMPTY, phone_evidence
from .perception.role import RoleResolver
from .perception.zones import ZoneSet
from .rules.engine import RoomPresence, RuleEngine
from .types import Observation, Person, Phone


class Analyzer:
    def __init__(self, camera_id: str, room: str, role_cfg: dict, rules_cfg: dict, zones: ZoneSet,
                 classifier=None, action_model=None, presence: RoomPresence | None = None):
        self.camera_id, self.room = camera_id, room
        self.rules_cfg = rules_cfg
        self.zones = zones
        self.roles = RoleResolver(role_cfg, classifier)
        self.engine = RuleEngine(rules_cfg, camera_id, room, presence)
        self.action_model = action_model
        self.persons: list[Person] = []
        self.phones: list[Phone] = []
        self.phone_debug: dict[int, dict] = {}              # track -> evidence (shown in the live UI)
        self._phone_hold: dict[int, tuple[float, float]] = {}  # track -> (score, time) of last good sighting

    HOLD_S = 0.8        # phones flicker in and out of detection; keep the last score this long

    def _phone(self, p: Person, phones: list[Phone], now: float) -> float:
        ev = phone_evidence(p.kpts, p.xyxy, phones, self.rules_cfg.get("phone"))
        score = ev["score"]
        last = self._phone_hold.get(p.track_id)
        if score >= 0.5:
            self._phone_hold[p.track_id] = (score, now)
        elif last and now - last[1] <= self.HOLD_S:
            score = max(score, last[0] * 0.9)             # brief detection gap: keep counting
            ev = {**ev, "score": round(score, 3), "reason": "in use (phone briefly hidden)"}
        self.phone_debug[p.track_id] = ev
        return score

    def process(self, frame: np.ndarray, persons: list[Person], phones: list[Phone], now: float) -> list[dict]:
        h, w = frame.shape[:2]
        obs = []
        for p in persons:
            role, pc = self.roles.resolve(p, frame, now)
            if role == "adult":
                score = self._phone(p, phones, now)
            else:
                score = 0.0
                self.phone_debug[p.track_id] = dict(EMPTY, reason="child: phone rule not applied")
            actions = None
            if self.action_model is not None:
                actions = self.action_model.update(p.track_id, p.kpts, p.xyxy, now, candidate=self._candidate(p.track_id, score))
            obs.append(Observation(p.track_id, p.xyxy, p.kpts, role, pc,
                                   self.zones.types_for_box(p.xyxy, w, h), score, actions))
        events = self.engine.step(obs, now)
        self.roles.forget(self.engine.forgotten)
        for t in self.engine.forgotten:
            self.phone_debug.pop(t, None)
            self._phone_hold.pop(t, None)
        if self.action_model is not None:
            self.action_model.forget(self.engine.forgotten)
        self.persons, self.phones = persons, phones
        return events

    def _candidate(self, tid: int, score: float) -> bool:
        """Only run the action model on tracks the rules already find suspicious (saves compute)."""
        s = self.engine.tracks.get(tid)
        return score > 0.2 or (s is not None and (s.lying or s.head_down or s.seated))
