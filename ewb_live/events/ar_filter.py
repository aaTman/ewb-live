"""AR object tracking + track-level tropical/TC exclusion — ONE shared fn.

Owner directive (2026-11-09, SPEC "AR tropical exclusion"): a physical AR
must never blink between 6-hourly frames, and the rule is object-level. So:
label the AR-mask connected components per 6-hourly step, link them across
consecutive steps into tracks, and apply exclusion at TRACK level — a track
is IN for all its steps or OUT for all of them. Consumers: detection (events
/ar.py, events carry track_id) and the raw AR map layer (viz/exceedance.py);
the map and the event list can't diverge. Scoring consumes events; purged
events are documented in pair_status.

Track-level exclusion is only as good as the TRACK, and the owner-measured
failure (2026-11-10) was the linker, not the threshold: with no displacement
or age limit, a Southern-Hemisphere storm chain slithered into ONE track of
132 steps / 1.92M cells (ar_20260811T00_4 in the published ledger), and a
single tropical cell then hid a month of mid-latitude ARs — which is how "a
massive AR just appears at 12Z" happens (the chain re-seeds a fresh id for
the same physical object, and the new id has no tropical ancestor). 6.74M
cells were hidden this way vs 7.66M painted, 54% of it in 10 tracks. Hence
the two link caps below (speed, age) and the centroid-based tropical rule.

Linking rules (owner asked for these documented):
- Components per step: seam.periodic label_periodic, 8-connectivity (the
  longitude array seam joins col nx-1 to col 0).
- Strength(parent, child) = |overlap| / |child| (child = later step);
  a child links to a parent when strength >= TRACK_OVERLAP_FRAC **and** the
  parent/child centroids are within LINK_MAX_KM (an AR cannot outrun that)
  **and** the parent track is younger than LINK_MAX_STEPS.
- SPLIT: the parent's strongest linked child inherits the parent's id;
  the other linked children start NEW tracks (physical lineage stays with
  the dominant fragment).
- MERGE: a child linking to several parents inherits the strongest
  parent's id; losing parents' tracks END at the previous step.
- GAP BRIDGE (owner 2026-11-09, option B): a track that has NO linked
  child at step t+1 may still pass its id to a child at step t+2 —
  max gap = ONE step (6 h); a 2-step gap ends the track. The gap step
  gets the parent's last footprint written into ``labels`` flagged as
  bridged (track 'bridged' step list) — DISPLAY ONLY: 'keep' (the
  physical/detection mask) never contains bridged cells, so events,
  scoring and the tropical/TC screens see physical pixels only.

Exclusion rules applied to a finished track:
- TROPICAL (owner, 2026-11-10 revision): a tropical *cell* is not the test,
  the AR's *centroid* is. A track is dropped (at every step) only when its
  area-weighted centroid stays inside ±TROPICAL_LAT for
  TROPICAL_PERSIST_STEPS consecutive steps — a whole day of 6-hourly frames.
  A brief plunge across 15° is weather, not a tropical system.
- TC (STEP-level, revised 2026-11-10): cells of the track within TC_SCREEN_KM
  of an IBTrACS cyclone point are removed AT THE STEPS THEY TOUCH, not for the
  track's whole life. A cyclone crossing an AR contaminates the frames it is in
  (the storm's own moisture flux is not an AR); it says nothing about the 40
  frames before or after. Whole-track removal here measured as the bigger bug:
  the owner's 2026-08-13 north-Atlantic report ("IVT values just disappear":
  1 304 -> 85 painted cells against 5 238 real mask cells) and the 2026-08-14/15
  south-Atlantic report both sit on `reason: tc` tracks (`ar_20260811T00_9`,
  170 623 cells / 56 steps; `ar_20260815T12_254`, 73 422 cells) — one storm
  erased weeks of mid-latitude AR. A track whose EVERY step is TC-hit is still
  dropped entirely (reason 'tc'); a partially hit track stays IN and publishes
  `tc_steps` in the ledger so the removal is auditable.

Track ids: ``ar_<YYYYMMDDTHH>_<seq>`` from the first step's valid time plus
creation order — deterministic for a fixed window start (the scan's rolling
14-day load buffer keeps starts stable across weekly runs).
shortcut: ids are not persisted across rule revisions; upgrade path is a
persisted ledger joined by (start, centroid) if ids must survive rule edits.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

TRACK_OVERLAP_FRAC = 0.25
TROPICAL_LAT = 15.0
# 6-hourly steps: 4 = the centroid sitting inside the tropics for a full day
# (owner 2026-11-10: a touch is not the test, staying is — then drop the AR).
TROPICAL_PERSIST_STEPS = 4
# Parent->child centroid speed cap, per 6-hourly step, in km. Mid-latitude
# ARs advect with the flow at ~10-25 m/s (220-540 km/6 h); 900 km (~41 m/s)
# is deliberately generous so the cap only kills the identity-hopping that
# let one id crawl between ocean basins (see module docstring).
LINK_MAX_KM = 900.0
# A track older than this is a NEW AR, not the same one still going: 40 steps
# = 10 days (observed AR lifetimes are ~2-3 days median, rare beyond a week).
LINK_MAX_STEPS = 40

# 8-connectivity: a single-pixel neck still counts as one object
_STRUCTURE = np.ones((3, 3), dtype=bool)


def _fmt_id(t: np.datetime64, seq: int) -> str:
    s = str(np.datetime64(t, "h"))
    return f"ar_{s[:10].replace('-', '')}T{s[11:13]}_{seq}"


def _centroids(lab: np.ndarray, n: int, lats: np.ndarray,
               lons: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Area-weighted centroid (lat, lon) per local label, as (n+1,) arrays.

    Area-weighted (cos lat) because that is what "the centroid of the event"
    means on a lat-lon grid — an unweighted pixel mean is pulled poleward by
    the shrinking cell area, which matters at the 15 deg tropical threshold.
    Longitude is the mean UNIT VECTOR, so a straddling object's centroid is
    correct across the array seam (and across ±180). Label 0 (background)
    comes back NaN and can never satisfy a distance test.
    """
    w = np.cos(np.radians(lats))[:, None] * np.ones((1, lons.size))
    lab1 = np.broadcast_to(lats[:, None], lab.shape)
    sinl = np.broadcast_to(np.sin(np.radians(lons))[None, :], lab.shape)
    cosl = np.broadcast_to(np.cos(np.radians(lons))[None, :], lab.shape)
    flat = lab.ravel()
    wsum = np.bincount(flat, weights=w.ravel(), minlength=n + 1)
    lat_s = np.bincount(flat, weights=lab1.ravel(), minlength=n + 1)
    sin_s = np.bincount(flat, weights=sinl.ravel(), minlength=n + 1)
    cos_s = np.bincount(flat, weights=cosl.ravel(), minlength=n + 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        lat_c = lat_s / np.where(wsum > 0, wsum, np.nan)
        lon_c = np.degrees(np.arctan2(sin_s, cos_s))
    return np.asarray(lat_c, float), np.asarray(lon_c, float)


def _centroid_km(lat_a, lon_a, lat_b, lon_b) -> np.ndarray:
    """Great-circle distance between centroid pairs (elementwise, NaN-safe)."""
    from .tc_screen import _haversine_km

    return _haversine_km(np.asarray(lat_a, float), np.asarray(lon_a, float),
                          np.asarray(lat_b, float), np.asarray(lon_b, float))


def track_ar_components(mask: np.ndarray, valid_times: np.ndarray,
                        latitude, longitude):
    """Label + link AR components across steps.

    ``mask``: (n_t, ny, nx) bool per 6-hourly step; ``valid_times``:
    datetime64 per step; ``latitude``/``longitude``: the mask's own axes
    (needed for the centroid drift cap and the centroid tropical test — the
    caps are never silently disabled by missing coordinates).
    Returns (labels, tracks): ``labels`` int32 array of
    the mask shape holding per-pixel track index + 1 (0 = no AR; may contain
    DISPLAY-ONLY bridged cells at a 1-step gap — see GAP BRIDGE above);
    ``tracks``: list of {id, steps: [int], size: int, bridged: [int],
    step_lats: {step: centroid lat}}.
    """
    from .seam import label_periodic

    lat_a = np.asarray(latitude, float)
    lon_a = np.asarray(longitude, float)
    labels = np.zeros(mask.shape, dtype=np.int32)
    tracks: list[dict] = []
    prev_lab = None    # previous step's local label grid (int32)
    prev_track = None  # prev-step local label -> track idx
    prev_clat = prev_clon = None   # prev-step centroids per local label
    p2_lab = None      # step t-2 grids (1-step gap bridge, docstring)
    p2_track = None
    p2_clat = p2_clon = None
    for t in range(mask.shape[0]):
        if mask[t].any():
            lab_n = label_periodic(mask[t])  # seam-crossing ARs stay ONE object
            assert isinstance(lab_n, tuple)  # scipy stub union: (labels, n)
            lab = np.asarray(lab_n[0], dtype=np.int32)
            n = int(lab_n[1])
            sizes = np.bincount(lab.ravel(), minlength=n + 1)
        else:   # fully empty step: valid gap frame, keep link history alive
            lab = np.zeros(mask.shape[1:], dtype=np.int32)
            n = 0
            sizes = np.array([mask[t].size])
        cent_lat, cent_lon = _centroids(lab, n, lat_a, lon_a)

        inherit_to = np.full(n + 1, -1, dtype=np.int32)
        linked = np.zeros(n + 1, dtype=bool)
        if (prev_lab is not None and prev_track is not None and n
                and len(prev_track) > 1):   # empty prev step: no live parents
            p_n = len(prev_track) - 1
            c_flat = (prev_lab * (n + 1) + lab).ravel()
            cnt = np.bincount(c_flat[mask[t].ravel()],
                              minlength=(p_n + 1) * (n + 1))
            M = cnt.reshape(p_n + 1, n + 1)  # overlap px, parent x child
            child_best = M[1:].argmax(axis=0) + 1  # strongest parent (>=1)
            strength = M[child_best, np.arange(n + 1)] / np.maximum(sizes, 1)
            # overlap alone let one id CRAWL between oceans over weeks (module
            # docstring): a child must also be within a speed-capable distance
            # of the parent it wants to inherit.
            drift = _centroid_km(prev_clat[child_best], prev_clon[child_best],
                                 cent_lat, cent_lon)
            linked = (strength >= TRACK_OVERLAP_FRAC) & (drift <= LINK_MAX_KM)
            chosen: dict[int, tuple[int, int]] = {}  # child -> (ovl, track)
            for p in np.unique(child_best[linked]):
                ch = np.flatnonzero(linked & (child_best == p))
                c_star = int(ch[np.argmax(M[p, ch])])
                s = int(M[p, c_star])
                # merge: child takes the strongest choosing parent's id
                if c_star not in chosen or s > chosen[c_star][0]:
                    chosen[c_star] = (s, int(prev_track[p]))
            for c, (_s, idx) in chosen.items():
                inherit_to[c] = idx

        # GAP BRIDGE: tracks present at t-2 but with no id at t-1 died at
        # the gap step t-1; their t-2 footprint (minus cells a live track
        # physically occupied at t-1) is the ghost grid. A child at t with
        # NO live link may inherit a ghost parent's id; the gap step then
        # shows that footprint (labels write, 'bridged' flag).
        if p2_lab is not None and p2_track is not None and n:
            g = np.where(p2_lab > 0, p2_track[p2_lab] + 1, 0).astype(np.int32)
            alive_prev = np.unique(prev_track[prev_track >= 0]) + 1 \
                if prev_track is not None else np.zeros(0, np.int32)
            if alive_prev.size:
                g[np.isin(g, alive_prev)] = 0
            g[mask[t - 1]] = 0   # gap step occupied -> not a ghost there
            if g.any():
                tmax = int(g.max())
                # g holds TRACK ids, but p2_clat/p2_clon are indexed by the
                # step's LOCAL label — remap so the drift test compares the
                # right centroids (a track id used as a label index would
                # read out of bounds whenever the window holds many tracks).
                # Size by the largest id EITHER side owns: tmax only covers
                # the ghosts that survived the alive/occupied zeroing.
                gid_max = max(tmax, int(p2_track.max()) + 1)
                g_lat = np.full(gid_max + 1, np.nan)
                g_lon = np.full(gid_max + 1, np.nan)
                owned = np.flatnonzero(p2_track >= 0)
                owned = owned[owned < p2_clat.size]
                g_lat[p2_track[owned] + 1] = p2_clat[owned]
                g_lon[p2_track[owned] + 1] = p2_clon[owned]
                joint = (g * (n + 1) + lab).ravel()[mask[t].ravel()]
                cnt2 = np.bincount(joint, minlength=(tmax + 1) * (n + 1))
                M2 = cnt2.reshape(tmax + 1, n + 1)  # ghost parent x child
                best2 = M2.argmax(axis=0)
                s2 = M2[best2, np.arange(n + 1)] / np.maximum(sizes, 1)
                # the ghost is two steps old, so it gets a doubled speed cap
                drift2 = _centroid_km(g_lat[best2], g_lon[best2],
                                      cent_lat, cent_lon)
                ok = ((best2 > 0) & (s2 >= TRACK_OVERLAP_FRAC)
                      & (drift2 <= 2.0 * LINK_MAX_KM) & ~linked)
                for gid in np.unique(best2[ok]):
                    ch = np.flatnonzero(ok & (best2 == gid))
                    c_star = int(ch[np.argmax(M2[gid, ch])])
                    if inherit_to[c_star] < 0:
                        inherit_to[c_star] = int(gid) - 1
                        labels[t - 1][g == gid] = gid   # display-only
                        tracks[int(gid) - 1]["bridged"].append(t - 1)

        track_of = np.zeros(n + 1, dtype=np.int32)
        for c in range(1, n + 1):
            idx = int(inherit_to[c])
            if idx >= 0 and t - tracks[idx]["steps"][0] >= LINK_MAX_STEPS:
                idx = -1        # elder track: the chain restarts under a new id
            if idx < 0:
                idx = len(tracks)
                tracks.append({"id": _fmt_id(valid_times[t], idx),
                               "steps": [], "size": 0, "bridged": [],
                               "step_lats": {}})
            tracks[idx]["steps"].append(t)
            tracks[idx]["size"] += int(sizes[c])
            tracks[idx]["step_lats"][t] = float(cent_lat[c])
            track_of[c] = idx + 1
        labels[t] = np.where(labels[t] > 0, labels[t], track_of[lab])
        p2_lab, p2_track, p2_clat, p2_clon = prev_lab, prev_track, prev_clat, prev_clon
        prev_lab, prev_clat, prev_clon = lab, cent_lat, cent_lon
        prev_track = track_of - 1
    return labels, tracks


def _storm_pass(labels, mask, latitude, longitude, valid_times, ibtracs_tracks):
    """Storm-driven TC screen: for every IBTrACS track point, look only at
    the ~dlat x dlon cell crop around it and the steps within the time gate,
    and mark every (track, step) whose cells come within TC_SCREEN_KM — the
    step list, because the screen is applied per step (see module docstring).
    Cost scales with storm points (thousands), not tracks (hundreds of
    thousands).

    ``mask`` (the physical AR mask) gates the lookup: ``labels`` also carries
    DISPLAY-only gap-bridge footprints, and a ghost must not drag a track
    into the TC screen — it is not a cell that met the AR rule (box hotfix,
    2026-11-10).
    """
    from .tc_screen import TC_SCREEN_KM, TC_SCREEN_ON, _haversine_km

    hit: dict[int, dict] = {}
    if ibtracs_tracks is None or not len(ibtracs_tracks) or not TC_SCREEN_ON:
        return hit
    import pandas as pd

    from .tc_screen import TC_SCREEN_HOURS

    lat_a = np.asarray(latitude, float)
    lon_a = np.asarray(longitude, float)
    vt = np.asarray(valid_times, "datetime64[h]").astype("datetime64[us]")
    gate = np.timedelta64(int((TC_SCREEN_HOURS + 6) * 3600), "s")
    dlat = TC_SCREEN_KM / 111.32 + 1.0
    pts = ibtracs_tracks
    pt_t = pd.to_datetime(pts["valid_time"]).to_numpy().astype("datetime64[us]")
    pt_lat = np.asarray(pts["latitude"], float)
    pt_lon = np.asarray(pts["longitude"], float)
    name_col = "tc_name" if "tc_name" in pts.columns else "storm_id"
    pt_sid = np.asarray(pts["storm_id"], dtype=object)
    pt_name = np.asarray(pts[name_col], dtype=object)
    for j in range(len(pt_t)):
        steps = np.flatnonzero(np.abs(vt - pt_t[j]) <= gate)
        if not steps.size:
            continue
        la, lo = float(pt_lat[j]), float(pt_lon[j])
        rows = np.flatnonzero(np.abs(lat_a - la) <= dlat)
        dlon = dlat / max(0.2, np.cos(np.radians(la))) + 1.0
        cols = np.flatnonzero(np.abs(((lon_a - lo + 180.0) % 360.0) - 180.0)
                              <= dlon)
        if not rows.size or not cols.size:
            continue
        for t in steps:
            sub = np.where(mask[t][np.ix_(rows, cols)],
                           labels[t][np.ix_(rows, cols)], 0)   # ghosts: display-only
            for lbl in np.unique(sub[sub > 0]):
                ys, xs = np.nonzero(sub == lbl)
                d = float(_haversine_km(la, lo, lat_a[rows[ys]],
                                        lon_a[cols[xs]]).min())
                if d <= TC_SCREEN_KM:
                    k = int(lbl) - 1
                    h = hit.get(k)
                    if h is None:
                        hit[k] = {"ibtracs_sid": str(pt_sid[j]),
                                  "tc_name": str(pt_name[j]),
                                  "distance_km": round(d, 1),
                                  "matched_time": str(vt[t]),
                                  "steps": {int(t)}}
                    else:
                        h["steps"].add(int(t))
                        if d < h["distance_km"]:
                            h.update({"ibtracs_sid": str(pt_sid[j]),
                                      "tc_name": str(pt_name[j]),
                                      "distance_km": round(d, 1),
                                      "matched_time": str(vt[t])})
    for h in hit.values():
        h["steps"] = sorted(h["steps"])
    return hit


def _tropical_persistence(tracks: list[dict]) -> tuple[np.ndarray, list[int]]:
    """Per-track length of the longest run of CONSECUTIVE steps whose centroid
    sits inside ±TROPICAL_LAT (owner 2026-11-10: touch is fine, staying is not).

    Returns (tropical_flag, run_length). A gap step (the track has no id then,
    e.g. a mask dropout or a bridge) breaks the run — 'stays for a day' means
    it was there in every one of those frames.
    """
    flag = np.zeros(len(tracks), bool)
    runs: list[int] = []
    for k, tr in enumerate(tracks):
        run = best = 0
        prev = None
        for t in tr["steps"]:
            if abs(tr["step_lats"].get(t, 90.0)) < TROPICAL_LAT:
                run = run + 1 if prev is not None and t == prev + 1 else 1
                best = max(best, run)
            else:
                run = 0
            prev = t
        runs.append(best)
        flag[k] = best >= TROPICAL_PERSIST_STEPS
    return flag, runs


def _track_bounds(labels, mask, latitude, longitude, n: int):
    """Per-label lat/lon extent over every step, for the exclusion ledger.

    Returns four (n+1,) arrays (lat_min, lat_max, lon_min, lon_max) with
    longitudes normalised to [-180, 180); `lon_max - lon_min > 180` means the
    object straddles the antimeridian, so read the lon pair with that in mind.
    Label 0 (background) stays (+inf, -inf). Costs one pass over the PHYSICAL
    cells only — a gap-bridge footprint never widens a ledger bbox.

    Without these, `ar_tracks.json` could not answer "which dropped track was
    the AR I am looking at on the map?": the ledger had ids, sizes and reasons
    but no location, so an owner report could only be attributed by re-running
    the detector (notes/ar_outage_20261110.md §2).
    """
    lat = np.asarray(latitude, float)
    lon = ((np.asarray(longitude, float) + 180.0) % 360.0) - 180.0
    lat_min = np.full(n + 1, np.inf)
    lat_max = np.full(n + 1, -np.inf)
    lon_min = np.full(n + 1, np.inf)
    lon_max = np.full(n + 1, -np.inf)
    la_g = np.broadcast_to(lat[:, None], (lat.size, lon.size))
    lo_g = np.broadcast_to(lon[None, :], (lat.size, lon.size))
    for t in range(labels.shape[0]):
        sel = mask[t]
        if not sel.any():
            continue
        lab = labels[t][sel]
        o = np.argsort(lab, kind="stable")
        lab_s, la_s, lo_s = lab[o], la_g[sel][o], lo_g[sel][o]
        uniq = np.unique(lab_s)
        starts = np.searchsorted(lab_s, uniq, side="left")
        lat_min[uniq] = np.minimum(lat_min[uniq],
                                   np.minimum.reduceat(la_s, starts))
        lat_max[uniq] = np.maximum(lat_max[uniq],
                                   np.maximum.reduceat(la_s, starts))
        lon_min[uniq] = np.minimum(lon_min[uniq],
                                   np.minimum.reduceat(lo_s, starts))
        lon_max[uniq] = np.maximum(lon_max[uniq],
                                   np.maximum.reduceat(lo_s, starts))
    return lat_min, lat_max, lon_min, lon_max


def ar_filter_tracks(mask, latitude, longitude, valid_times,
                     ibtracs_tracks=None):
    """Track-level exclusion (owner 2026-11-09; tropical rule revised
    2026-11-10, TC rule revised to step level 2026-11-10): drop a whole TRACK
    when its centroid stays inside |lat| < 15 for TROPICAL_PERSIST_STEPS
    consecutive steps, or when EVERY one of its steps is inside the IBTrACS TC
    screen radius; otherwise remove only the TC-touched steps' cells.

    Returns (keep, dropped, labels, tracks): ``keep`` bool mask of the input
    shape (kept tracks' cells, minus step-level TC removals); ``dropped`` the
    dropped track dicts with reason ('tropical' | 'tc'), bbox and centroid;
    ``labels`` the shared track-label array (track idx + 1 — design's raw layer
    can label cells by track id); ``tracks`` all tracks, each gaining 'status'
    (+ 'reason' when dropped, + 'tc_steps' when partially screened).
    """
    mask = np.asarray(mask, bool)
    labels, tracks = track_ar_components(mask, valid_times, latitude, longitude)
    touched, trop_runs = _tropical_persistence(tracks)

    hit_tc_map = _storm_pass(labels, mask, latitude, longitude, valid_times,
                             ibtracs_tracks)
    hit_tc = [hit_tc_map.get(k) for k in range(len(tracks))]
    # step-level TC: a touch removes those steps, a track that is in the storm
    # for ALL its steps is not an AR at all
    tc_full = np.zeros(len(tracks), bool)
    tc_part = np.zeros(len(tracks), bool)
    for k, h in enumerate(hit_tc):
        if h is None:
            continue
        steps = set(tracks[k]["steps"])
        hit_steps = [s for s in h["steps"] if s in steps]
        if not hit_steps:
            hit_tc[k] = None
            continue
        (tc_full if len(hit_steps) >= len(steps) else tc_part)[k] = True

    dropped_idx = touched | tc_full
    # labels are 1-based; index -1 (no AR) reads the leading False; the
    # '& mask' keeps DISPLAY-ONLY bridged cells out of the physical mask
    # (detection/scoring lanes never see them — owner 2026-11-09)
    keep = np.concatenate([[False], ~dropped_idx])[labels] & mask
    for k, h in enumerate(hit_tc):        # partial TC screen, step by step
        if not tc_part[k]:
            continue
        for t in h["steps"]:
            keep[t][labels[t] == k + 1] = False

    need_bounds = bool(dropped_idx.any())
    b = (_track_bounds(labels, mask, latitude, longitude, len(tracks))
         if need_bounds else (None,) * 4)
    dropped = []
    for k, tr in enumerate(tracks):
        if tc_part[k]:
            tr["tc_steps"] = [str(np.datetime64(valid_times[s], "h"))
                              for s in hit_tc[k]["steps"]]
            tr["tc_name"] = hit_tc[k]["tc_name"]
            tr["tc_distance_km"] = hit_tc[k]["distance_km"]
        if not dropped_idx[k]:
            tr["status"] = "kept"
            tr.pop("step_lats", None)
            continue
        tr["status"] = "dropped"
        if touched[k]:
            tr["reason"] = "tropical"
            tr["tropical_run_steps"] = int(trop_runs[k])
            tr["centroid_lat"] = round(float(np.mean(
                list(tr["step_lats"].values()))), 2)
        else:
            tr["reason"] = "tc"
            tr.update({x: v for x, v in (hit_tc[k] or {}).items()
                       if x != "steps"})
            tr["tc_steps"] = [str(np.datetime64(valid_times[s], "h"))
                              for s in (hit_tc[k] or {}).get("steps", [])]
        if need_bounds:
            lat_min, lat_max, lon_min, lon_max = b
            i = k + 1
            if np.isfinite(lat_min[i]):
                tr["bbox"] = [round(float(lat_min[i]), 2),
                              round(float(lat_max[i]), 2),
                              round(float(lon_min[i]), 2),
                              round(float(lon_max[i]), 2)]
        tr["start"] = str(np.datetime64(valid_times[tr["steps"][0]], "h"))
        tr["end"] = str(np.datetime64(valid_times[tr["steps"][-1]], "h"))
        tr["label"] = int(k) + 1
        tr.pop("step_lats", None)    # per-step detail stays out of the ledger
        dropped.append(tr)
    logger.info("ar_filter_tracks: %d tracks, %d dropped (%d tropical, %d tc), "
                "%d tracks partially TC-screened", len(tracks), len(dropped),
                int(touched.sum()), int(tc_full.sum()), int(tc_part.sum()))
    return keep, dropped, labels, tracks
