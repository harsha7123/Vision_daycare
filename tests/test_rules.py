"""Rule engine unit tests with a fake clock and hand-built observations."""
import numpy as np
import pytest

from daycare.config import load_rules
from daycare.rules.engine import RoomPresence, RuleEngine
from daycare.types import Observation

FPS = 10
T0 = 1_790_000_000.0


@pytest.fixture
def cfg():
    c = load_rules("production")
    c["schedule"]["active_hours"] = None
    return c


def child(tid, zones=("play",)):
    return Observation(tid, np.array([0, 0, 40, 80.0]), None, "child", 0.9, set(zones))


def adult(tid, zones=("play",), phone=0.0):
    return Observation(tid, np.array([100, 0, 160, 200.0]), None, "adult", 0.05, set(zones), phone)


def run(engine, seconds, frame_fn, t_start=0.0):
    """Step the engine at FPS for `seconds`; returns [(t, event)]."""
    out = []
    n0 = int(round(t_start * FPS))
    for i in range(n0, n0 + int(seconds * FPS)):
        t = i / FPS
        out += [(t, e) for e in engine.step(frame_fn(t), T0 + t)]
    return out


def rules_fired(events):
    return [e["rule"] for _, e in events]


def test_r3_fires_after_threshold_not_before(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    kids = lambda t: [child(1), child(2)]
    assert run(eng, 59, kids) == []
    ev = run(eng, 2, kids, t_start=59)
    assert rules_fired(ev) == ["R3_unattended"]
    assert ev[0][1]["priority"] == "critical"
    assert ev[0][1]["children_in_zone"] == 2 and ev[0][1]["adults_in_zone"] == 0


def test_r3_timer_resets_when_adult_returns(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    frames = lambda t: [child(1)] + ([adult(9)] if 40 <= t < 42 else [])
    assert "R3_unattended" not in rules_fired(run(eng, 90, frames))


def test_r3_cooldown_prevents_alert_storm(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ev = run(eng, 250, lambda t: [child(1)])
    assert rules_fired(ev).count("R3_unattended") == 1          # cooldown is 300 s
    ev = run(eng, 120, lambda t: [child(1)], t_start=250)
    assert rules_fired(ev).count("R3_unattended") == 1          # fires again after cooldown


def test_adult_outside_play_zone_does_not_count_as_supervising(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ev = run(eng, 200, lambda t: [child(1), adult(9, zones=("staff_only",))])
    assert set(rules_fired(ev)) == {"R3_unattended", "R2_left_zone"}


def test_r2_requires_children_present(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ev = run(eng, 200, lambda t: [adult(9, zones=("staff_only",))])
    assert ev == []


def test_r1_phone_hysteresis_and_threshold(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ev = run(eng, 130, lambda t: [child(1), adult(9, phone=1.0)])
    fired = [(t, e) for t, e in ev if e["rule"] == "R1_phone_use"]
    assert len(fired) == 1
    t, e = fired[0]
    assert 120 <= t <= 123 and e["track_id"] == 9 and e["priority"] == "high"


def test_r1_brief_phone_glances_do_not_alert(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    glance = lambda t: [child(1), adult(9, phone=1.0 if (t % 60) < 20 else 0.0)]   # 20 s of every minute
    assert "R1_phone_use" not in rules_fired(run(eng, 300, glance))


def test_r1_ignores_phone_score_below_enter_threshold(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ev = run(eng, 200, lambda t: [child(1), adult(9, phone=0.4)])     # posture-only signal
    assert "R1_phone_use" not in rules_fired(ev)


def test_r4_ratio(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    ten_kids = lambda t: [child(i) for i in range(10)] + [adult(99)]
    ev = run(eng, 125, ten_kids)
    assert rules_fired(ev) == ["R4_ratio"]
    eng2 = RuleEngine(cfg, "cam01", "A")
    ok = lambda t: [child(i) for i in range(8)] + [adult(99)]
    assert run(eng2, 125, ok) == []


def test_children_in_nap_zone_are_not_counted(cfg):
    eng = RuleEngine(cfg, "cam01", "A")
    assert run(eng, 90, lambda t: [child(1, zones=("nap",))]) == []


def test_multi_camera_room_presence(cfg):
    presence = RoomPresence()
    cam_a = RuleEngine(cfg, "camA", "Room", presence)
    cam_b = RuleEngine(cfg, "camB", "Room", presence)
    events = []
    for i in range(90 * FPS):
        now = T0 + i / FPS
        events += cam_a.step([child(1)], now)          # camA sees only a child
        events += cam_b.step([adult(9)], now)          # camB sees the caretaker in its play zone
    assert events == []


def test_inactive_hours_pause_rules(cfg):
    cfg["schedule"]["active_hours"] = "00:00-00:01"
    cfg["schedule"]["timezone"] = "UTC"
    eng = RuleEngine(cfg, "cam01", "A")
    # T0 = 2026-09-21 ~14:13 UTC, outside the window
    assert run(eng, 90, lambda t: [child(1)]) == []


def test_disabled_rule_never_fires(cfg):
    cfg["rules"]["R3_unattended"]["enabled"] = False
    eng = RuleEngine(cfg, "cam01", "A")
    assert run(eng, 90, lambda t: [child(1)]) == []


def test_event_payload_shape(cfg):
    eng = RuleEngine(cfg, "cam01", "Toddlers A")
    (_, e), = run(eng, 61, lambda t: [child(1)])
    for key in ["event_id", "rule", "priority", "camera", "room", "track_id", "role", "started_at",
                "duration_s", "children_in_zone", "adults_in_zone", "confidence", "snapshot_url", "clip_url", "ack"]:
        assert key in e
    assert e["event_id"].startswith("evt_") and e["event_id"].endswith("_cam01_R3")
