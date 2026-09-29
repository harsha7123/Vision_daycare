import numpy as np
import pytest

from daycare.config import load_yaml
from daycare.perception.role import RoleResolver
from daycare.perception.zones import ZoneSet, point_in_polygon
from daycare.sim import SimScene
from daycare.types import Person

SQUARE = np.array([[0.2, 0.2], [0.6, 0.2], [0.6, 0.6], [0.2, 0.6]])


def test_point_in_polygon():
    assert point_in_polygon(0.4, 0.4, SQUARE)
    assert not point_in_polygon(0.7, 0.4, SQUARE)


def test_membership_uses_feet_not_box_centre():
    zs = ZoneSet([{"name": "play", "type": "play", "polygon": SQUARE.tolist()},
                  {"name": "k", "type": "staff_only", "polygon": [[0.2, 0.6], [0.6, 0.6], [0.6, 0.9], [0.2, 0.9]]}])
    # box centre inside play, feet inside the staff-only area below it
    box = np.array([300, 300, 400, 700.0])            # 1000 x 1000 frame
    assert zs.types_for_box(box, 1000, 1000) == {"staff_only"}


def test_no_play_zone_means_whole_frame_is_play():
    zs = ZoneSet([{"name": "door", "type": "exit", "polygon": SQUARE.tolist()}])
    assert zs.types_at(0.9, 0.9) == {"play"}
    assert zs.types_at(0.4, 0.4) == {"exit"}


def test_invalid_zone_rejected():
    with pytest.raises(ValueError):
        ZoneSet([{"name": "x", "type": "garden", "polygon": SQUARE.tolist()}])
    with pytest.raises(ValueError):
        ZoneSet([{"name": "x", "type": "play", "polygon": [[0, 0], [1, 1]]}])


@pytest.fixture
def resolver():
    return RoleResolver(load_yaml("configs/cameras.yaml")["cameras"][0]["role"])


def _person(role, pose="stand", y=0.7, tid=1):
    sim = SimScene()
    hgt = sim.adult_height_px(y) * (0.55 if role == "child" else 1.0)
    k = sim.skeleton(role, pose, 500, y * 720, hgt, 0.0)
    return Person(tid, sim._box(k, hgt, role), 0.9, k)


@pytest.mark.parametrize("role,pose", [("adult", "stand"), ("child", "stand"), ("child", "lie"), ("child", "play")])
def test_role_from_height_and_proportions(resolver, role, pose):
    frame = np.zeros((720, 1280, 3), np.uint8)
    p = _person(role, pose)
    for i in range(20):
        got, _ = resolver.resolve(p, frame, float(i))
    assert got == role


def test_manual_override_wins(resolver):
    frame = np.zeros((720, 1280, 3), np.uint8)
    p = _person("adult")
    resolver.set_override(1, "child")
    assert resolver.resolve(p, frame, 0.0)[0] == "child"
    resolver.set_override(1, "auto")
    assert resolver.resolve(p, frame, 1.0)[0] == "adult"


def test_role_vote_resists_single_frame_noise(resolver):
    frame = np.zeros((720, 1280, 3), np.uint8)
    adult_p, child_p = _person("adult"), _person("child")
    for i in range(20):
        resolver.resolve(adult_p, frame, float(i))
    child_p.track_id = 1                                  # one bad frame on the same track
    assert resolver.resolve(child_p, frame, 21.0)[0] == "adult"
