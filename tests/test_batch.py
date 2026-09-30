"""Web/cloud mode: batch analysis of cached detections, calibration, and the cloud API."""
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from daycare.batch import Detections, analyze, build_rules, estimate_calibration, render_snapshots
from daycare.sim import LOOP_S, SimScene, H, W


def sim_detections(fps: float = 6.0, seconds: float = LOOP_S) -> Detections:
    sim = SimScene()
    det = Detections(W, H, fps, seconds, fps)
    for i in range(int(seconds * fps)):
        t = i / fps
        _, persons, phones = sim.render(t)
        det.frames.append((t, i, persons, phones))
    return det


@pytest.fixture(scope="module")
def det():
    return sim_detections()


def test_presets_and_validation():
    q = build_rules("quick")
    assert q["rules"]["R3_unattended"]["threshold_s"] == 4 and q["schedule"]["active_hours"] is None
    assert build_rules("production")["rules"]["R3_unattended"]["threshold_s"] == 60
    assert build_rules("quick", {"R3_unattended": {"threshold_s": 9}})["rules"]["R3_unattended"]["threshold_s"] == 9
    for bad in ({"R9": {}}, {"R3_unattended": {"priority": "low"}}, {"R3_unattended": {"threshold_s": -1}}):
        with pytest.raises(ValueError):
            build_rules("quick", bad)
    with pytest.raises(ValueError):
        build_rules("nope")


def test_calibration_recovers_the_scene_scale(det):
    cal = estimate_calibration(det)
    assert cal is not None
    sim = SimScene()
    for y, h in (cal["far"], cal["near"]):
        expected = sim.adult_height_px(y) / H
        assert abs(h - expected) / expected < 0.2       # within 20 % of the true adult height


def test_analyze_flags_every_rule_with_video_timestamps(det):
    zones = json.loads(Path("configs/zones/sim.json").read_text())["zones"]
    res = analyze(det, build_rules("demo"), zones, calibration=estimate_calibration(det))
    rules = sorted(e["rule"] for e in res["events"])
    assert rules == sorted(["R1_phone_use", "R2_left_zone", "R3_unattended", "R4_ratio", "R5_fall", "R6_idle"])
    r3 = next(e for e in res["events"] if e["rule"] == "R3_unattended")
    assert 45 <= r3["t"] <= 55 and r3["clip"][0] == pytest.approx(r3["t"] - 10, abs=0.05)
    assert len(res["frames"]) == len(det.frames)
    assert res["summary"]["adults"] == 2 and res["summary"]["children"] == 6
    assert res["summary"]["worst"] == "critical"
    json.dumps(res)                                      # must be JSON serialisable


def test_role_override_changes_outcome(det):
    zones = json.loads(Path("configs/zones/sim.json").read_text())["zones"]
    base = analyze(det, build_rules("demo"), zones, calibration=estimate_calibration(det))
    # treat the fallen child (track 5) as an adult: no R5 any more
    res = analyze(det, build_rules("demo"), zones, roles={"5": "adult"}, calibration=estimate_calibration(det))
    assert "R5_fall" in {e["rule"] for e in base["events"]}
    assert "R5_fall" not in {e["rule"] for e in res["events"]}
    assert next(t for t in res["tracks"] if t["track_id"] == 5)["override"] == "adult"


def test_snapshots_written(tmp_path, det):
    video = tmp_path / "clip.avi"
    vw = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 6, (W, H))
    sim = SimScene()
    for t, *_ in det.frames[:120]:
        vw.write(sim.render(t)[0])
    vw.release()
    short = Detections(W, H, 6, 20, 6, det.frames[:120])
    res = analyze(short, build_rules("quick"))
    assert res["events"], "quick preset should flag something in the first 20 s"
    render_snapshots(video, short, res, tmp_path / "snaps")
    assert (tmp_path / "snaps" / "0.jpg").exists()


# ---- cloud API --------------------------------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    import daycare.cloud_api as ca
    monkeypatch.setattr(ca, "DATA", tmp_path / "jobs")
    monkeypatch.setattr(ca, "LIVE_DATA", tmp_path / "live")
    return TestClient(ca.create_app())


