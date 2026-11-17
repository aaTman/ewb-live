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
    "cold_snap": "Cold Snap",
    "atmospheric_river": "Atmospheric River",
    "tropical_cyclone": "Tropical Cyclone",   # tc lane, 2026-11-16
    "flood": "Flood",                     # flood lane, 2026-11-17
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
    "cold_snap": (-90.0, 45.0),      # degC (same rule minus the <=0C cap)
    "atmospheric_river": (0.0, 10000.0),  # IVT kg m-1 s-1
    "tropical_cyclone": (34.0, 200.0),    # kt, 1-min sustained (34 = the
                                          # qualification floor, 200 kt above
                                          # the highest best-track wind on record)
    # flood: the peak TRAILING 72 h accumulation in mm (IMERG). The physical
    # world record 72 h total is ~1.8 m, and a 24 h total ~1.2 m, so the range
    # is a Kelvin-misparse / unit-flip tripwire, not a climatological claim.
    "flood": (0.5, 2000.0),
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
        # titles name the LAST day of the event; end is exclusive. A sub-daily
        # end (a tropical cyclone ending 2026-10-03T06:00) is still active on
        # Oct 3 — for the midnight-aligned daily lanes this is exactly
        # end.date() - 1 day, so nothing changes for heat/freeze/AR.
        last_day = (e.end - dt.timedelta(seconds=1)).date()
        if d1 != last_day:
            problems.append(
                f"title end {d1} != last event day {last_day}"
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
    # (cold_snap behaves exactly like freeze here: "peak" is the coldest value)
    if e.event_type in ("heat_wave", "freeze", "cold_snap") and pv is not None:
        mn, mean = det.get("min_temp_c"), det.get("mean_temp_c")
        if mn is not None and mean is not None:
            if not mn <= mean:
                problems.append(f"min_temp_c {mn} > mean_temp_c {mean}")
            if e.event_type == "heat_wave":
                if pv < mean:
                    problems.append(f"heat peak_value {pv} < mean_temp_c {mean}")
                if mn < -90:
                    problems.append(f"min_temp_c {mn} physically implausible")
            else:  # freeze / cold_snap: peak is the coldest value
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

    # --- tropical cyclone: best-track cross-checks (tc lane, 2026-11-16)
    if e.event_type == "tropical_cyclone":
        from ewb_live.events.tc import MIN_TS_FIXES

        pw = det.get("peak_wind_kt")
        if pw is not None and pv is not None and abs(pw - pv) > 0.11:
            problems.append(
                f"peak_wind_kt {pw} != peak_value {pv} (peak_value is max wind in kt)")
        mp = det.get("min_pressure_hpa")
        if mp is not None and not 850.0 <= mp <= 1015.0:
            problems.append(f"min_pressure_hpa {mp} outside [850, 1015] hPa")
        if int(det.get("ts_fixes") or 0) < MIN_TS_FIXES:
            problems.append(
                f"ts_fixes {det.get('ts_fixes')} below the "
                f"{MIN_TS_FIXES}-fix qualification floor")
        cat = det.get("peak_category")
        if pv is not None and cat in ("CAT 1", "CAT 2", "CAT 3", "CAT 4", "CAT 5") \
                and pv < 64.0:
            problems.append(f"peak_category {cat} with peak wind {pv} kt (< 64 kt)")
        trk = det.get("track")
        if not trk:
            problems.append("track empty: the dashboard has nothing to draw")
        else:
            bad = [r for r in trk if not (isinstance(r, list) and len(r) == 5
                                          and r[1] is not None and r[2] is not None)]
            if bad:
                problems.append(f"{len(bad)} track row(s) not [time, lat, lon, "
                                "intensity, pressure] with finite lat/lon")
    # --- flood: accumulation, area and provenance cross-checks
    # (flood lane, 2026-11-17). The peak is a TRAILING 72 h accumulation, so it
    # cannot be smaller than the peak daily total, and an event that exceeded
    # nothing must not exist.
    if e.event_type == "flood":
        p3, p1 = det.get("peak_3day_mm"), det.get("peak_daily_mm")
        if p3 is not None and p1 is not None and p3 + 0.01 < p1:
            problems.append(
                f"flood peak_3day_mm {p3} < peak_daily_mm {p1} (a 72 h "
                "accumulation cannot be below its own peak day)")
        if p3 is not None and pv is not None and abs(pv - p3) > 0.05:
            problems.append(f"flood peak_value {pv} != peak_3day_mm {p3}")
        # The rule is "percentile AND absolute floor" PER LANE, so the event must
        # have reached a floor in at least one lane. A LOW daily peak is not a
        # contradiction: the 72 h lane fires on the day an extreme accumulation
        # ends, so a slow 15 mm/day storm exceeds it without ever reaching the
        # 20 mm/day daily floor (rain_peak_day says where the rain fell).
        from .flood import FLOOR_MM as _FLOOR

        if not ((p3 is not None and p3 >= _FLOOR[3] - 0.01)
                or (p1 is not None and p1 >= _FLOOR[1] - 0.01)):
            problems.append(
                f"flood reached no lane floor: peak_3day_mm {p3} < "
                f"{_FLOOR[3]} and peak_daily_mm {p1} < {_FLOOR[1]}")
        if int(det.get("peak_area_km2") or 0) <= 0:
            problems.append(f"flood peak_area_km2 {det.get('peak_area_km2')} <= 0")
        ratio = det.get("peak_excess_ratio")
        if ratio is not None and ratio < 1.0:
            problems.append(f"flood peak_excess_ratio {ratio} < 1: nothing exceeded")
        if not (det.get("data_source") or {}).get("days_present"):
            problems.append("flood data_source has no observed days")
        if "provisional" not in det:
            problems.append("flood event does not say whether it is provisional")

    return problems


def validate_events(events: list[Event]) -> dict[str, list[str]]:
    """Map event.id -> problems (only for failing events)."""
    out: dict[str, list[str]] = {}
    for e in events:
        p = validate_event(e)
        if p:
            out[e.id] = p
    return out
