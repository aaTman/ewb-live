"""Publish validator for curated events (Weather Explorer pattern).

Borrowed from Neil Gordon's Weather Explorer (notes/design.md): their
publish gate refuses a feed unless every caption claim matches the real
frames. Our analogue: before an event is published (events_selected /
scoring / weekly output), programmatically check the claims the event
itself makes — title dates, per-type value ranges, cross-field
consistency (peak vs mean/min, AR landfall/centroid vs bbox) — and
REFUSE events that fail, logging the reason.

Deliberately pure + dependency-light (stdlib + Event): must be runnable
offline in unit tests and cheap to call on every publish. If an LLM
narrative is added later, keep its sentences constrained to the fields
checked here (design channel, idea 2).
"""

from __future__ import annotations

import datetime as dt
import math
import re

from ewb_live.events.types import Event

# "<Label> YYYY-MM-DD to YYYY-MM-DD" — the publish title shape
_TITLE_RE = re.compile(
    r"^(?P<label>[\w ]+?) (?P<d0>\d{4}-\d{2}-\d{2}) to (?P<d1>\d{4}-\d{2}-\d{2})$"
)
_TITLE_LABELS = {
    "heat_wave": "Heat Wave",
    "freeze": "Freeze",
    "atmospheric_river": "Atmospheric River",
}
# physical sanity ranges for peak_value, per type. NOTE freeze is NOT
# "sub-zero": per SPEC it mirrors EWB's climatology-percentile definition
# (t2m below local p15), so in desert regions the event's coldest daily
# mean is legitimately 14-21 degC (5 such events in the 2026-09/10 live
# selection). Bounds here are PHYSICAL tripwires (e.g. Kelvin misparse),
# not climatological claims; the semantic checks live below.
_PEAK_RANGE = {
    "heat_wave": (-20.0, 60.0),      # degC
    "freeze": (-90.0, 45.0),         # degC (percentile-relative, see above)
    "atmospheric_river": (0.0, 10000.0),  # IVT kg m-1 s-1
}
# Duration ceiling for a published event. The footprint/core tracker (SPEC §2)
# closes events on their own core, so long spells are physical rather than an
# artefact of the tracker: the live 2026-08-11..11-02 window tops out at 33 days
# (central Africa). 31 days (the old upstream-tracker-era value) refused the
# single most extreme heat wave of the window, so the cap sits above the
# longest real spell while still tripping on nonsense (a whole-window "event").
MAX_DURATION_DAYS = 45


def validate_event(e: Event) -> list[str]:
    """Return a list of human-readable problems; empty list == publishable."""
    problems: list[str] = []

    # --- title claims: dates must match the fields, label must match type
    m = _TITLE_RE.match(e.title or "")
    if not m:
        problems.append(f"title {e.title!r} does not match '<Label> DATE to DATE'")
    else:
        want = _TITLE_LABELS.get(e.event_type)
        if want and m.group("label") != want:
            problems.append(
                f"title label {m.group('label')!r} != {want!r} for {e.event_type}"
            )
        d0 = dt.date.fromisoformat(m.group("d0"))
        d1 = dt.date.fromisoformat(m.group("d1"))
        if d0 != e.start.date():
            problems.append(f"title start {d0} != start {e.start.date()}")
        # titles name the LAST day of the event; end is exclusive
        if d1 != e.end.date() - dt.timedelta(days=1):
            problems.append(
                f"title end {d1} != last event day {e.end.date() - dt.timedelta(days=1)}"
            )

    # --- timing
    if not e.start < e.end:
        problems.append(f"start {e.start} not before end {e.end}")
    else:
        days = (e.end - e.start).total_seconds() / 86400.0
        if not 0.5 <= days <= MAX_DURATION_DAYS:
            problems.append(f"duration {days:.1f}d outside (0.5, {MAX_DURATION_DAYS}]")

    # --- bbox geometry
    lat0, lat1, lon0, lon1 = e.bbox
    if not -90.0 <= lat0 < lat1 <= 90.0:
        problems.append(f"bbox latitudes invalid: {lat0}..{lat1}")
    if not all(-180.0 <= x <= 180.0 for x in (lon0, lon1)):
        problems.append(f"bbox longitudes out of range: {lon0}..{lon1}")
    wrapped = lon0 > lon1  # antimeridian eastward arc convention
    wrap_raw = (e.details or {}).get("antimeridian_wrap")
    if wrap_raw is not None and bool(wrap_raw) != wrapped:
        problems.append(
            f"antimeridian_wrap={wrap_raw} disagrees with bbox lon {lon0}..{lon1}"
        )

    # --- peak value: present, finite, in range for type
    pv = e.peak_value
    if pv is None or not math.isfinite(pv):
        problems.append(f"peak_value invalid: {pv!r}")
        pv = None
    else:
        lo, hi = _PEAK_RANGE.get(e.event_type, (-1e9, 1e9))
        if not lo <= pv <= hi:
            problems.append(
                f"peak_value {pv} outside {e.event_type} range [{lo}, {hi}]"
            )

    det = e.details or {}

    # --- temperature events: cross-field ordering of the reported stats
    if e.event_type in ("heat_wave", "freeze") and pv is not None:
        mn, mean = det.get("min_temp_c"), det.get("mean_temp_c")
        if mn is not None and mean is not None:
            if not mn <= mean:
                problems.append(f"min_temp_c {mn} > mean_temp_c {mean}")
            if e.event_type == "heat_wave":
                if pv < mean:
                    problems.append(f"heat peak_value {pv} < mean_temp_c {mean}")
                if mn < -90:
                    problems.append(f"min_temp_c {mn} physically implausible")
            else:  # freeze: peak is the coldest value
                if mn is not None and abs(pv - mn) > 0.05:
                    problems.append(f"freeze peak_value {pv} != min_temp_c {mn}")
                if mean > 273.0:
                    problems.append(f"freeze mean_temp_c {mean} looks like Kelvin")
        mcd = det.get("max_consecutive_days")
        if mcd is not None and (mcd < 1 or mcd > MAX_DURATION_DAYS):
            problems.append(f"max_consecutive_days {mcd} implausible")

    # --- AR: detection-threshold and geometry cross-checks
    if e.event_type == "atmospheric_river":
        thr = det.get("ivt_threshold_kg_m_s")
        if pv is not None and thr is not None and pv < thr:
            problems.append(f"AR peak IVT {pv} below detection threshold {thr}")
        plp, mmg = det.get("peak_land_pixels"), det.get("min_mask_gridpoints")
        if plp is not None and mmg is not None and plp < mmg:
            problems.append(
                f"peak_land_pixels {plp} below min_mask_gridpoints {mmg}"
            )
        lf = det.get("landfall_days")
        if lf is not None and lf < 1:
            problems.append(f"landfall_days {lf} < 1")
        clat = det.get("centroid_lat")
        if clat is not None and not lat0 - 2.5 <= clat <= lat1 + 2.5:
            problems.append(
                f"centroid_lat {clat} outside bbox lat {lat0}..{lat1} (+2.5 deg tol)"
            )

    return problems


def validate_events(events: list[Event]) -> dict[str, list[str]]:
    """Map event.id -> problems (only for failing events)."""
    out: dict[str, list[str]] = {}
    for e in events:
        p = validate_event(e)
        if p:
            out[e.id] = p
    return out