def test_config_and_upload_validation(client):
    cfg = client.get("/api/config").json()
    assert set(cfg["presets"]) == {"quick", "demo", "production"} and cfg["notify"]["twilio"] is False
    r = client.post("/api/jobs", files={"video": ("x.txt", io.BytesIO(b"hello"), "text/plain")})
    assert r.status_code == 422
    r = client.post("/api/jobs", files={"video": ("x.mp4", io.BytesIO(b"not a video"), "video/mp4")})
    assert r.status_code == 422
    r = client.post("/api/jobs", data={"options": '{"preset": "nope"}'},
                    files={"video": ("x.mp4", io.BytesIO(b"x"), "video/mp4")})
    assert r.status_code == 422
    assert client.get("/api/jobs/doesnotexist").status_code == 404


def test_rerun_on_cached_detections(client, tmp_path, det):
    import daycare.cloud_api as ca
    mgr = client.app.state.manager
    (tmp_path / "jobs" / "j").mkdir(parents=True)
    job = ca.Job(tmp_path / "jobs" / "j" / "video.avi", {"preset": "demo"})
    job.det, job.status = det, "done"
    job.calibration = ca.estimate_calibration(det)
    mgr.jobs[job.id] = job
    zones = json.loads(Path("configs/zones/sim.json").read_text())["zones"]
    r = client.post(f"/api/jobs/{job.id}/rerun", json={"zones": zones, "preset": "demo"})
    assert r.status_code == 200 and len(r.json()["events"]) == 6
    r = client.post(f"/api/jobs/{job.id}/rerun", json={"rules": {"R3_unattended": {"enabled": False}}})
    assert "R3_unattended" not in {e["rule"] for e in r.json()["events"]}
    assert client.post(f"/api/jobs/{job.id}/rerun", json={"preset": "bogus"}).status_code == 422
    assert client.delete(f"/api/jobs/{job.id}").status_code == 200
    assert client.get(f"/api/jobs/{job.id}").status_code == 404


def test_notify_requires_twilio_and_allowlist(client, monkeypatch):
    body = {"to": "+919876543210", "message": "test"}
    assert client.post("/api/notify/call", json=body).status_code == 503
    for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("ALLOWED_CALL_NUMBERS", "+911111111111")
    assert client.post("/api/notify/call", json=body).status_code == 403          # not allowlisted
    assert client.post("/api/notify/call", json={**body, "to": "98765"}).status_code == 422


def test_warns_when_no_people_are_detected():
    empty = Detections(W, H, 6, 10, 6, [(i / 6, i, [], []) for i in range(60)])
    res = analyze(empty, build_rules("quick"))
    assert res["events"] == [] and res["summary"]["warning"] == "no_people"
    ok = analyze(sim_detections(seconds=10), build_rules("quick"))
    assert "warning" not in ok["summary"]


# ---- live WebSocket ----------------------------------------------------------------------------------
class FakePerception:
    """Stands in for YOLO: returns the simulator's people for successive scene times."""
    device = "cpu"

    def __init__(self, start=8.0, fps=10):
        self.sim, self.t, self.dt = SimScene(), start, 1 / fps

    def reset(self):
        pass

    def __call__(self, frame):
        _, persons, phones = self.sim.render(self.t)
        self.t += self.dt
        return persons, phones


def test_live_session_flags_phone_use(client, monkeypatch):
    import daycare.cloud_api as ca
    monkeypatch.setattr(ca, "make_perception", lambda: FakePerception())
    jpg = cv2.imencode(".jpg", np.zeros((360, 640, 3), np.uint8))[1].tobytes()
    events = []
    with client.websocket_connect("/ws/live?preset=quick") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["rules"]["R1_phone_use"]["threshold_s"] == 6
        ws.send_text(json.dumps({"type": "role", "track_id": 3, "role": "child"}))
        assert ws.receive_json() == {"type": "ack", "for": "role"}
        t0 = __import__("time").time()
        for _ in range(400):
            ws.send_bytes(jpg)
            out = ws.receive_json()
            assert out["type"] == "frame" and len(out["p"]) >= 7
            events += out["events"]
            if any(e["rule"] == "R1_phone_use" for e in events):
                break
            __import__("time").sleep(0.02)
        ws.send_text("{bad json")
        assert ws.receive_json()["type"] == "error"
    r1 = [e for e in events if e["rule"] == "R1_phone_use"]
    assert r1 and r1[0]["track_id"] == 1, "phone use should be flagged for the caretaker on the phone"
    assert client.get(r1[0]["snapshot"]).status_code == 200
    assert client.get("/api/health").json()["live_sessions"] == 0      # model returned to the pool


def test_live_rejects_foreign_origin(tmp_path, monkeypatch):
    import daycare.cloud_api as ca
    monkeypatch.setattr(ca, "DATA", tmp_path / "jobs")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://good.example")
    c = TestClient(ca.create_app())
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws/live", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_json()
