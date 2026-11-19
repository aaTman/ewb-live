"""Tropical-cyclone WIND-FIELD asset for the merged map (tc-map, 2026-11-18).

Owner direction 2026-11-18: "plot the TCs as wind fields (max winds if
possible) where greater than tropical storm force winds (34 kts) and plot by
winds and Saffir-Simpson category for shading." So the map layer for a storm is
the field it produced, not only the line it followed: every cell that reached
tropical-storm force is shaded, classed by the Saffir-Simpson scale, and the
track + centre marker stay on top for orientation.

Source — measured, not assumed (full numbers in ``notes/tc_map.md``)
-------------------------------------------------------------------
* **Used: ERA5 10 m wind, ``10m_u_component_of_wind`` / ``10m_v_component_of_wind``
  from the ERA5 ARCO zarr** (the same store ``ewb_live/sources/mlc_era5.py``
  streams; hourly on archive, 0.25°). Sustained wind, 10-minute means — the only
  quantity the Saffir-Simpson thresholds in kt are defined against.
* Not the local truth mirror: ``truth_mirror.MIRROR_VARS`` is
  ``2m_temperature`` + the AR variables on **pressure levels** — there is no
  10 m wind in it (checked 2026-11-18).
* Not the gust fields (``instantaneous_10m_wind_gust``,
  ``10m_wind_gust_since_previous_post_processing``): a gust is ~1.4× a sustained
  wind, so shading by them would overstate every category on a scale defined by
  sustained wind. They are the right source for a *gust* layer, not this one.
* Cadence: the **four 6-hourly synoptic steps** of a UTC day (00/06/12/18Z), read
  per step and **stored per step** — the field the map paints at 06Z is the wind
  that blew at 06Z (owner direction 2026-11-19: "instantaneous 10 m wind at the
  displayed hour, not the daily max"). The merged map's own frames ARE those four
  hours (``raw_exceedance/index.json`` → 336 frames at 00/06/12/18Z), so a frame
  matches a step one-for-one: nothing is interpolated, held over from the previous
  step, or maxed over the day. Hourly ERA5 was measured and rejected: reading all
  24 hourly steps raises the peak from 0.69 to 0.72 of the best-track peak (+1–3
  kt) for 6× the reads, and the map's time axis is 6-hourly anyway, so hourly
  steps would only be re-interpolated to this cadence.
* "Windowed, not global" — measured the other way: the ARCO wind chunks are
  ``(1, 721, 1440)``, i.e. one whole global field per time step, so a
  13°×13° window read costs *more* than the global one (measured on the ewb LXC:
  4 steps global 4.9 s vs the same 4 steps windowed 25.6 s). The build therefore
  streams the global 6-hourly field once per day (~1.2 s/step for both
  components, ~7 min for a 90-day window) and crops **in memory**, which is the
  cheap way to honour the intent: nothing global is ever stored, every published
  raster is a ±5° window around one storm's live positions for one day.

DISSIPATION — the field exists only while the storm is alive
-----------------------------------------------------------
Owner addition 2026-11-18: "make sure the TCs dissipate after the storm ends".
The first build shaded a ±5° box around EACH DAY'S FIXES for the WHOLE UTC day,
over the storm's FULL best track — which is not the storm's life. Measured on
the 44 published storms (2026-11-18): 35 of them have best-track fixes BEFORE
their event start (pre-genesis disturbance) and 23 have fixes AFTER it (the
post-tropical / ET tail), and the asset was shading wind in all of it — 113
shaded cells on days before a storm's first qualifying fix and 19 on days after
its last one, plus 1.0 % of cells more than 5° from any live fix (up to 8.3°:
monsoon flow and neighbouring systems attributed to a storm that was nowhere
near them), and 436 cells on end days computed from hours the storm did not
exist to produce.

The rule this build implements, in the producer so no client can get it wrong:

* **Alive = the lane's own event window** (``ewb_live/events/tc.py``): first to
  last qualifying fix — wind >= 34 kt, ``NATURE`` not ``ET``/``MX`` — with
  ``end`` = last qualifying fix + one fix step. The window is the decision
  already published in the event, so the map, the event page and this layer can
  never disagree about when a storm existed. The **ET tail is excluded**: the
  window stops at the last tropical-storm-force fix, so a post-tropical low
  that re-deepens into a 45 kt gale shades nothing. (Recommended to the owner:
  no fade, a hard stop — an invented 6-12 h fade would be shading for a storm
  that the best track says is no longer a tropical cyclone, and the layer has
  to be able to say "gone".)
* **Per 6-hourly step, stored per step.** Only the four synoptic steps inside the
  window contribute, and each keeps its OWN grid, so a frame at 06Z shows the 06Z
  field and a frame at or after the window's end shows nothing at all. A step
  before the first qualifying fix or at/after the window's end contributes NOTHING,
  so the end day carries only the hours the storm was alive, and the day after its
  last fix carries nothing at all.
* **Around THAT step's position**, interpolated between the storm's live fixes
  (never a stale last position), and only in the **storm's own envelope**: a
  cell is kept when it is within ``KEEP_DEG`` of that step's centre, it is the
  nearest live centre among ALL storms alive at that step (attribution by
  nearest fix, so two overlapping storms each end on their own date), and it
  lies in the >=34 kt **connected component that contains the storm's core**
  (``CORE_DEG`` seed) — the cheap, physical way to drop a detached monsoon blob
  3° away that happens to be blowing 40 kt. Within ONE step a cell has exactly
  one owner (strictly nearer, so the frontier between two storms is claimed by
  neither); at a frame only that step's grid is drawn, so a cell belongs to at
  most one storm on screen at any hour, and each storm's own field still ends on
  its own date. (The AUDIT still looks at days, so it unions a day's steps — see
  ``day_union``.)
* **Ongoing storms** are the ones still open when the *data* runs out: the lane's
  ``details.ongoing`` only says the best track ends while the system was still
  tropical — which is also true of a storm that dissipated six weeks before the
  cutoff — so the asset marks ``ongoing`` only when that is combined with a death
  at or after the last 10 m wind step that has real values (``dissipation.cutoff``
  names that step; ``track_open`` keeps the lane's own flag). The map draws no
  dissipation for those; storms that ended before the cutoff dissipate cleanly.
  (ARCO's time axis is not the cutoff — it lists NaN-filled times ahead of the
  data — so the cutoff is measured from the steps actually read.)

The asset publishes the whole rule under ``"dissipation"`` and per-storm life
under ``"life"``, so the layer states it instead of inventing it.

Honesty about intensity (the bias is real and large)
----------------------------------------------------
ERA5 at 0.25° cannot resolve an eyewall and its wind is a 10-minute mean, while
IBTrACS peaks are ~1-minute sustained. For the 44 published storms in the dev
window the daily-max 10 m wind within 2° of the peak fix averaged **0.69×** the
best-track peak (0.40× for a Cat 4). So category shading derived from the field
reads *lower* than the official category, always. This lane therefore never
presents the field as the category: the asset says which field it came from, the
map labels the layer "ERA5 10 m wind" and keeps the best-track peak/category on
the storm itself, and the legend says the field under-reads. No silent
correction is applied — the measured ratio is published in the asset instead.

Output (one small asset next to the other map assets)
-----------------------------------------------------
``data/results/tc_wind.json``::

    {"schema": 3, "lane": "tropical_cyclone",
     "source": "ERA5 10 m wind (10m_u/v_component_of_wind), ERA5 ARCO 0.25°, "
               "00/06/12/18Z), the one 6-hourly step the map is showing",
     "cadence_h": 6, "step_deg": 1.0, "read_deg": 0.25, "half_deg": 5.0, "floor_kt": 34,
     "units": "kt",
     "dissipation": {"window": "the lane's event window: first to last qualifying
                            fix (>=34 kt, NATURE not ET/MX)",
                     "keep_deg": 4.0, "core_deg": 2.0, ...},   # the rule, as data
     "classes": [{"id": 1, "kt": [34, 63], "mph": [39, 72], "label": "TS",
                  "tok": "--tc-wind-1"}, … Cat 5 {"kt": [137, null]}],
     "bias": {"ratio_median": 0.69, "n": 44,
              "note": "ERA5 10 m wind at 0.25° under-reads tropical-cyclone peak
                       winds; class shading from it is lower than the best-track
                       category, which the map shows separately."},
     "census": {"1": 12345, …},                  # cells per class, summed over the
                                                  # asset's STEP grids
     "storms": [{"id": …, "best_peak_kt": 115, "best_category": "CAT 4",
                 "era_peak_kt": 46, "ratio": 0.4,
                 "life": {"birth": "2026-08-19T00:00:00Z", "death": "...",
                          "first_fix": "...", "last_fix": "...",
                          "ongoing": false, "track_open": true,
                          "alive_steps": 21},
                 "days": [{"d": "2026-08-19", "max_kt": 46,     # peak over its steps
                           "lat0": 15.5, "lon0": -173.5, "ny": 11, "nx": 11,
                           "grids": [{"h": 6, "label": "06Z", "max_kt": 44,
                                      "rle": [kt, run, kt, run, …]},
                                     {"h": 12, "label": "12Z", "max_kt": 46,
                                      "rle": […]}]}]}   # run-length u8; ONE grid per
                                                          # live 6-hourly step

Grid geometry (THE PLACEMENT CONTRACT, pinned by tests/test_tc_wind.py): cell
``(i, j)`` is centred at ``lat0 + i*step_deg`` (row 0 is the southern centre)
and ``((lon0 + j*step_deg + 180) % 360) - 180`` (the repo wrap convention — the
grid itself never crosses the dateline in coordinates, the client wraps per
cell, so an antimeridian-crossing storm needs no special case). The geometry is
shared by every step of one day (the window is the day's), so ``grids`` are
interchangeable at the same ``lat0/lon0/ny/nx``.

Runs on the ewb LXC (network + CPU): ``ops/remote.sh run python -m
ewb_live.viz.tc_wind --days 90``. Pure geometry/classification is offline and
unit-tested; only the read path touches the network.

Audit a built asset anywhere, no network: ``python -m ewb_live.viz.tc_wind
--audit`` prints the storm-by-storm dissipation table (cells on the last live
day, on the day after it — must be 0 — before its first live day, and the
furthest shaded cell from a live best-track fix) and exits non-zero on any
violation.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import statistics
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

SCHEMA = 3                        # 2: the dissipation contract (life window,
                                  #    per-step masking, per-storm "life")
                                  # 3: ONE GRID PER 6-HOURLY STEP — the field the
                                  #    map paints is the 10 m wind of the displayed
                                  #    hour, not the day's maximum (owner 2026-11-19)
OUT_NAME = "tc_wind.json"
SIZE_LIMIT = 8_000_000          # bytes; 44 storms ≈ 0.2 MB (measured)
MS_TO_KT = 1.0 / 0.514444       # m/s -> kt
FLOOR_KT = 34.0                 # tropical-storm force (39 mph)
STEP_DEG = 0.25                 # the ARCO grid (the read resolution)
OUT_DEG = 1.0                   # the RENDERED cell: the map's own legibility step.
                                # A 0.25° cell is half a CSS pixel on a 700 px world
                                # sheet, so the field is max-pooled to 1° — the same
                                # OR-over-cells convention (and the same ~1° scale) as
                                # the map's raster lanes. Nothing is interpolated.
HALF_DEG = 5.0                  # default window half-width around a day's fixes
MAX_HALF_DEG = 12.0             # a day's box never grows past this
# --- the storm's OWN envelope (the dissipation rule, see the module docstring)
KEEP_DEG = 4.0                  # a cell further than this from the step's centre
                                # is not this storm's wind: a 34 kt wind radius (R34)
                                # is ~110 km for a small storm and ~350 km (3.2°) for
                                # the largest ones on record, so 4° covers every real
                                # TC and excludes the monsoon/neighbor flow beyond it.
CORE_DEG = 2.0                  # the envelope is the >=34 kt component seeded here
HOURS = (0, 6, 12, 18)          # the four synoptic steps of a UTC day
FACTOR = int(round(OUT_DEG / STEP_DEG))   # fine cells per coarse cell
LAT_N, LON_N = 721, 1440        # ARCO 0.25° global grid (90..-90, 0..359.75)
WIND_VARS = ["10m_u_component_of_wind", "10m_v_component_of_wind"]

# Saffir-Simpson by sustained wind (owner's scale, NHC thresholds), with mph
# alongside because the owner's threshold was quoted in both units.
CLASSES = [
    {"id": 1, "kt": [34, 63], "mph": [39, 72], "label": "TS", "tok": "--tc-wind-1"},
    {"id": 2, "kt": [64, 82], "mph": [74, 95], "label": "CAT 1", "tok": "--tc-wind-2"},
    {"id": 3, "kt": [83, 95], "mph": [96, 110], "label": "CAT 2", "tok": "--tc-wind-3"},
    {"id": 4, "kt": [96, 112], "mph": [110, 129], "label": "CAT 3", "tok": "--tc-wind-4"},
    {"id": 5, "kt": [113, 136], "mph": [130, 156], "label": "CAT 4", "tok": "--tc-wind-5"},
    {"id": 6, "kt": [137, None], "mph": [157, None], "label": "CAT 5", "tok": "--tc-wind-6"},
]
# class edges in kt on the u8 grid (0 = below the floor = not drawn)
EDGES_KT = np.array([0, 34, 64, 83, 96, 113, 137], dtype=np.uint8)

SOURCE = ("ERA5 10 m wind (10m_u/v_component_of_wind, ERA5 ARCO 0.25°), "
          "00/06/12/18Z, the one 6-hourly step the map is showing")
BIAS_NOTE = ("ERA5 10 m wind at 0.25° resolves no eyewall and is a 10-minute "
             "mean, so it under-reads tropical-cyclone peak winds: across the "
             "published storms the field's maximum inside the storm's own envelope "
             "averaged {ratio}× the IBTrACS best-track peak. Category shading "
             "from this field is always at or below the best-track category, "
             "which the map shows on the storm itself.")


# ------------------------------------------------------------- pure parts --
def class_of(kt: np.ndarray) -> np.ndarray:
    """u8 kt grid -> u8 class grid (0 where below the 34 kt floor).

    Class ids follow CLASSES (1 = TS … 6 = Cat 5); the top class is open.
    """
    return np.clip(np.searchsorted(EDGES_KT, kt, side="right") - 1, 0, 6).astype(np.uint8)


def window_for(pts: list, half_deg: float = HALF_DEG) -> tuple[float, float, float]:
    """One day's positions -> (centre_lat, centre_lon, half_deg).

    Centred on the points and grown to hold them PLUS a full `KEEP_DEG` envelope
    around the outermost one (so no step's storm-force field is cut by the box),
    capped at MAX_HALF_DEG. The producer feeds it the storm's interpolated
    positions AT THAT DAY'S ALIVE STEPS (`step_windows`), never the whole best
    track: a day with no live step gets no window, hence no shading.
    """
    lats = np.array([p[0] for p in pts], float)
    lons = np.array([p[1] for p in pts], float)
    # circular mean of the longitudes, so a day straddling ±180 centres right
    ang = np.deg2rad(lons)
    lon_c = float(np.rad2deg(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())))
    spread = max(float(lats.max() - lats.min()),
                 float((np.abs(((lons - lon_c + 180) % 360) - 180)).max() * 2))
    half = min(MAX_HALF_DEG, max(half_deg, spread / 2 + KEEP_DEG))
    return float(lats.mean()), ((lon_c + 180.0) % 360.0) - 180.0, half


# ------------------------------------------- dissipation: life, steps, centre --
def to_ms(v) -> int | None:
    """'2026-08-19T06:00:00Z' | '2026-08-19' | '2026081906' -> epoch ms (UTC).

    None for anything unparseable: an unknown time is never guessed at, and the
    caller drops the row (a fix with no time cannot be inside a life window).
    """
    s = str(v or "").strip()
    if not s:
        return None
    if len(s) == 10 and s.isdigit():                 # the map's frame key
        s = f"{s[:4]}-{s[4:6]}-{s[6:8]}T{s[8:10]}:00:00Z"
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)        # a naked stamp is UTC here
    return int(d.timestamp() * 1000)


def iso_z(ms: int | None) -> str | None:
    """epoch ms -> 'YYYY-MM-DDTHH:MM:SSZ' (the repo's stamp format)."""
    if ms is None:
        return None
    return (dt.datetime(1970, 1, 1)
            + dt.timedelta(milliseconds=int(ms))).strftime("%Y-%m-%dT%H:%M:%SZ")


def life_window(ev: dict) -> dict:
    """When this storm EXISTED, from the lane's own published window.

    ``start`` is the first qualifying fix and ``end`` the last qualifying fix +
    one fix step (exclusive) — see ``ewb_live/events/tc.py``: qualifying means
    >= 34 kt with ``NATURE`` not ``ET``/``MX``. So "alive" here and "the event"
    on the event page are the SAME interval, and a post-tropical tail is outside
    it by construction. A storm is ``ongoing`` when its last qualifying fix is
    also its last fix, i.e. the archive simply ran out (``details.ongoing``).
    """
    d = ev.get("details") or {}
    birth, death = to_ms(ev.get("start")), to_ms(ev.get("end"))
    if birth is None or death is None or death <= birth:
        raise ValueError(f"{ev.get('id')}: unusable event window "
                         f"{ev.get('start')!r}..{ev.get('end')!r}")
    rows = live_rows(d.get("track"), birth, death)
    step_h = _num(d.get("fix_step_h"), 6.0) or 6.0
    return {"birth": birth, "death": death,
            "first_fix": rows[0][0] if rows else None,
            "last_fix": rows[-1][0] if rows else None,
            "step_h": float(step_h),
            "ongoing": bool(d.get("ongoing")),
            "alive_steps": len(live_steps(birth, death))}


def live_rows(track, birth: int, death: int) -> list[tuple[int, float, float]]:
    """Best-track rows INSIDE the life window, as (ms, lat, lon-unwrapped).

    Rows outside it — the pre-genesis disturbance before the first qualifying
    fix and the extratropical remnant after the last one — are not part of the
    storm's position series, so they cannot drag the interpolated centre, the
    window or the shading. Longitudes are unwrapped along the track (the repo's
    antimeridian convention) so a crossing storm interpolates the short way.
    """
    out: list[tuple[int, float, float]] = []
    for row in track or []:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        ms = to_ms(row[0])
        try:
            lat, lon = float(row[1]), float(row[2])
        except (TypeError, ValueError):
            continue
        if ms is None or not (np.isfinite(lat) and np.isfinite(lon)):
            continue
        if abs(lat) > 90.0 or abs(lon) > 360.0:      # an impossible fix
            continue
        if not (birth <= ms < death):                # outside the storm's life
            continue
        out.append((ms, lat, ((lon + 180.0) % 360.0) - 180.0))
    out.sort()
    lons = np.array([p[2] for p in out], float)
    for k in range(1, lons.size):                     # unwrap along the track
        if lons[k] - lons[k - 1] > 180.0:
            lons[k:] -= 360.0
        elif lons[k] - lons[k - 1] < -180.0:
            lons[k:] += 360.0
    return [(m, la, float(lo)) for (m, la, _), lo in zip(out, lons)]


def live_steps(birth: int, death: int) -> list[dt.datetime]:
    """The synoptic steps that exist while the storm is alive: 00/06/12/18Z with
    birth <= t < death. Nothing before the first qualifying fix, nothing at or
    after the last one plus a step — that IS the dissipation."""
    t0 = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=birth)
    t1 = dt.datetime(1970, 1, 1) + dt.timedelta(milliseconds=death)
    out = []
    day = t0.date()
    epoch = dt.datetime(1970, 1, 1)
    while day <= t1.date():
        for h in HOURS:
            T = dt.datetime.combine(day, dt.time(h))
            ms = int((T - epoch).total_seconds() * 1000)
            if birth <= ms < death:
                out.append(T)
        day += dt.timedelta(days=1)
    return out


def centre_at(rows: list, when: dt.datetime) -> tuple[float, float]:
    """The storm's position at a step: linear between its live fixes, wrapped.

    Clamped at both ends (np.interp), which is the honest answer for a step in
    the window with no fix bracketing it — a 6-hourly step just after the last
    live fix sits at the last live position, not somewhere between that and the
    extratropical fix the window excluded.
    """
    ms = np.array([r[0] for r in rows], float)
    lats = np.array([r[1] for r in rows], float)
    lons = np.array([r[2] for r in rows], float)
    t = (when - dt.datetime(1970, 1, 1)).total_seconds() * 1000.0
    if ms.size == 1:                                  # np.interp wants >= 2 points
        return float(lats[0]), float(lons[0])
    return float(np.interp(t, ms, lats)), float(np.interp(t, ms, lons))


def step_windows(centres: dict, half_deg: float = HALF_DEG) -> dict:
    """{step: (lat, lon)} -> {UTC date: (centre_lat, centre_lon, half_deg)} for
    the days the storm was alive. A day with no alive step gets NO window, so
    the day after a storm's last fix has nothing to shade at all."""
    by_day: dict[dt.date, list[tuple[float, float]]] = {}
    for T, pos in centres.items():
        by_day.setdefault(T.date(), []).append((float(pos[0]), wrap_lon(pos[1])))
    return {day: window_for(pts, half_deg) for day, pts in by_day.items()}


def wrap_lon(x: float) -> float:
    return ((float(x) + 180.0) % 360.0) - 180.0


def cell_axes(lat0: float, lon0: float, ny: int, nx: int,
              step: float = STEP_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Cell CENTRES of a window: latitudes (northward from lat0) and longitudes
    wrapped to [-180, 180) — the same placement contract grid_geometry() states."""
    return (lat0 + np.arange(ny) * step,
            wrap_lon_arry(lon0 + np.arange(nx) * step))


def wrap_lon_arry(x: np.ndarray) -> np.ndarray:
    return ((np.asarray(x, float) + 180.0) % 360.0) - 180.0


def dist_deg(lat: np.ndarray, lon: np.ndarray, plat: float, plon: float) -> np.ndarray:
    """Degrees from each cell centre to one point, with the longitude gap shrunk
    by cos(latitude) — good enough at storm scale (and the map's own hover uses
    the same equirectangular idea)."""
    dlon = ((np.asarray(lon) - plon + 540.0) % 360.0) - 180.0
    return np.hypot(np.asarray(lat) - plat, dlon * np.cos(np.deg2rad(lat)))


def envelope(keep: np.ndarray, seed: tuple[int, int]) -> np.ndarray:
    """The 8-connected component of `keep` that holds `seed`.

    A storm's wind field is one contiguous thing; a >=34 kt patch that is not
    connected to the storm's own core is some other weather (a monsoon jet, a
    neighbouring low, a front) and is not drawn in the storm's name. Dilation
    inside the mask instead of a per-cell walk: a day's window is ~70x70 cells.
    """
    if not keep.any() or not keep[seed]:
        return np.zeros_like(keep)
    cur = np.zeros_like(keep)
    cur[seed] = True
    for _ in range(keep.shape[0] + keep.shape[1]):
        nxt = cur.copy()
        nxt[1:, :] |= cur[:-1, :]
        nxt[:-1, :] |= cur[1:, :]
        nxt[:, 1:] |= cur[:, :-1]
        nxt[:, :-1] |= cur[:, 1:]
        nxt[1:, 1:] |= cur[:-1, :-1]
        nxt[1:, :-1] |= cur[:-1, 1:]
        nxt[:-1, 1:] |= cur[1:, :-1]
        nxt[:-1, :-1] |= cur[1:, 1:]
        nxt &= keep
        if np.array_equal(nxt, cur):
            return cur
        cur = nxt
    return cur


def keep_mask(sub: np.ndarray, lats: np.ndarray, lons: np.ndarray,
              centre: tuple[float, float], others: list = (),
              keep_deg: float = KEEP_DEG, core_deg: float = CORE_DEG) -> np.ndarray:
    """One step's kt field in one window -> the cells that are THIS storm's wind.

    A cell survives three tests, in order:
      * it reached storm force (the floor is already 0 in `sub`),
      * it is within `keep_deg` of THIS storm's centre at THIS step and no other
        live storm's centre is closer (attribution by nearest fix: two storms
        side by side each keep their own half, and each ends on its own date),
      * it is connected to the storm's own core — the kept cell nearest the
        centre, which must itself be within `core_deg` of it. No core, no field:
        where the reanalysis has no storm-force wind near the best-track centre
        the lane draws nothing rather than shading whatever is blowing nearby.
    """
    keep = np.asarray(sub) > 0
    if not keep.any():
        return keep
    d = dist_deg(lats[:, None], lons[None, :], centre[0], centre[1])
    keep &= d <= keep_deg
    for (olat, olon) in others:
        # STRICTLY nearer, so a cell exactly on the frontier of two storms is
        # claimed by neither at that step: one cell, one storm, no double count
        keep &= d < dist_deg(lats[:, None], lons[None, :], olat, olon)
    if not keep.any():
        return keep
    idx = np.unravel_index(int(np.argmin(np.where(keep, d, np.inf))), keep.shape)
    if d[idx] > core_deg:
        return np.zeros_like(keep)
    return envelope(keep, idx)


def snap_lattice(v: float, step: float, origin: float) -> float:
    """Snap a coordinate onto the read lattice (``origin + k*step``).

    The contract is only worth this much shouting if it is exact: a window whose
    origin sits between data cells would have `window_indices` ROUND it, and the
    published ``lat0 + i*step`` would then be up to half a cell away from the
    cell actually stored. Snapping the origin first makes the two identical.
    Latitude is snapped against -90 (the pole row is a real cell), longitude
    against -180.
    """
    return round((float(v) - origin) / step) * step + origin


def grid_geometry(lat_c: float, lon_c: float, half: float,
                  step: float = STEP_DEG) -> tuple[float, float, int, int]:
    """Window centre -> (lat0, lon0, ny, nx) of the cell-CENTRE grid it selects.

    THE PLACEMENT CONTRACT (the map draws exactly this, and tests/test_tc_wind.py
    pins it): cell (i, j) sits at latitude lat0 + i*step — rows run NORTH from
    the southern centre — and longitude lon0 + j*step wrapped to [-180, 180).
    lat0/lon0 are therefore the south/west cell centres, ON THE DATA LATTICE by
    construction (the centre is snapped first, then the box offset by a whole
    number of cells), so the cell the contract names is the cell that was read;
    the box holds an ODD number of cells and is symmetric about that centre. lon0
    stays in the repo's [-180, 180) frame so a window that straddles the dateline
    is just a wrapped column range. Latitude is clamped by SLIDING the centre, so
    the whole window stays inside the grid instead of running off a pole or
    repeating the edge row.
    """
    k = int(round(half / step))                  # half-width in CELLS
    n = 2 * k + 1
    span = 2 * k * step
    lat_c = min(90.0 - span / 2, max(-90.0 + span / 2, snap_lattice(lat_c, step, -90.0)))
    lat0 = lat_c - k * step
    lon0 = wrap_lon(snap_lattice(lon_c, step, -180.0) - k * step)
    return round(lat0, 4), round(lon0, 4), n, n

def window_indices(lat0: float, lon0: float, ny: int, nx: int,
                   step: float = STEP_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Cell centres -> row indices on the ascending-latitude grid and column
    indices on the 0..359.75 ARCO longitude axis (wrapped, so a window that
    straddles ±180 is just a wrapped column range — no special case)."""
    jj = np.rint((np.arange(ny) * step + lat0 + 90.0) / step).astype(int)
    ii = np.rint((np.arange(nx) * step + lon0 + 180.0) / step).astype(int) % LON_N
    # latitude is clipped rather than wrapped: a window that would run past a
    # pole repeats the edge row, which grid_geometry() keeps from happening
    return np.clip(jj, 0, LAT_N - 1), ii


