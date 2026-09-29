"""Zone polygons (normalised 0..1 coordinates) and feet-anchor membership (blueprint 5.3)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ZONE_TYPES = ("play", "nap", "exit", "staff_only")


def point_in_polygon(x: float, y: float, poly: np.ndarray) -> bool:
    """Even-odd ray casting."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class ZoneSet:
    def __init__(self, zones: list[dict] | None = None):
        self.zones: list[dict] = []
        for z in zones or []:
            if z.get("type") not in ZONE_TYPES:
                raise ValueError(f"zone type must be one of {ZONE_TYPES}, got {z.get('type')!r}")
            poly = [[float(x), float(y)] for x, y in z["polygon"]]
            if len(poly) < 3:
                raise ValueError(f"zone {z.get('name')!r} needs at least 3 points")
            self.zones.append({"name": z.get("name") or z["type"], "type": z["type"], "polygon": poly})
        self._arrays = [np.array(z["polygon"]) for z in self.zones]

    @classmethod
    def load(cls, path: str | Path) -> "ZoneSet":
        p = Path(path)
        if not p.exists():
            return cls([])
        return cls(json.loads(p.read_text(encoding="utf-8")).get("zones", []))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    def to_dict(self) -> dict:
        return {"coords": "normalized", "zones": self.zones}

    def has(self, ztype: str) -> bool:
        return any(z["type"] == ztype for z in self.zones)

    def types_at(self, x: float, y: float) -> set[str]:
        """Zone types containing a normalised point. With no play zone drawn, the whole frame is play."""
        out = {z["type"] for z, a in zip(self.zones, self._arrays) if point_in_polygon(x, y, a)}
        if not self.has("play") and not (out & {"nap", "exit", "staff_only"}):
            out.add("play")
        return out

    def types_for_box(self, xyxy, frame_w: int, frame_h: int) -> set[str]:
        """Membership by the box bottom-centre (feet), not the box centre."""
        x = (xyxy[0] + xyxy[2]) / 2 / frame_w
        y = xyxy[3] / frame_h
        return self.types_at(x, min(y, 0.999))

    def polygons_px(self, w: int, h: int):
        for z, a in zip(self.zones, self._arrays):
            yield z, (a * [w, h]).astype(np.int32)
