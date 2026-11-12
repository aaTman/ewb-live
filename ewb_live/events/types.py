"""Event type shared across ewb_live."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from typing import Any

EVENT_TYPES = ("heat_wave", "freeze", "atmospheric_river")


def _encode(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


@dataclasses.dataclass
class Event:
    """A detected extreme weather event (SPEC section 2).

    bbox is (lat_min, lat_max, lon_min, lon_max). For events crossing the
    antimeridian the longitude span is stored as lon_min > lon_max (eastward
    arc), mirroring EWB's wrapped-region convention.
    """

    id: str
    event_type: str  # one of EVENT_TYPES
    title: str
    start: dt.datetime
    end: dt.datetime
    bbox: tuple[float, float, float, float]
    peak_value: float | None
    details: dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Event":
        return cls(
            id=d["id"],
            event_type=d["event_type"],
            title=d["title"],
            start=dt.datetime.fromisoformat(d["start"]),
            end=dt.datetime.fromisoformat(d["end"]),
            bbox=tuple(d["bbox"]),  # type: ignore[arg-type]
            peak_value=d.get("peak_value"),
            details=d.get("details", {}),
        )

    def to_ewb_case(self, case_id_number: int = 1) -> dict[str, Any]:
        """Render as an EWB IndividualCase dict (case yaml schema)."""
        lat_min, lat_max, lon_min, lon_max = self.bbox
        return {
            "case_id_number": case_id_number,
            "title": self.title,
            "start_date": self.start,
            "end_date": self.end,
            "location": {
                "type": "bounded_region",
                "parameters": {
                    "latitude_min": lat_min,
                    "latitude_max": lat_max,
                    "longitude_min": lon_min,
                    "longitude_max": lon_max,
                },
            },
            "event_type": self.event_type,
        }

    def padded(self, pad_deg: float = 5.0) -> "Event":
        """Return a copy with the bbox grown by pad_deg (lat clamped to +-90)."""
        lat_min, lat_max, lon_min, lon_max = self.bbox
        lat_min = max(-90.0, lat_min - pad_deg)
        lat_max = min(90.0, lat_max + pad_deg)
        lon_min = (lon_min - pad_deg + 180.0) % 360.0 - 180.0
        lon_max = (lon_max + pad_deg + 180.0) % 360.0 - 180.0
        return dataclasses.replace(self, bbox=(lat_min, lat_max, lon_min, lon_max))


def normalize_lon_180(lon: float) -> float:
    """Map a longitude to [-180, 180)."""
    return (lon + 180.0) % 360.0 - 180.0


def make_event_id(
    event_type: str, start: dt.datetime, bbox: tuple[float, float, float, float]
) -> str:
    """Stable id: hash of (type, start date, rounded bbox)."""
    payload = json.dumps(
        [event_type, start.strftime("%Y-%m-%d"), [round(b, 2) for b in bbox]],
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode()).hexdigest()[:12]
