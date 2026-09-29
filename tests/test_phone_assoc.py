import numpy as np

from daycare.perception.phone_assoc import phone_score
from daycare.sim import SimScene
from daycare.types import Phone

SIM = SimScene()
H = 220.0      # adult height in px
FX, FY = 400.0, 500.0


def adult(pose):
    k = SIM.skeleton("adult", pose, FX, FY, H, 0.0)
    return k, SIM._box(k, H, "adult")


def phone_at(xy, conf=0.7):
    x, y = xy
    return Phone(np.array([x - 4, y - 6, x + 4, y + 6]), conf)


def test_phone_in_hand_while_looking_down_scores_high():
    k, box = adult("phone")
    wrists = (k[9, :2] + k[10, :2]) / 2
    assert phone_score(k, box, [phone_at(wrists)]) == 1.0


def test_phone_on_table_next_to_person_does_not_count():
    k, box = adult("stand")
    far = (k[9, 0] + 0.6 * H, k[9, 1])            # beside the person, not in the hand
    assert phone_score(k, box, [phone_at(far)]) == 0.0


def test_phone_in_hand_but_head_up_gets_half_score():
    k, box = adult("stand")                       # arms down, head up
    at_wrist = k[9, :2]
    assert phone_score(k, box, [phone_at(at_wrist)]) == 0.5


def test_low_confidence_phone_scales_down():
    k, box = adult("phone")
    wrists = (k[9, :2] + k[10, :2]) / 2
    assert phone_score(k, box, [phone_at(wrists, conf=0.25)]) == 0.5


def test_posture_only_signal_without_phone_box():
    k, box = adult("phone")
    assert phone_score(k, box, []) == 0.4


def test_no_keypoints_no_score():
    _, box = adult("stand")
    assert phone_score(None, box, [phone_at((FX, FY - 100))]) == 0.0