def rle_grid(kt: np.ndarray) -> list[int]:
    """u8 grid -> [value, run_length, value, run_length, ...] row-major.

    Run-length, not base64+zlib, because the browser has no synchronous inflate:
    a day's window is a few hundred near-identical cells (mostly zeros), so runs
    are both smaller than the raw bytes and decodable in a ten-line JS loop
    without a vendored decompressor. Round-trips exactly (see the test).
    """
    flat = kt.astype(np.uint8).reshape(-1)
    if not flat.size:
        return []
    edges = np.flatnonzero(flat[1:] != flat[:-1]) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [flat.size]))
    out: list[int] = []
    for a, b in zip(starts, ends):
        out.append(int(flat[a]))
        out.append(int(b - a))
    return out


def unpack_rle(rle: list[int], n: int) -> np.ndarray:
    """The inverse of rle_grid() (and the shape of the JS decoder)."""
    out = np.empty(n, np.uint8)
    k = 0
    for v, run in zip(rle[::2], rle[1::2]):
        out[k:k + run] = v
        k += run
    if k != n:
        raise ValueError(f"rle grid holds {k} cells, expected {n}")
    return out


def ms_to_kt(ms):
    """m/s -> kt (the ARCO unit -> the owner's unit). Scalars and fields alike."""
    return np.asarray(ms, dtype=np.float64) * MS_TO_KT


