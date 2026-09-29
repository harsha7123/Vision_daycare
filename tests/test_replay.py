"""Replay test (blueprint section 10): the scripted scene must produce exactly the expected events."""
from daycare.replay import run_sim_replay

# rule -> (expected sim time window in seconds, track id or None)
EXPECTED = {
    "R1_phone_use": ((18, 24), 1),
    "R4_ratio": ((35, 41), None),
    "R2_left_zone": ((42, 48), 2),
    "R3_unattended": ((47, 53), None),
    "R5_fall": ((102, 108), 5),
    "R6_idle": ((114, 122), 2),
}


def test_one_loop_fires_every_rule_once_at_the_right_time():
    events = run_sim_replay()
    assert sorted(e["rule"] for e in events) == sorted(EXPECTED)
    for e in events:
        (lo, hi), tid = EXPECTED[e["rule"]]
        assert lo <= e["sim_t"] <= hi, (e["rule"], e["sim_t"])
        assert e["track_id"] == tid


def test_hard_negatives_never_alert():
    events = run_sim_replay()
    # napping child (track 8) and the phone lying on the table must never produce an event
    assert all(e["track_id"] != 8 for e in events)
    assert sum(e["rule"] == "R1_phone_use" for e in events) == 1
