import numpy as np

from daycare.analyzer import Analyzer
from daycare.batch import build_rules
from daycare.perception.phone_assoc import phone_evidence, phone_score
from daycare.perception.zones import ZoneSet
from daycare.sim import SimScene
from daycare.types import Person, Phone

SIM = SimScene()
H = 220.0      # adult height in px
FX, FY = 400.0, 500.0


def adult(pose):
    k = SIM.skeleton("adult", pose, FX, FY, H, 0.0)
    return k, SIM._box(k, H, "adult")


def phone_at(xy, conf=0.7, size=(8, 12)):
    x, y = xy
    return Phone(np.array([x - size[0] / 2, y - size[1] / 2, x + size[0] / 2, y + size[1] / 2]), conf)


# ---- full-body (CCTV) geometry -------------------------------------------------------------------
def test_phone_in_hand_while_looking_down_scores_high():
    k, box = adult("phone")
    wrists = (k[9, :2] + k[10, :2]) / 2
    assert phone_score(k, box, [phone_at(wrists)]) >= 0.9


def test_phone_on_table_next_to_person_does_not_count():
    k, box = adult("stand")
    far = (k[9, 0] + 0.6 * H, k[9, 1])            # beside the person, not in the hand
    assert phone_score(k, box, [phone_at(far)]) == 0.0


def test_phone_held_at_hip_without_looking_stays_below_threshold():
    k, box = adult("stand")                       # arms down, head up
    s = phone_score(k, box, [phone_at(k[9, :2])])
    assert 0.3 < s < 0.6


def test_low_confidence_phone_still_counts_when_geometry_agrees():
    k, box = adult("phone")
    wrists = (k[9, :2] + k[10, :2]) / 2
    assert phone_score(k, box, [phone_at(wrists, conf=0.26)]) >= 0.8


def test_posture_only_signal_without_phone_box():
    k, box = adult("phone")
    assert phone_score(k, box, []) == 0.4


def test_no_keypoints_no_score():
    _, box = adult("stand")
    assert phone_score(None, box, [phone_at((FX, FY - 100))]) == 0.0


# ---- laptop-camera (close-up upper body) geometry ------------------------------------------------
def webcam_person(wrists_visible=True):
    """Upper body filling a 960x540 frame; hips and legs are below the frame (confidence 0)."""
    k = np.zeros((17, 3))
    pts = {0: (480, 170), 1: (455, 150), 2: (505, 150), 3: (420, 165), 4: (540, 165),
           5: (630, 330), 6: (330, 330), 7: (600, 470), 8: (360, 470), 9: (525, 430), 10: (435, 430)}
    for i, (x, y) in pts.items():
        k[i] = (x, y, 0.9)
    if not wrists_visible:
        k[9, 2] = k[10, 2] = 0.1
    return k, np.array([300, 60, 660, 540.0])


def test_webcam_texting_in_front_of_chest():
    k, box = webcam_person()
    ev = phone_evidence(k, box, [phone_at((480, 400), conf=0.4, size=(60, 100))])
    assert ev["score"] >= 0.8 and ev["reason"] == "in use"


def test_webcam_hand_hidden_behind_phone():
    k, box = webcam_person(wrists_visible=False)
    ev = phone_evidence(k, box, [phone_at((480, 380), conf=0.35, size=(60, 100))])
    assert ev["score"] >= 0.7


def test_webcam_phone_on_desk_outside_person():
    k, box = webcam_person()
    assert phone_score(k, box, [phone_at((820, 520), conf=0.8, size=(80, 40))]) == 0.0


def test_evidence_explains_why_not():
    k, box = webcam_person()
    assert phone_evidence(k, box, [])["reason"] == "no phone detected"


# ---- flicker: brief detection gaps keep the phone "in use" ---------------------------------------
def test_phone_hold_bridges_detection_gaps():
    an = Analyzer("t", "r", {"mode": "all_adult"}, build_rules("quick"), ZoneSet([]))
    frame = np.zeros((540, 960, 3), np.uint8)
    k, box = webcam_person()
    person = Person(1, box, 0.9, k)
    phone = [phone_at((480, 400), conf=0.4, size=(60, 100))]
    events, t = [], 0.0
    for i in range(100):                          # 10 fps, phone detected only 2 frames out of 3
        t = i / 10
        events += an.process(frame, [person], phone if i % 3 else [], 1_790_000_000 + t)
        if events:
            break
    assert events and events[0]["rule"] == "R1_phone_use"
    assert t < 8, f"R1 (6 s threshold) should fire despite flicker, fired at {t}"