def day_steps(day: dt.date) -> list[dt.datetime]:
    return [dt.datetime.combine(day, dt.time(h)) for h in HOURS]


# ---------------------------------------------------------- read the ARCO --
def open_wind_store():
    """The ERA5 ARCO zarr with only the two 10 m wind components selected."""
    from ewb_live.sources.mlc_era5 import ARCO_URI   # one URI, one env override
    import xarray as xr

    ds = xr.open_zarr(ARCO_URI, storage_options={"token": "anon"},
                      consolidated=False, decode_times=True)
    return ds[WIND_VARS]


class NoWindData(RuntimeError):
    """No 10 m wind step exists for that day (past the ARCO cutoff): an honest
    absence, never a zero field — the map gets no entry for the day at all."""


def step_fields(ds, day: dt.date) -> list[tuple[dt.datetime, np.ndarray]]:
    """The day's available 6-hourly steps as ``(time, u8 kt field)``, floored at
    0 below the 34 kt floor, on the ascending-latitude / [-180,180)-longitude
    frame. ~1 MB of u8 per step, four in flight (~50 MB float32 while the day is
    being read).

    ARCO gives (time, lat descending 90..-90, lon ascending 0..359.75); flipping
    the rows and rolling the columns by half the axis puts it in the frame every
    other map asset uses, so index arithmetic here matches the rest of the repo.
    A step missing from the archive (very recent dates) drops out of the list —
    the build then simply has no frame to mask at that time, which is an absence,
    not a zero. The per-step split is what makes dissipation possible: shading a
    day's MAXIMUM cannot know that the storm died at 06Z.
    """
    times = np.array([np.datetime64(t) for t in day_steps(day)], dtype="datetime64[ns]")
    # only labels the store actually has: a time past the archive's end is absent
    # from its axis, and xarray raises on a missing exact label
    have = np.isin(times, np.asarray(ds.indexes["time"].values))
    if not have.any():
        raise NoWindData(f"tc_wind: no 10 m wind steps for {day}")
    blk = ds.sel(time=times[have]).load()
    u = np.asarray(blk[WIND_VARS[0]].values, np.float32)
    v = np.asarray(blk[WIND_VARS[1]].values, np.float32)
    del blk
    if u.ndim != 3:
        raise RuntimeError(f"tc_wind: unexpected 10 m wind shape {u.shape} for {day}")
    # ARCO fills missing recent times with NaN: a step that is missing drops out
    # of the day, and a day with NO valid step raises (no entry, never a zero
    # field that would read as "no storm force that day")
    finite = np.isfinite(u) & np.isfinite(v)
    if not finite.any():
        raise NoWindData(f"tc_wind: no 10 m wind steps for {day}")
    sp = np.hypot(np.where(finite, u, 0.0), np.where(finite, v, 0.0))
    sp = np.where(finite.any(axis=0), sp, 0.0)             # (n, 721, 1440) m/s
    del u, v
    out = []
    for n, t in enumerate(times[have]):
        T = t.astype("datetime64[s]").item()       # naive UTC datetime, == a step
        f = sp[n][::-1]                                # latitude ascending
        f = np.roll(f, LON_N // 2, axis=1)             # longitude -180..179.75
        kt = np.rint(ms_to_kt(f)).astype(np.uint16).clip(0, 255).astype(np.uint8)
        out.append((T, np.where(kt < int(FLOOR_KT), np.uint8(0), kt)))
    return out


def day_max_kt(ds, day: dt.date) -> np.ndarray:
    """Global u8 field: the max 10 m wind (kt, floored) over the day's steps.

    Kept because it is the frame cadence the layer's own time axis uses and it
    pins the floor/NoWindData contract in the tests; the BUILD shades per step
    (see step_fields) so a storm's field can end when the storm does.
    """
    fs = step_fields(ds, day)
    out = fs[0][1].copy()
    for _, f in fs[1:]:
        np.maximum(out, f, out=out)
    return out


def crop(field: np.ndarray, lat0: float, lon0: float, ny: int, nx: int) -> np.ndarray:
    jj, ii = window_indices(lat0, lon0, ny, nx)
    return field[np.ix_(jj, ii)]


def rebin_max(g: np.ndarray, factor: int) -> np.ndarray:
    """Max-pool a window onto a coarser lattice (factor = OUT_DEG / STEP_DEG).

    A coarse cell means "some fine cell inside me reached this wind" — an OR over
    cells, which is exactly how the map's raster lanes are stored, and the honest
    way to show a threshold map at 1°: nothing is averaged away and nothing is
    invented. The coarse lattice SHARES the window's southwest cell centre, so the
    placement contract is unchanged (cell (i, j) is still lat0 + i*step_deg, with
    step_deg now the coarse one); a trailing partial block covers the ragged end
    of a window whose width is not a multiple of the coarse cell.
    """
    if factor <= 1:
        return g.astype(np.uint8)
    rows = np.rint(np.arange(g.shape[0]) / factor).astype(int)
    cols = np.rint(np.arange(g.shape[1]) / factor).astype(int)
    out = np.zeros((int(rows.max()) + 1, int(cols.max()) + 1), np.uint8)
    np.maximum.at(out, (rows[:, None], cols[None, :]), g)
    return out


# ------------------------------------------------------------------ build --
def _tc_events(results_dir: Path) -> list[dict]:
    doc = json.loads((results_dir / "events_selected.json").read_text())
    events = doc.get("events", []) if isinstance(doc, dict) else doc
    out = [e for e in events if e.get("event_type") == "tropical_cyclone"
           and isinstance((e.get("details") or {}).get("track"), list)
           and e["details"]["track"]]
    out.sort(key=lambda e: str(e.get("start")))
    return out


def _num(v, dflt=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dflt
    return f if np.isfinite(f) else dflt


def build(results_dir: Path, *, days: int | None = None, half_deg: float = HALF_DEG,
          limit: int | None = None, store=None, out_name: str = OUT_NAME) -> Path:
    """Write the wind-field asset next to events_selected.json; returns the path.

    ``days`` trims the window to the last N days (the weekly run's window),
    ``limit`` builds only the first N storms (dev/iteration).
    """
    events = _tc_events(results_dir)
    if days:
        end = max(dt.date.fromisoformat(str(e["end"])[:10]) for e in events) if events else None
        if end:
            lo = end - dt.timedelta(days=days - 1)
            events = [e for e in events if dt.date.fromisoformat(str(e["start"])[:10]) >= lo]
    if limit:
        events = events[:limit]
    if not events:
        logger.info("tc_wind: no published tropical_cyclone events — nothing to build")

    plans = []
    for ev in events:
        try:
            life = life_window(ev)
        except ValueError as exc:               # an unusable window: skip, loudly
            logger.warning("tc_wind: %s", exc)
            continue
        det = ev["details"]
        rows = live_rows(det["track"], life["birth"], life["death"])
        # the storm's position at EVERY step of its life, interpolated between its
        # live fixes: the shading follows the storm, never a stale last position
        centres = {T: centre_at(rows, T) for T in live_steps(life["birth"], life["death"])} \
            if rows else {}
        wins = step_windows(centres, half_deg)
        plans.append({"ev": ev, "life": life, "centres": centres, "wins": wins,
                      "geo": {d: grid_geometry(*w, STEP_DEG) for d, w in wins.items()},
                      "grid": {}, "measured": set()})

    need = sorted({d for p in plans for d in p["wins"]})
    nodata: list[str] = []
    data_end: int | None = None      # last 10 m wind step with real data (ms)
    if need:
        if store is None:
            store = open_wind_store()
        for i, day in enumerate(need):
            try:
                steps = step_fields(store, day)      # 4 x 1 MB u8: one day in flight
            except NoWindData as exc:
                logger.warning("%s — skipping the day", exc)
                nodata.append(day.isoformat())
                continue
            # NOT the store's axis maximum: ARCO lists times it has not filled
            # yet (NaN), so the axis runs past the data. The last step this build
            # read with real values is the cutoff — and in the weekly build the
            # newest storms' windows abut it, which is the whole point.
            data_end = max(data_end or 0,
                           int(max(T for T, _ in steps)
                               .replace(tzinfo=dt.timezone.utc).timestamp() * 1000))
            # every storm alive at this step, so a cell can be attributed to the
            # nearest live centre ACROSS storms (two storms, one envelope)
            for T, fld in steps:
                alive = [(p, p["centres"][T]) for p in plans if T in p["centres"]]
                for p, (clat, clon) in alive:
                    lat0, lon0, ny, nx = p["geo"][day]
                    p["measured"].add(day)     # measured: an empty field is a field
                    sub = crop(fld, lat0, lon0, ny, nx)
                    if not sub.any():
                        continue
                    lats, lons = cell_axes(lat0, lon0, ny, nx, STEP_DEG)
                    m = keep_mask(sub, lats, lons, (clat, clon),
                                  [o for q, o in alive if q is not p])
                    if not m.any():
                        continue
                    acc = np.where(m, sub, 0)
                    # ONE grid per (day, step): the map's frames are exactly these
                    # four hours, so the field it paints at 06Z is the wind that
                    # blew at 06Z. A cell has exactly one owner inside one step
                    # (nearest live centre), so nothing is accumulated across
                    # storms here and no day-level maximum is written any more.
                    p["grid"][(day, T)] = acc
            if (i + 1) % 10 == 0:
                logger.info("tc_wind: %d/%d days read", i + 1, len(need))

    storms, ratios, census = [], [], {str(c["id"]): 0 for c in CLASSES}
    for p in plans:
        ev, det, life = p["ev"], p["ev"]["details"], p["life"]
        entries, era_peak, peak_day = [], 0, None
        for day in sorted(p["measured"]):
            if day not in p["wins"]:                # no alive step this day
                continue
            lat0, lon0, ny, nx = p["geo"][day]
            # the day's steps, ascending; each keeps its own grid
            its = sorted(T for (d, T) in p["grid"] if d == day)
            steps_out, day_max, shape = [], 0, (ny // FACTOR + 1, nx // FACTOR + 1)
            for T in its:
                sub = rebin_max(p["grid"][(day, T)], FACTOR)
                shape = sub.shape
                cls = class_of(sub)
                for cid in census:
                    census[cid] += int((cls == int(cid)).sum())
                mx = int(sub.max())
                steps_out.append({"h": T.hour, "label": f"{T.hour:02d}Z",
                                  "max_kt": mx, "rle": rle_grid(sub)})
                day_max = max(day_max, mx)
                if mx >= era_peak:
                    era_peak, peak_day = mx, day
            if not steps_out:
                continue
            entries.append({"d": day.isoformat(), "max_kt": day_max,
                            "grids": steps_out,
                            "lat0": lat0, "lon0": lon0, "ny": shape[0], "nx": shape[1]})
        best = _num(det.get("peak_wind_kt"), _num(ev.get("peak_value")))
        # ratio None when nothing was measured (no field at all) — a 0.00x would
        # say "the field read zero", which is a measurement, not an absence
        ratio = round(era_peak / best, 2) if best and era_peak else None
        if ratio is not None:
            ratios.append(ratio)
        storms.append({"id": ev["id"], "title": ev.get("title"),
                       "name": str(det.get("name") or "").strip(),
                       "best_peak_kt": best,
                       "best_category": str(det.get("peak_category") or "").strip(),
                       "era_peak_kt": era_peak, "peak_day": peak_day.isoformat() if peak_day else None,
                       "ratio": ratio,
                       "life": {"birth": iso_z(life["birth"]), "death": iso_z(life["death"]),
                                "first_fix": iso_z(life["first_fix"]),
                                "last_fix": iso_z(life["last_fix"]),
                                # `ongoing` = still OPEN at the end of the data, so the
                                # map draws no dissipation for it. The lane's own
                                # details.ongoing only says the best track ends while
                                # the system was still tropical — true for a storm that
                                # dissipated six weeks before the cutoff too, which the
                                # map must NOT present as still alive.
                                "ongoing": bool(life["ongoing"]) and data_end is not None
                                and life["death"] >= data_end,
                                "track_open": bool(life["ongoing"]),
                                "alive_steps": int(life["alive_steps"])},
                       "days": entries})
        logger.info("tc_wind: %s %s best %s kt, field max %s kt (%s), alive %s..%s%s",
                    ev["id"], det.get("name") or "?", best, era_peak,
                    f"{ratio:.2f}x" if ratio is not None else "n/a",
                    iso_z(life["birth"]), iso_z(life["death"]),
                    " [ongoing]" if life["ongoing"] else "")

    doc = {"schema": SCHEMA, "lane": "tropical_cyclone", "source": SOURCE, "cadence_h": 6,
           "step_deg": OUT_DEG, "read_deg": STEP_DEG,
           "half_deg": half_deg, "floor_kt": FLOOR_KT, "units": "kt",
           "classes": CLASSES,
           "dissipation": {
               "window": "the lane's event window: first to last qualifying fix "
                         ">= 34 kt, NATURE not ET/MX (ewb_live/events/tc.py)",
               "steps": "only the 00/06/12/18Z steps inside that window; a step "
                        "before the first qualifying fix or at/after the end "
                        "contributes nothing",
               "keep_deg": KEEP_DEG, "core_deg": CORE_DEG,
               "attribution": "the nearest live centre across all storms at that "
                              "step, then the >=34 kt component connected to the "
                              "storm's own core — a detached monsoon/neighbor "
                              "patch and a post-tropical remnant shade nothing",
               "ongoing": "storms still open at the last wind step with data "
                          "(life.cutoff) are marked life.ongoing; the map draws "
                          "no dissipation for them",
               "cutoff": iso_z(data_end)},
           "bias": {"ratio_median": round(statistics.median(ratios), 2) if ratios else None,
                    "ratio_min": min(ratios) if ratios else None,
                    "ratio_max": max(ratios) if ratios else None, "n": len(ratios),
                    "note": BIAS_NOTE.format(ratio=statistics.median(ratios) if ratios else "?")},
           "census": census, "days_no_data": nodata, "storms": storms}
    raw = json.dumps(doc, separators=(",", ":"))
    if len(raw) > SIZE_LIMIT:
        raise RuntimeError(f"{OUT_NAME} {len(raw)} B exceeds {SIZE_LIMIT} B")
    out = results_dir / out_name
    tmp = out.with_name(out.name + f".tmp.{os.getpid()}")
    tmp.write_text(raw)
    tmp.rename(out)
    logger.info("tc_wind: %d storms, %.0f KB -> %s", len(storms), len(raw) / 1024, out)
    return out


def day_union(e: dict) -> np.ndarray:
    """One day entry -> the union (max) of its step grids: where the storm had
    storm-force wind at ANY of its live steps that day. The audit asks about
    DAYS (does any cell exist once the storm is over?), the asset stores STEPS.
    """
    n = int(e["ny"]) * int(e["nx"])
    u = None
    for gr in e.get("grids") or []:
        g = unpack_rle(gr["rle"], n).reshape(int(e["ny"]), int(e["nx"]))
        u = g if u is None else np.maximum(u, g)
    return u if u is not None else np.zeros((int(e["ny"]), int(e["nx"])), np.uint8)


def audit(results_dir: Path, out_name: str = OUT_NAME) -> list[dict]:
    """Per-storm dissipation audit of a built asset (local, no network).

    The owner's question, answered for every published storm: does any cell of a
    storm's field exist once the storm is over? For each storm: the life window,
    the cells on its LAST live day, on the day AFTER it (must be 0), on days
    before its first live day (must be 0), and the furthest shaded cell from a
    live best-track fix (must be <= keep_deg + one rendered cell, since a 1° cell
    is the max of the 0.25° readings inside it). Reads the event too, so an asset
    that quietly shaded the ET tail is caught rather than trusted.
    """
    doc = json.loads((results_dir / out_name).read_text())
    events = {e["id"]: e for e in _tc_events(results_dir)}
    step = float(doc["step_deg"])
    keep = float((doc.get("dissipation") or {}).get("keep_deg", KEEP_DEG))
    epoch = dt.datetime(1970, 1, 1)
    rows = []
    for s in doc["storms"]:
        ev = events.get(s["id"]) or {}
        det = ev.get("details") or {}
        life = s.get("life") or {}
        birth, death = to_ms(life.get("birth")), to_ms(life.get("death"))
        day_cells: dict[str, int] = {}
        for e in s["days"]:
            day_cells[e["d"]] = int(np.count_nonzero(day_union(e)))
        first_day = last_day = None
        if birth is not None and death is not None:
            first_day = (epoch + dt.timedelta(milliseconds=birth)).date().isoformat()
            last_day = (epoch + dt.timedelta(milliseconds=death - 1000)).date().isoformat()
        after = sum(n for d, n in day_cells.items() if last_day and d > last_day)
        before = sum(n for d, n in day_cells.items() if first_day and d < first_day)
        far = 0.0
        fixes = live_rows(det.get("track") or [], birth or 0, death or 0)
        if fixes:
            fl = [(f[1], f[2]) for f in fixes]
            for e in s["days"]:
                lats, lons = cell_axes(e["lat0"], e["lon0"], e["ny"], e["nx"], step)
                g = day_union(e)
                ii, jj = np.nonzero(g)
                if not len(ii):
                    continue
                d = np.stack([dist_deg(lats[ii], lons[jj], a, b) for a, b in fl], 1)
                far = max(far, float(d.min(axis=1).max()))
        rows.append({"id": s["id"], "name": s.get("name") or "?",
                     "ongoing": bool(life.get("ongoing")),
                     "birth": life.get("birth"), "death": life.get("death"),
                     "days": len(s["days"]), "cells": sum(day_cells.values()),
                     "cells_last_day": day_cells.get(last_day, 0),
                     "cells_after": after, "cells_before": before,
                     "max_dist_deg": round(far, 2),
                     "ok": after == 0 and before == 0 and far <= keep + step})
    return rows


def print_audit(rows: list[dict]) -> bool:
    """The storm-by-storm table the owner asked for; False if any storm fails."""
    hdr = (f"{'storm':16} {'ongoing':7} {'birth':10} {'death':10} "
           f"{'days':>4} {'cells':>6} {'last':>5} {'after':>5} {'before':>6} {'maxdeg':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(f"{r['name'][:15]:16} {'yes' if r['ongoing'] else '-':7} "
              f"{str(r['birth'])[:10]:10} {str(r['death'])[:10]:10} "
              f"{r['days']:4} {r['cells']:6} {r['cells_last_day']:5} "
              f"{r['cells_after']:5} {r['cells_before']:6} {r['max_dist_deg']:6}"
              + ("" if r["ok"] else "   <-- FAIL"))
    bad = [r for r in rows if not r["ok"]]
    print(f"\n{len(rows)} storms, {len(bad)} failing: "
          + (", ".join(r["name"] for r in bad) if bad else "clean"))
    return not bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.tc_wind")
    ap.add_argument("--results", type=Path,
                    default=Path(__file__).resolve().parents[2] / "data" / "results")
    ap.add_argument("--audit", action="store_true",
                    help="audit an existing asset for post-dissipation cells and exit "
                         "(local read-only: no network, no ewb-host guard)")
    ap.add_argument("--days", type=int, default=None,
                    help="only storms starting in the last N days (default: all published)")
    ap.add_argument("--half-deg", type=float, default=HALF_DEG,
                    help="window half-width around a day's live positions (degrees)")
    ap.add_argument("--limit", type=int, default=None, help="build only the first N storms")
    args = ap.parse_args(argv)
    if args.audit:
        return 0 if print_audit(audit(args.results)) else 1

    from ewb_live._guard import require_ewb_host      # network + CPU: ewb LXC only

    require_ewb_host()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build(args.results, days=args.days, half_deg=args.half_deg, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
