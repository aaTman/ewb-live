"""AR object tracking + track-level tropical/TC exclusion — ONE shared fn.

Owner directive (2026-11-09, SPEC "AR tropical exclusion"): a physical AR
must never blink between 6-hourly frames, and the rule is object-level
("any cell at ANY time step -> drop the whole AR"). So: label the AR-mask
connected components per 6-hourly step, link them across consecutive steps
into tracks, and apply exclusion at TRACK level — a track is IN for all its
steps or OUT for all of them. Consumers: detection (events/ar.py, events
carry track_id) and the raw AR map layer (viz/exceedance.py); the map and
the event list can't diverge. Scoring consumes events; purged events are
documented in pair_status.

Linking rules (owner asked for these documented):
- Components per step: seam.periodic label_periodic, 8-connectivity (the
  longitude array seam joins col nx-1 to col 0).
- Strength(parent, child) = |overlap| / |child| (child = later step);
  a child links to a parent when strength >= TRACK_OVERLAP_FRAC.
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

# 8-connectivity: a single-pixel neck still counts as one object
_STRUCTURE = np.ones((3, 3), dtype=bool)


def _fmt_id(t: np.datetime64, seq: int) -> str:
    s = str(np.datetime64(t, "h"))
    return f"ar_{s[:10].replace('-', '')}T{s[11:13]}_{seq}"


def track_ar_components(mask: np.ndarray, valid_times: np.ndarray):
    """Label + link AR components across steps.

    ``mask``: (n_t, ny, nx) bool per 6-hourly step; ``valid_times``:
    datetime64 per step. Returns (labels, tracks): ``labels`` int32 array of
    the mask shape holding per-pixel track index + 1 (0 = no AR; may contain
    DISPLAY-ONLY bridged cells at a 1-step gap — see GAP BRIDGE above);
    ``tracks``: list of {id, steps: [int], size: int, bridged: [int]}.
    """
    from .seam import label_periodic

    labels = np.zeros(mask.shape, dtype=np.int32)
    tracks: list[dict] = []
    prev_lab = None    # previous step's local label grid (int32)
    prev_track = None  # prev-step local label -> track idx
    p2_lab = None      # step t-2 grids (1-step gap bridge, docstring)
    p2_track = None
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
            linked = strength >= TRACK_OVERLAP_FRAC
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
                joint = (g * (n + 1) + lab).ravel()[mask[t].ravel()]
                cnt2 = np.bincount(joint, minlength=(tmax + 1) * (n + 1))
                M2 = cnt2.reshape(tmax + 1, n + 1)  # ghost parent x child
                best2 = M2.argmax(axis=0)
                s2 = M2[best2, np.arange(n + 1)] / np.maximum(sizes, 1)
                ok = (best2 > 0) & (s2 >= TRACK_OVERLAP_FRAC) & ~linked
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
            if idx < 0:
                idx = len(tracks)
                tracks.append({"id": _fmt_id(valid_times[t], idx),
                               "steps": [], "size": 0, "bridged": []})
            tracks[idx]["steps"].append(t)
            tracks[idx]["size"] += int(sizes[c])
            track_of[c] = idx + 1
        labels[t] = np.where(labels[t] > 0, labels[t], track_of[lab])
        p2_lab, p2_track = prev_lab, prev_track
        prev_lab = lab
        prev_track = track_of - 1
    return labels, tracks


def _storm_pass(labels, mask, latitude, longitude, valid_times,
                ibtracs_tracks):
    """Storm-driven TC screen: for every IBTrACS track point, look only at
    the ~dlat x dlon cell crop around it and the steps within the time gate,
    and mark any track whose cells come within TC_SCREEN_KM. Cost scales
    with storm points (thousands), not tracks (hundreds of thousands)."""
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
                    if k not in hit or d < hit[k]["distance_km"]:
                        hit[k] = {"ibtracs_sid": str(pt_sid[j]),
                                  "tc_name": str(pt_name[j]),
                                  "distance_km": round(d, 1),
                                  "matched_time": str(vt[t])}
    return hit


def ar_filter_tracks(mask, latitude, longitude, valid_times,
                     ibtracs_tracks=None):
    """Track-level exclusion (owner 2026-11-09): drop a whole TRACK when any
    cell at any step lies within |lat| < 15, or when its cells come within
    the IBTrACS TC screen radius at any step.

    Returns (keep, dropped, labels, tracks): ``keep`` bool mask of the input
    shape (kept tracks' cells); ``dropped`` the dropped track dicts with
    reason ('tropical' | 'tc'); ``labels`` the shared track-label array
    (track idx + 1 — design's raw layer can label cells by track id);
    ``tracks`` all tracks, each gaining 'status' (+ 'reason' when dropped).
    """
    mask = np.asarray(mask, bool)
    labels, tracks = track_ar_components(mask, valid_times)
    band = np.abs(np.asarray(latitude, float)) < TROPICAL_LAT
    touched = np.zeros(len(tracks), bool)
    for idx in np.unique(labels[:, band, :][mask[:, band, :]]):   # physical px only
        if idx:
            touched[idx - 1] = True

    hit_tc_map = _storm_pass(labels, mask, latitude, longitude, valid_times,
                             ibtracs_tracks)
    hit_tc = [hit_tc_map.get(k) for k in range(len(tracks))]

    dropped_idx = touched | np.fromiter((h is not None for h in hit_tc),
                                        bool, len(tracks))
    # labels are 1-based; index -1 (no AR) reads the leading False; the
    # '& mask' keeps DISPLAY-ONLY bridged cells out of the physical mask
    # (detection/scoring lanes never see them — owner 2026-11-09)
    keep = np.concatenate([[False], ~dropped_idx])[labels] & mask
    dropped = []
    for k, tr in enumerate(tracks):
        if not dropped_idx[k]:
            tr["status"] = "kept"
            continue
        tr["status"] = "dropped"
        if touched[k]:
            tr["reason"] = "tropical"
        else:
            tr["reason"] = "tc"
            tr.update(hit_tc[k] or {})
        tr["start"] = str(np.datetime64(valid_times[tr["steps"][0]], "h"))
        tr["end"] = str(np.datetime64(valid_times[tr["steps"][-1]], "h"))
        tr["label"] = int(k) + 1
        dropped.append(tr)
    logger.info("ar_filter_tracks: %d tracks, %d dropped (%d tropical, %d tc)",
                len(tracks), len(dropped), int(touched.sum()),
                sum(h is not None for h in hit_tc))
    return keep, dropped, labels, tracks
