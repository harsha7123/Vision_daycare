"""Event store + API tests (camera pipelines are not started)."""
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from daycare.config import load_rules
from daycare.events.api import Runtime, create_app
from daycare.events.dispatch import Dispatcher
from daycare.events.store import EventStore


class FakeCam:
    def __init__(self):
        self.error, self.fps, self.source = None, 0.0, None
        self.role_calls = []

    def status(self):
        return {"id": "cam01", "room": "A", "counts": {}, "timers": {}, "tracks": [], "frame": [0, 0],
                "models": {"yolo": False, "device": None}, "fps": 0}

    def set_role(self, tid, role):
        if role not in ("adult", "child", "auto"):
            raise ValueError("bad role")
        self.role_calls.append((tid, role))

    def set_source(self, s):
        self.source = s

    def zones(self):
        return {"coords": "normalized", "zones": []}

    def set_zones(self, body):
        from daycare.perception.zones import ZoneSet
        return ZoneSet(body.get("zones", [])).to_dict()


def make_event(eid="evt_1_cam01_R3", priority="critical", ts=None):
    return {"event_id": eid, "rule": "R3_unattended", "title": "Children unattended", "priority": priority,
            "camera": "cam01", "room": "A", "track_id": None, "role": None, "ts": ts or time.time(),
            "started_at": "2026-09-29T10:00:00+05:30", "created_at": "2026-09-29T10:01:00+05:30",
            "duration_s": 61, "children_in_zone": 3, "adults_in_zone": 0, "confidence": 0.9,
            "snapshot_url": None, "clip_url": None, "ack": None}


@pytest.fixture
def client(tmp_path):
    store = EventStore(tmp_path)
    rules = load_rules("demo")
    disp = Dispatcher({"channels": {"console": {"enabled": True}}}, rules, store)
    cam = FakeCam()
    rt = Runtime({"cam01": cam}, store, disp, rules, {})
    c = TestClient(create_app(rt))
    c.store, c.cam, c.rules = store, cam, rules
    return c


def test_event_list_and_ack_with_feedback(client):
    client.store.add(make_event())
    evs = client.get("/api/events").json()
    assert [e["event_id"] for e in evs] == ["evt_1_cam01_R3"] and evs[0]["ack"] is None
    r = client.post("/api/events/evt_1_cam01_R3/ack", json={"by": "priya", "feedback": "false"})
    assert r.status_code == 200 and r.json()["ack"]["by"] == "priya" and r.json()["ack"]["feedback"] == "false"
    assert client.get("/api/events?unacked=true").json() == []
    assert client.post("/api/events/nope/ack", json={}).status_code == 404
    assert client.post("/api/events/evt_1_cam01_R3/ack", json={"feedback": "maybe"}).status_code == 422


def test_public_payload_hides_internal_fields(client):
    client.store.add({**make_event(), "box": [1, 2, 3, 4]})
    pub = client.get("/api/events/evt_1_cam01_R3?public=true").json()
    assert "box" not in pub and "ts" not in pub and pub["rule"] == "R3_unattended"


def test_escalation_candidates(client):
    old = time.time() - 600
    client.store.add(make_event("evt_high", "high", old))
    client.store.add(make_event("evt_crit", "critical", old))
    client.store.add(make_event("evt_new", "high"))
    due = [e["event_id"] for e in client.store.due_for_escalation(120)]
    assert due == ["evt_high"]          # critical already went to everyone; new one is not due


def test_snapshot_and_clip_written(client):
    import cv2
    ev = make_event()
    img = np.full((120, 160, 3), 127, np.uint8)
    jpg = cv2.imencode(".jpg", img)[1].tobytes()
    client.store.save_snapshot(ev, jpg)
    client.store.add(ev)
    assert client.get(ev["snapshot"]).status_code == 200
    client.store.write_clip(ev, [(i / 10, jpg) for i in range(20)])
    stored = client.store.get(ev["event_id"])
    assert stored.get("clip") and client.get(stored["clip"]).status_code == 200


def test_rule_tuning_validates(client):
    r = client.put("/api/rules", json={"R3_unattended": {"threshold_s": 5}})
    assert r.status_code == 200 and client.rules["rules"]["R3_unattended"]["threshold_s"] == 5
    assert client.put("/api/rules", json={"R3_unattended": {"priority": "low"}}).status_code == 422
    assert client.put("/api/rules", json={"R9": {"threshold_s": 1}}).status_code == 422
    assert client.put("/api/rules", json={"R3_unattended": {"threshold_s": -1}}).status_code == 422


def test_role_and_source_and_zones(client):
    assert client.post("/api/cameras/cam01/tracks/7/role", json={"role": "child"}).status_code == 200
    assert client.cam.role_calls == [(7, "child")]
    assert client.post("/api/cameras/cam01/tracks/7/role", json={"role": "teacher"}).status_code == 422
    assert client.post("/api/cameras/cam01/source", json={"source": "sim"}).status_code == 200
    assert client.post("/api/cameras/cam01/source", json={"source": "/no/such/file.mp4"}).status_code == 422
    bad = {"zones": [{"name": "x", "type": "garden", "polygon": [[0, 0], [1, 0], [1, 1]]}]}
    assert client.put("/api/cameras/cam01/zones", json=bad).status_code == 422
    assert client.get("/api/cameras/nope/zones").status_code == 404


def test_retention_purge(client):
    client.store.add(make_event("evt_old", ts=time.time() - 40 * 86400))
    client.store.add(make_event("evt_new"))
    assert client.store.purge(30) == 1
    assert [e["event_id"] for e in client.store.list()] == ["evt_new"]
