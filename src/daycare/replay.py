"""Offline replay of the synthetic scenario (faster than real time) - used by tests and CI.

    python -m daycare.replay            # prints the events one 150 s loop produces
"""
from __future__ import annotations

from .analyzer import Analyzer
from .config import load_rules, load_yaml, resolve
from .perception.zones import ZoneSet
from .sim import LOOP_S, SimScene

START_TS = 1_790_000_000.0   # fixed epoch so event ids are deterministic


def run_sim_replay(duration_s: float = LOOP_S, fps: int = 12, profile: str = "demo") -> list[dict]:
    rules = load_rules(profile)
    rules["schedule"]["active_hours"] = None        # replay must not depend on the time of day
    role_cfg = load_yaml("configs/cameras.yaml")["cameras"][0]["role"]
    an = Analyzer("cam01", "Toddlers A", role_cfg, rules, ZoneSet.load(resolve("configs/zones/sim.json")))
    sim = SimScene(role_cfg.get("calibration"))
    events = []
    for i in range(int(duration_s * fps)):
        t = i / fps
        frame, persons, phones = sim.render(t % LOOP_S, int(t // LOOP_S))
        for e in an.process(frame, persons, phones, START_TS + t):
            e["sim_t"] = round(t, 1)
            events.append(e)
    return events


if __name__ == "__main__":
    for e in run_sim_replay():
        print(f"{e['sim_t']:6.1f}s  {e['rule']:15s} {e['priority']:8s} track={e['track_id']} "
              f"dur={e['duration_s']}s conf={e['confidence']}")
