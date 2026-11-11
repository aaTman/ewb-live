"""Build data/results/raw_exceedance/ — raw ERA5 detector output, 6-hourly.

Second global map (brief notes/briefs/raw_exceedance_map.md): the per-frame
exceedance masks BEFORE the AND/3-day/area/curation filters, no event boxes.
Layers per 6-hourly frame: heat (2t > OWB p85), freeze (2t < OWB p15) — the
exact frame-level operators of upstream build_exceedance_mask minus only the
daily min-over-4-steps pass, land-only as the detector is; AR = EWB's own
IVT>=400 & Laplacian detector computed seam-periodically
(ewb_live/events/ar_mask.py — upstream's non-wrapping stencil and component
floor cut ARs along the prime meridian, which is the array edge of a 0..360
ERA5 grid), per 6-hourly step. Source: the local ERA5 mirror (2t + u/v/q on
exactly the levels AR detection uses — no ARCO fetch needed); frames end at
the mirror end, which is what the pipeline's own ERA5 copy covers (index
`cutoff`).

Frame file <YYYYMMDDHH>.bin = zlib( packbits(heat) ++ packbits(freeze) ++
packbits(ar) ++ packbits(bridged) ); each layer row-major over DISPLAY
orientation: lat -90..90 (row 0 = south), lon -180..179.75 (col 0 = west) —
mirror-native (N→S, 0-360) is flipped/rolled at load. Plane 2 `ar` is what
the AR lane PAINTS (physical cells plus a gap-bridged footprint); plane 3
`bridged` flags the cells that are only that carried-over footprint, so the
frontend can render them faintly instead of passing them off as physical.
Sidecar <YYYYMMDDHH>.ivt = zlib(u8 IVT magnitude classes, DISPLAY orientation):
low 3 bits = bin (0 = <250, 1 = 250-500, 2 = 500-750, 3 = 750-1000,
4 = 1000-1250, 5 = >=1250 kg m-1 s-1), bit 7 = IVT >= 400 (the AR detector's
threshold). Computed from the IVT FIELD — never masked by the AR paint, which
is what made IVT values vanish whenever a track was screened (schema 2).
Sidecar <YYYYMMDDHH>.run = zlib( u8 heat_run
++ u8 freeze_run ) per consecutive-day duration (dur rev v2, contract
notes/channel_consecutive_days.md §4): low 7 bits = CONSECUTIVE COMPLETED UTC
days the cell met the daily rule (all four 00/06/12/18Z steps exceeded),
constant across all four frames of a UTC day (no provisional +1, no midnight
reset — sawtooth fix, diurnal investigation 2026-11-09); bit 7 = today so far
(in-progress day, all steps known to date exceeded) — render as provisional
texture on an already-qualifying cell, never as a painted point of its own.
Counts saturate at 127. Seeded from the earliest mirror step. v1 sidecars
(pre-v2) hold the old provisional per-step run; JS keys off index dur.rev.
Qualification gate (fixed the 2026-11-10 "points flash on at 00Z" report): a
cell paints only once its COMPLETED streak reaches the lane minimum — heat
MIN_CONSECUTIVE_DAYS (3, read from upstream), freeze 1 — so a cell a few hours
short of that minimum is blank at every hour of the qualifying day instead of
appearing only in the 00Z frame and vanishing at 06Z. Published as dur.min
(see PAINT_MIN). The .run encoding itself is unchanged.
index.json (frontend contract, channel 13:40/14:05):
{schema, generated, grid{res_deg,ny,nx,lat0,lon0,step_lat,step_lon},
frames:["YYYYMMDDHH",... asc], cutoff:"YYYYMMDDHH", window:{start,end},
dur:{rev,seed,min{heat,freeze}}}. Incremental: existing frames skip (a frame is done only with
its .run/.trk sidecars on disk). Run on the box:
python -m ewb_live.viz.exceedance [--days N] [--force]; also the weekly tail.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SCHEMA = 3  # v3: adds AR IVT class plane sidecars .ivt (2026-11-09)
OUT_DIRNAME = "raw_exceedance"
LAYER_REV = "v9"  # bump when mask/screen rules change (v9 = `.ivt` published
# from the IVT FIELD (schema 2: class in the low 3 bits, bit 7 = IVT >= 400)
# instead of being masked by the AR paint, which is what made "IVT values just
# disappear" whenever a track was screened (owner report 2026-11-10,
# notes/ar_outage_20261110.md §3); the TC screen is step-level now too
# (events/ar_filter). v8 = seam-periodic AR
# mask (events/ar_mask) + the 4th `.bin` plane `bridged`: the gap-bridge
# footprint reaches the screen as a flag now, not as an indistinguishable
# physical cell. v7 = .ivt classified in
# DISPLAY orientation; v6 sidecars were native-mixed garbage — ponytail 23:59Z.
# v6 = owner gap-bridge
# 2026-11-09: tracks persist through 1-step (6 h) gaps; the gap frame shows
# the last footprint with index tracks[sid].bridged listing the gap tags —
# render those cells lighter so the display stays honest. v5 = forced
# rebuild: v4 frames written during the 03:51Z durbackfill collision predate
# the effective screen — 51 shipped tracks sit inside the 500 km TC buffer)
LAT_N, LON_N = 721, 1440  # mirror grid; tests monkeypatch these


def _mirror(root: Path):
    import xarray as xr
    return xr.open_zarr(root / "data/cache/truth/era5.zarr", consolidated=False)


def _index_path(out: Path) -> Path:
    return out / "index.json"


def _load_index(out: Path) -> dict:
    p = _index_path(out)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except ValueError:
            pass
    return {"schema": SCHEMA,
            "grid": {"res_deg": 0.25, "ny": LAT_N, "nx": LON_N,
                     "lat0": -90.0, "lon0": -180.0,
                     "step_lat": 0.25, "step_lon": 0.25},
            "frames": []}


def frame_bytes(heat: np.ndarray, freeze: np.ndarray, ar: np.ndarray,
                bridged: np.ndarray | None = None) -> bytes:
    """4 bool layers (same (lat, lon) shape) -> zlib(packbits x4).

    Plane 2 is the AR paint mask (physical cells + gap-bridge footprint);
    plane 3 `bridged` marks WHICH of those cells are only the carried-over
    footprint, so the frontend can paint them faintly (SPEC "AR tropical
    exclusion": the bridge is a display flag, not a physical cell). Pass
    bridged=None for an empty plane: the payload is ALWAYS 4 planes, so the
    offsets of planes 0-2 never move for an older reader.

    Per-ROW packing (axis=1): matches the JS decoder's ceil(nx/8) row
    stride for any nx, not just nx % 8 == 0."""
    if bridged is None:
        bridged = np.zeros(np.shape(ar), bool)
    blob = (np.packbits(heat, axis=1).tobytes()
            + np.packbits(freeze, axis=1).tobytes()
            + np.packbits(ar, axis=1).tobytes()
            + np.packbits(np.asarray(bridged, bool), axis=1).tobytes())
    return zlib.compress(blob, 6)


def frame_layers(raw: bytes, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Decode frame_bytes() output (used by tests; the JS decoder is the port)."""
    ny, nx = shape
    rs = -(-nx // 8)
    plane = ny * rs
    b = zlib.decompress(raw)
    if len(b) != 4 * plane:
        raise ValueError(f"frame payload {len(b)} B != {4 * plane} B")
    out = []
    for i in range(4):
        bits = np.unpackbits(np.frombuffer(b[i * plane:(i + 1) * plane], np.uint8))
        out.append(bits.reshape(ny, rs * 8)[:, :nx].astype(bool))
    return tuple(out)  # type: ignore[return-value]


def _frame_name(t: np.datetime64) -> str:
    return pd.Timestamp(str(t)).strftime("%Y%m%d%H") + ".bin"


RUN_NAME = ".run"   # <frame>.run sidecar: zlib(u8 heat_run ++ u8 freeze_run)
DUR_REV = "v2"      # duration semantics version (index.json "dur.rev")
FREEZE_PAINT_MIN = 1  # freeze paint gate (owner 2026-11-10): day 1, not the
# detector's 3-day floor — cold runs are shorter and rarer; see dur_block().
# The heat gate is the detector constant itself, never a second literal.
RUN_CAP = 127       # completed-day count saturates here; bit 7 = provisional
IVT_CLASS_NAME = ".ivt"  # <frame>.ivt sidecar: zlib(u8 AR IVT class plane)


def dur_block(seed_tag: str | None) -> dict:
    """index.json "dur" block (frontend contract, dur rev v2).

    `min` is the painted qualification gate per lane:
      * heat = the detector's minimum consecutive-day run, read from upstream
        temperature_events().MIN_CONSECUTIVE_DAYS (3) so the gate can never
        drift from the detector and the map never shades a cell that can't be
        part of a heat wave;
      * freeze = 1 (owner 2026-11-10): short cold runs paint from their first
        COMPLETED day. Freeze events still require the detector minimum —
        detection is untouched — but cold runs decay twice as fast as hot ones
        (17.6% vs 31.6% reach 3+ days, notes/channel_diurnal.md round 4), so a
        3-day paint floor would leave the lane nearly blank (~220 cells vs
        ~3,085 on 2026-11-01) and the 1-2 day ramp shades unusable.
    See the module docstring for the paint rule."""
    from ewb_live.events._upstream import temperature_events
    return {"rev": DUR_REV, "seed": seed_tag,
            "min": {"heat": int(temperature_events().MIN_CONSECUTIVE_DAYS),
                    "freeze": FREEZE_PAINT_MIN}}


def _run_day(day: list[np.ndarray], c: np.ndarray
             ) -> tuple[list[np.ndarray], np.ndarray]:
    """One day of v2 run grids + the next day's C grid.

    value(step j of day D) = min(C(D-1),127) | 0x80 if steps 0..j of D all
    exceeded else min(C(D-1),127); C(D) = min(C(D-1)+1,127) if ALL steps of D
    exceeded else 0. The solid count is the COMPLETED-day streak: constant
    across all four frames of a UTC day — no provisional +1 at 00Z, no
    midnight reset (diurnal sawtooth fix 2026-11-09). Today rides only in the
    provisional bit and lands in the solid count with day D+1's frames.
    """
    acc = day[0].copy()               # running AND of steps 0..j
    base = np.minimum(c, RUN_CAP).astype(np.uint8)
    grids = []
    for step in day:
        acc &= step
        grids.append(np.where(acc, base | np.uint8(0x80), base).astype(np.uint8))
    return grids, np.where(acc, np.minimum(base + 1, RUN_CAP), 0).astype(np.uint8)


def run_recurrence(days: list[list[np.ndarray]],
                   seed: np.ndarray | None = None) -> list[np.ndarray]:
    """Consecutive-day run recurrence (contract §2, dur rev v2) over day
    groups of per-step bool masks in chronological order (up to 4 steps per
    group); see _run_day for the value semantics. seed = the C grid of the day
    before days[0] (pre-window mirror history, §3); None = zeros (run
    truncates at mirror start). Returns one u8 grid per step.
    """
    c = (np.zeros(days[0][0].shape, np.uint8) if seed is None
         else np.minimum(np.asarray(seed, np.uint8) & 0x7F, RUN_CAP))
    out: list[np.ndarray] = []
    for day in days:
        grids, c = _run_day(day, c)
        out.extend(grids)
    return out                       # one u8 grid per step (v2 encoded)


def run_bytes(heat_run: np.ndarray, freeze_run: np.ndarray) -> bytes:
    """Two (ny, nx) u8 run grids (display orientation) -> zlib bytes."""
    return zlib.compress(np.asarray(heat_run, np.uint8).tobytes()
                         + np.asarray(freeze_run, np.uint8).tobytes(), 6)


def run_from_bytes(raw: bytes, shape: tuple[int, int]
                   ) -> tuple[np.ndarray, np.ndarray]:
    """Decode run_bytes() output (used by tests; the JS decoder is the port)."""
    b = zlib.decompress(raw)
    n = shape[0] * shape[1]
    if len(b) != 2 * n:
        raise ValueError(f".run payload {len(b)} B != {2 * n} B")
    a = np.frombuffer(b, np.uint8)
    return a[:n].reshape(shape), a[n:].reshape(shape)


IVT_CLASS_EDGES = [250, 500, 750, 1000, 1250]   # kg m-1 s-1, class 1..5
IVT_AR_MIN = 400.0    # the AR detector's own IVT threshold
IVT_ABOVE_BIT = 128   # plane bit 7: IVT >= IVT_AR_MIN (ivt schema 2)
IVT_CLASS_MASK = 7    # low 3 bits: the IVT class (0 = below the first edge)


def ivt_class_plane(ivt: np.ndarray) -> np.ndarray:
    """IVT magnitude class plane (u8) from the IVT FIELD itself.

    Bins (kg m-1 s-1), low 3 bits:
    - 0: below 250
    - 1: 250-500
    - 2: 500-750
    - 3: 750-1000
    - 4: 1000-1250
    - 5: >=1250
    plus bit 7 (`IVT_ABOVE_BIT`) wherever IVT >= `IVT_AR_MIN` (400) — the
    detector's own threshold, so the frontend can keep the moisture-flux field
    visible without calling those cells ARs.

    It is deliberately NOT gated on the AR mask (owner report 2026-11-10, "IVT
    values just disappear", north Atlantic 08-12 18Z -> 08-13 00Z). The plane
    used to be `class(ivt, ar_paint)` — zero wherever the AR layer does not
    paint — and the dashboard colours AND hovers from it alone, so the published
    `.ivt` non-zero mask was cell-identical to the `.bin` AR plane
    (notes/ar_outage_20261110.md §3). Every track screen and every marginal
    `|blurred v2 IVT| >= 2.5` flicker therefore deleted the IVT field too: at
    08-13 00Z the basin held 5 515 cells at IVT >= 400 while 85 were painted.
    What the screens decide is what COUNTS as an AR (`.bin` plane 2); the field
    itself is never unpublished.

    Args:
        ivt: (ny, nx) float array of IVT magnitude

    Returns:
        (ny, nx) u8 array: class in the low 3 bits, `IVT_ABOVE_BIT` on cells at
        or above the AR IVT threshold.
    """
    ok = np.isfinite(ivt)
    v = np.where(ok, ivt, 0.0)
    classes = np.zeros(ivt.shape, dtype=np.uint8)

    # Classify the IVT field into bins (NaN cells and cells below the first
    # edge stay 0; nothing here consults a mask)
    classes[v >= IVT_CLASS_EDGES[4]] = 5
    classes[(v >= IVT_CLASS_EDGES[3]) & (v < IVT_CLASS_EDGES[4])] = 4
    classes[(v >= IVT_CLASS_EDGES[2]) & (v < IVT_CLASS_EDGES[3])] = 3
    classes[(v >= IVT_CLASS_EDGES[1]) & (v < IVT_CLASS_EDGES[2])] = 2
    classes[(v >= IVT_CLASS_EDGES[0]) & (v < IVT_CLASS_EDGES[1])] = 1
    classes[~ok] = 0
    classes[(v >= IVT_AR_MIN) & ok] |= IVT_ABOVE_BIT
    return classes


def ivt_class_bytes(classes: np.ndarray) -> bytes:
    """(ny, nx) u8 class grid (display orientation) -> zlib bytes."""
    return zlib.compress(np.asarray(classes, np.uint8).tobytes(), 6)


def ivt_class_from_bytes(raw: bytes, shape: tuple[int, int]) -> np.ndarray:
    """Decode ivt_class_bytes() output."""
    b = zlib.decompress(raw)
    n = shape[0] * shape[1]
    if len(b) != n:
        raise ValueError(f".ivt payload {len(b)} B != {n} B")
    return np.frombuffer(b, np.uint8).reshape(shape)


def _to_display(a: np.ndarray) -> np.ndarray:
    """Mirror-native (lat 90→-90, lon 0→360) to contract display frame:
    row 0 = south, col 0 = lon -180. Pure index ops, applied per layer."""
    return np.roll(a, -(LON_N // 2), axis=-1)[..., ::-1, :]


def _frame_complete(out: Path, tag: str, done: set[str]) -> bool:
    """A frame is done only if indexed AND all sidecars are on disk —
    cached .bin-only frames get an idempotent sidecar backfill (contract §4).
    Shortcut: assumes the lane's layer_fn emits .trk and .ivt sidecars (v3)."""
    return (tag in done and (out / f"{tag}{RUN_NAME}").exists()
            and (out / f"{tag}{AR_LABELS_NAME}").exists()
            and (out / f"{tag}{IVT_CLASS_NAME}").exists())


def _seed_for(run_seed: dict | None, t0: np.datetime64):
    """C grids of the day before t0's day; None = run truncates (§3)."""
    if not run_seed:
        return None, None
    return run_seed.get(str(np.datetime64(t0, "D") - np.timedelta64(1, "D")),
                        (None, None))


def _write_atomic(path: Path, data: bytes) -> None:
    """Published frames are read concurrently (serve.py per request,
    qualification.py per event): in-place write_bytes can expose a torn
    zlib payload on rewrite (ponytail 06:16Z) — same tmp+replace
    discipline as ar_tracks.json/index.json in this file."""
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def build_frames(out: Path, times: list[np.datetime64], layer_fn, force: bool,
                 frames: list[str], run_seed: dict | None = None) -> int:
    """Write missing frames; returns count written. layer_fn(day_times) ->
    (heat, freeze, ar[, labels[, ivt[, bridged]]]) arrays (len(day_times),
    LAT_N, LON_N): heat/freeze/ar are bool, labels is uint16 track ids, ivt is
    float IVT values, bridged is the bool AR gap-bridge plane; display
    orientation. run_seed: {day: (c_heat, c_freeze)} C grids from
    _daily_runs, seeding the .run recurrence (§2/§3)."""
    out.mkdir(parents=True, exist_ok=True)
    done = set(frames)
    written = 0
    for group in _group_days(times):
        pending = [t for t in group
                   if force or not _frame_complete(out, _frame_name(t)[:-4],
                                                   done)]
        if not pending:
            continue
        try:
            res = layer_fn(group)
        except Exception:  # noqa: BLE001 — one bad day (e.g. AR mask dropping
            # a valid_time step) must not skip every later day or the index
            logger.exception("raw_exceedance: day %s failed — skipping day",
                             _group_label(group))
            continue
        labels4 = res[3] if len(res) > 3 else None
        ivt_day = res[4] if len(res) > 4 else None
        bridged4 = res[5] if len(res) > 5 else None
        heat, freeze, ar = res[0], res[1], res[2]
        seed_h, seed_f = _seed_for(run_seed, group[0])
        h_runs = run_recurrence([list(heat)], seed_h)
        f_runs = run_recurrence([list(freeze)], seed_f)
        for t in pending:
            i = group.index(t)
            raw = frame_bytes(heat[i], freeze[i], ar[i],
                              bridged4[i] if bridged4 is not None else None)
            _write_atomic(out / _frame_name(t), raw)
            tag = _frame_name(t)[:-4]
            if labels4 is not None:  # per-frame track labels (owner 2026-11-09)
                _write_atomic(out / f"{tag}{AR_LABELS_NAME}",
                              labels_bytes(labels4[i]))
            _write_atomic(out / f"{tag}{RUN_NAME}",
                          run_bytes(h_runs[i], f_runs[i]))
            # AR IVT class plane sidecar (v3 schema) — the gap-bridge
            # footprint is excluded: it never met the IVT rule, so it must
            # not carry a magnitude class (the screen paints it faintly from
            # plane 3, and hover reads this same plane)
            # AR IVT class plane sidecar (v3 schema) — computed from the IVT
            # field, NOT from the paint: see ivt_class_plane's docstring for the
            # owner report that proves why (the gap-bridge footprint carries no
            # class of its own because it never met the IVT rule)
            if ivt_day is not None and len(ivt_day) > i:
                class_plane = ivt_class_plane(ivt_day[i])   # display-oriented
                _write_atomic(out / f"{tag}{IVT_CLASS_NAME}",
                              ivt_class_bytes(class_plane))
            if tag in done:
                frames.remove(tag)
            frames.append(tag)
            done.add(tag)
            written += 1
        logger.info("raw_exceedance: wrote %s (%d frames total)",
                    _group_label(group), len(frames))
    return written


def _group_days(times: list[np.datetime64]) -> list[list[np.datetime64]]:
    groups: dict[str, list[np.datetime64]] = {}
    for t in times:
        d = str(np.datetime64(t, "D"))
        groups.setdefault(d, []).append(t)
    return list(groups.values())


def _group_label(group) -> str:
    return str(np.datetime64(group[0], "D"))


def _step_heat_freeze(t2m, clim_hi, clim_lo, land, n: int):
    """Per-step heat/freeze masks (mirror-native) for t2m's first n steps —
    the ONE definition of the frame-level rule, shared by the frame layer_fn
    and the daily-mask pass so the two can never drift."""
    heat = np.zeros((n, LAT_N, LON_N), bool)
    freeze = np.zeros_like(heat)
    for i in range(n):
        tt = pd.Timestamp(str(t2m.time.values[i]))
        doy = min(tt.dayofyear, 366)
        hi = clim_hi.sel(dayofyear=doy, hour=tt.hour,
                         latitude=t2m.latitude, longitude=t2m.longitude).to_numpy()
        lo = clim_lo.sel(dayofyear=doy, hour=tt.hour,
                         latitude=t2m.latitude, longitude=t2m.longitude).to_numpy()
        v = t2m.isel(time=i).to_numpy()
        heat[i] = (v > hi) & land
        freeze[i] = (v < lo) & land
    return heat, freeze


def _make_layer_fn(mirror, clim_hi, clim_lo, land, keep6, labels6, times6,
                   remap, ivt6, ghost6=None):
    """layer_fn for one day of 6-hourly mirror steps. AR layer + track
    labels come from the ONE window-level tracked pass (events/ar_filter) —
    no per-frame object decisions, so an AR cannot blink between frames
    (owner 2026-11-09). ivt6 is the full window IVT for AR class plane.
    ghost6 (optional) is the gap-bridge footprint alone — kept separate from
    keep6 (physical | ghost, what the AR lane paints) so the frame can carry
    the flag and the frontend can paint those cells faintly."""
    pos_of = {str(np.datetime64(t, "h")): i for i, t in enumerate(times6)}

    def layer_fn(day_times):
        # LABEL-select, never positional: the mirror can hold off-6h steps
        # (ERA5 preliminary data is 3-hourly); a slice+isel would shift
        # steps 2..4 and pair climatology with the wrong hour — silently
        # wrong pixels (ponytail 10:16Z). day_times ⊂ mirror times.
        t2m = mirror.sel(time=[str(np.datetime64(t))
                               for t in day_times])["2m_temperature"]
        heat, freeze = _step_heat_freeze(t2m, clim_hi, clim_lo, land,
                                         len(day_times))
        pos = [pos_of[str(np.datetime64(t, "h"))] for t in day_times]
        keep = keep6[pos]
        lab = np.where(keep, remap[labels6[pos]], np.uint16(0))
        ivt_day = ivt6[pos] if len(ivt6) > 0 else np.zeros((len(day_times),) + keep[0].shape, float)
        ghost = (_to_display(ghost6[pos]) if ghost6 is not None
                 else np.zeros_like(_to_display(keep)))
        return (_to_display(heat), _to_display(freeze), _to_display(keep),
                _to_display(lab),
                np.stack([_to_display(v) for v in ivt_day]),  # contract: ALL outputs display-oriented
                ghost)
    return layer_fn


def _daily_runs(mirror, clim_hi, clim_lo, land,
                times_all: list[np.datetime64]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """§2/§3: daily-mask recurrence over the ENTIRE mirror history (heat/
    freeze only, no AR — minutes of CPU, re-run every build, no persisted
    state). Returns {day: (c_heat, c_freeze)}: the C grid after each UTC day,
    display orientation, u8. build_frames seeds day D from key D-1, so runs
    are exact back to the earliest mirror step (longer runs truncate)."""
    runs: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    ch = cf = None
    for group in _group_days(times_all):
        t2m = mirror.sel(time=[str(np.datetime64(t))
                               for t in group])["2m_temperature"]
        heat, freeze = _step_heat_freeze(t2m, clim_hi, clim_lo, land,
                                         len(group))
        # day-1 seed: no mirror history before the first day -> C = 0
        # (_run_day has no None-seed branch; deploy-pi fix 2026-11-10 of the
        # 29609e9 latent crash — the unit tests never exercise _daily_runs)
        _, ch = _run_day(list(heat),
                         ch if ch is not None
                         else np.zeros(heat.shape[1:], np.uint8))
        _, cf = _run_day(list(freeze),
                         cf if cf is not None
                         else np.zeros(freeze.shape[1:], np.uint8))
        runs[str(np.datetime64(group[0], "D"))] = (_to_display(ch),
                                                   _to_display(cf))
    return runs


AR_LABELS_NAME = ".trk"       # <frame>.trk sidecar: zlib(uint16 labels)
TRACK_ID_MAX = 65535          # u2 labels; 0 = no AR cell


def labels_bytes(labels: np.ndarray) -> bytes:
    """(ny, nx) uint16 track labels -> zlib bytes (0 = no AR)."""
    lab = np.asarray(labels)
    assert lab.dtype == np.uint16 and lab.max(initial=0) <= TRACK_ID_MAX
    return zlib.compress(lab.tobytes(), 6)


def labels_from_bytes(raw: bytes, shape: tuple[int, int]) -> np.ndarray:
    return np.frombuffer(zlib.decompress(raw), dtype="<u2").reshape(shape)


def flickering_tracks(index: dict, presence: dict[int, set[str]],
                      min_span: int = 12) -> dict[int, list[str]]:
    """Track ids absent from an interior step between two appearances —
    the owner's Japan blink bug. Long-lived = first..last spans >= min_span
    steps. presence: track id -> frame tags; index order = time order."""
    order = {tag: i for i, tag in enumerate(index["frames"])}
    out: dict[int, list[str]] = {}
    for tid, tags in presence.items():
        idx = sorted(order[t] for t in tags if t in order)
        if len(idx) < 2 or idx[-1] - idx[0] < min_span:
            continue
        for a, b in zip(idx, idx[1:]):
            if b - a > 1:                          # interior dropout
                out.setdefault(tid, []).extend(index["frames"][a + 1:b])
    return out


def read_presence(out: Path, frames: list[str],
                  shape: tuple[int, int]) -> dict[int, set[str]]:
    """track id -> frame tags, from the <frame>.trk sidecars (empty when v1)."""
    presence: dict[int, set[str]] = {}
    for tag in frames:
        p = out / f"{tag}{AR_LABELS_NAME}"
        if not p.exists():
            continue
        lab = labels_from_bytes(p.read_bytes(), shape)
        for tid in np.unique(lab):
            if tid:
                presence.setdefault(int(tid), set()).add(tag)
    return presence


def _empty_pass():
    """Zero-length AR pass: empty window (:361 guard) or every day's AR
    path failed in the loop (ponytail 08:16Z NICE — same opaque
    concatenate ValueError the input guard names).

    7-tuple, same shape as _ar_window_pass's: (keep, labels, times, remap,
    ivt, bridged-dict, ghost)."""
    return (np.zeros((0, LAT_N, LON_N), bool),
            np.zeros((0, LAT_N, LON_N), np.int32),
            np.array([], "datetime64[h]"),
            np.zeros(1, np.uint16),
            np.zeros((0, LAT_N, LON_N), float),
            {},
            np.zeros((0, LAT_N, LON_N), bool))


def _ar_window_pass(mirror, times, ledger_path: Path):
    """ONE AR mask + tracking pass over the full frame window. Returns
    (keep6, labels6, times6, remap, ivt6, bridged, ghost6). Exclusion happens
    at TRACK level via the shared events/ar_filter fn — same function
    detection uses, so map and events cannot diverge (owner 2026-11-09). The
    mask itself comes from events/ar_mask (EWB detector, seam-periodic).
    keep6 here is the DISPLAY mask (physical | gap-bridged footprint — the
    physical/detection keep stays inside ar_filter); ghost6 is that bridged
    footprint alone, so the frame can carry it as a flag. remap maps global
    track label -> u2 sidecar id (kept tracks only); bridged maps sidecar id
    -> gap frame tags. The ledger json carries {track_id: window/reason} for
    design's before/after reports."""
    import dask
    from ewb_live.events import ar_mask
    from ewb_live.events.ar_filter import ar_filter_tracks
    from ewb_live.events.tc_screen import TC_SCREEN_ON, load_ibtracs_tracks

    if not times:   # stale/empty mirror with the window rolled forward:
        # np.concatenate([]) would die with an opaque ValueError and the
        # weekly lane never writes index.json (ponytail 06:16Z). Empty
        # pass => build_frames writes nothing; downstream guards handle it.
        return _empty_pass()
    ms, ts, ivt_arr = [], [], []
    with dask.config.set(scheduler="threads", num_workers=6):
        for group in _group_days(times):
            try:    # one bad day (AR mask dropping a valid_time step) must
                # skip that day, not kill the whole raw map (build_frames
                # tolerates missing frames via layer_fn pos_of)
                sl = slice(str(np.datetime64(group[0])), str(np.datetime64(group[-1])))
                day = mirror.sel(time=sl)
                ds = (day[["u_component_of_wind", "v_component_of_wind",
                           "specific_humidity"]]
                      .rename({"u_component_of_wind": "eastward_wind",
                               "v_component_of_wind": "northward_wind",
                               "time": "valid_time"})
                      .chunk({"valid_time": 1, "level": -1, "latitude": -1,
                              "longitude": -1}))
                result = ar_mask.build_atmospheric_river_mask_and_land_intersection(
                    ds, output_variables=["atmospheric_river_mask", "integrated_vapor_transport"])
                m = result["atmospheric_river_mask"]
                ivt_day = result["integrated_vapor_transport"]
                ms.append(np.asarray(m > 0, bool))
                ivt_arr.append(np.asarray(ivt_day, float))
                ts.append(np.asarray(m.valid_time, "datetime64[h]"))
            except Exception:  # noqa: BLE001
                logger.exception("raw_exceedance: AR window pass failed for "
                                 "day %s — skipping day", _group_label(group))
    if not ms:   # every day's AR path failed (ponytail 08:16Z NICE)
        return _empty_pass()
    mask6 = np.concatenate(ms)
    times6 = np.concatenate(ts)
    ivt6 = np.concatenate(ivt_arr) if ivt_arr else np.zeros_like(mask6, float)
    years = {int(np.datetime64(t, "Y").astype("int64")) + 1970 for t in times6}
    years |= {y + 1 for y in years} | {y - 1 for y in years}  # year-boundary gate
    tc = load_ibtracs_tracks(years) if TC_SCREEN_ON else None
    keep6, dropped, labels6, tracks = ar_filter_tracks(
        mask6, np.asarray(mirror.latitude, float),
        np.asarray(mirror.longitude, float), times6, tc)
    drop_labels = {d["label"] for d in dropped}
    remap = np.zeros(len(tracks) + 1, np.uint16)
    track_map: dict[str, str] = {}
    n = 0
    for k, tr in enumerate(tracks, start=1):
        if k in drop_labels:
            continue
        n += 1
        if n > TRACK_ID_MAX:
            raise RuntimeError(f"AR tracks in window exceed u2 sidecar cap "
                               f"({TRACK_ID_MAX}); widen the .trk format")
        remap[k] = n
        track_map[str(n)] = tr["id"]
    # GAP BRIDGE display (owner 2026-11-09): bridged cells live in labels6
    # (ar_filter) at gap steps but NOT in the physical mask — fold them into
    # the returned keep for the raw layer only, and expose them per track
    # so the frontend can paint them lighter ('bridged' honesty flag).
    surv = remap.astype(bool)
    ghost6 = surv[labels6] & ~mask6
    keep6 = keep6 | ghost6
    bridged: dict[str, list[str]] = {}
    for k, tr in enumerate(tracks, start=1):
        if remap[k] and tr.get("bridged"):
            bridged[str(int(remap[k]))] = [
                str(np.datetime64(times6[s], "h")).replace("-", "")
                                     .replace("T", "")[:10]
                for s in tr["bridged"]]
    # STEP-LEVEL TC SCREEN audit (owner 2026-11-10): a kept track that lost
    # cells to a cyclone records WHICH steps, so a hole in the AR layer is
    # answerable from the ledger instead of a detector re-run
    tc_masked: dict[str, dict] = {}
    for k, tr in enumerate(tracks, start=1):
        if remap[k] and tr.get("tc_steps"):
            tc_masked[str(int(remap[k]))] = {"steps": tr["tc_steps"],
                                             "tc": tr.get("tc_name")}
    tmp = ledger_path.with_name(ledger_path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps({
        "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "layer_rev": LAYER_REV,
        "tracks": track_map,
        "dropped": dropped,
        "bridged": bridged,
        "tc_masked": tc_masked,
    }, separators=(",", ":")))
    os.replace(tmp, ledger_path)   # torn ar_tracks.json kills ar_diff.py
    return keep6, labels6, times6, remap, ivt6, bridged, ghost6


def prune_frames(out: Path, frames: list[str], floor: str) -> None:
    """Drop frames older than floor (YYYYMMDDHH) from the keep-list and
    delete their .bin/.trk/.run/.ivt files (published-window retention)."""
    for t in [f for f in frames if f < floor]:
        for ext in (".bin", AR_LABELS_NAME, RUN_NAME, IVT_CLASS_NAME):
            (out / f"{t}{ext}").unlink(missing_ok=True)
        frames.remove(t)


def label_sig(remap) -> str:
    """Fingerprint of the sid numbering (numpy remap array; json would
    not serialize a bare ndarray — ponytail 04:15Z MUST)."""
    return hashlib.md5(json.dumps(
        np.asarray(remap).tolist(), sort_keys=True).encode()).hexdigest()[:12]


def build(root: Path, days: int | None = None, force: bool = False) -> Path:
    """Incremental build over the meta window, capped at the mirror end."""
    root = Path(root)
    out = root / "data/results" / OUT_DIRNAME
    from ewb_live.pipeline import _sweep_orphan_temps
    _sweep_orphan_temps(out)   # index.json.tmp.<pid> orphans from killed runs
    import xarray as xr
    from ewb_live.climatology import owb_clim
    from ewb_live.events._upstream import temperature_events

    meta = json.loads((root / "data/results" / "meta.json").read_text())
    w_start = dt.datetime.fromisoformat(meta["window"]["start"]).date()
    w_end = (dt.datetime.fromisoformat(meta["window"]["end"])
             - dt.timedelta(microseconds=1)).date()

    mirror = _mirror(root)
    mirror_end = np.datetime64(str(mirror.time.values[-1]), "h")
    cutoff_day = pd.Timestamp(mirror.time.values[-1]).date()
    end = min(w_end, cutoff_day)
    if days:
        start = max(w_start, end - dt.timedelta(days=days - 1))
    else:
        start = w_start

    index = _load_index(out)
    index.setdefault("frames", [])
    # snapshot BEFORE the wipes: a rev-bump or --force rebuild that hits
    # the all-AR-fail path must keep serving the last good index, not take
    # the "fresh index" branch and publish blank (ponytail 10:16Z)
    had_frames = bool(index["frames"])
    # layer-rule version: frames are cached by name only, so a mask/screen
    # rule change must wipe the done-set (bump on AR/heat rule changes;
    # v2 = SPEC "AR tropical exclusion" 2026-11-08)
    if index.get("layer_rev") != LAYER_REV:
        logger.info("raw_exceedance: layer_rev %s -> %s, full rebuild",
                    index.get("layer_rev"), LAYER_REV)
        index["frames"] = []
        index["layer_rev"] = LAYER_REV
    if force:
        index["frames"] = []

    land = temperature_events().build_land_mask(
        xr.DataArray(mirror.longitude, dims="longitude"),
        xr.DataArray(mirror.latitude, dims="latitude")).to_numpy()
    clim_hi = owb_clim.get_climatology_owb(0.85).sortby("latitude")
    clim_lo = owb_clim.get_climatology_owb(0.15).sortby("latitude")

    times = [np.datetime64(t, "h") for t in mirror.time.values
             if w_start <= pd.Timestamp(t).date() <= end
             and pd.Timestamp(t).hour in (0, 6, 12, 18)]
    # --days caps ONLY what build_frames writes. Capping `times` also caps
    # _ar_window_pass: numbering differs from the weekly build, label_sig
    # wipes index["frames"] and the published window shrinks for a week
    # (ponytail 08:16Z SHOULD). layer_fn is unaffected (pos_of spans
    # ar_times over the full window).
    build_times = ([t for t in times if t >= np.datetime64(str(start))]
                   if days else times)

    def fmt(t) -> str:
        return str(np.datetime64(t, "h")).replace("-", "").replace(
            "T", "")[:10]

    logger.info("raw_exceedance: %d candidate frames %s -> %s (mirror end %s)",
                len(times), times[0] if times else "-",
                times[-1] if times else "-", mirror_end)
    # ONE tracked AR pass for the whole window (owner 2026-11-09): track-level
    # exclusion, per-frame labels, ledger for design. Frames cached by name
    # only, so LAYER_REV bumped to v3 (tracking) forces the full rebuild.
    keep6, labels6, ar_times, remap, ivt6, bridged, ghost6 = _ar_window_pass(
        mirror, times, out / "ar_tracks.json")
    if not len(ar_times) and had_frames:
        # AR pass saw nothing (stale mirror or every day failed): the
        # degenerate remap's sig wipes index["frames"], layer_fn KeyErrors
        # on every day, and a BLANK index would publish for up to a week
        # while all .bin/.trk/.run data sits fine on disk (ponytail
        # 09:15Z). Keep the last good published index untouched. A fresh
        # v1 index (no frames) still proceeds and builds.
        logger.warning("raw_exceedance: empty AR pass with %d published "
                       "frames — keeping last good index", len(index["frames"]))
        return out
    layer_fn = _make_layer_fn(mirror, clim_hi, clim_lo, land, keep6, labels6,
                              ar_times, remap, ivt6, ghost6)
    # owner 2026-11-09: track-level filtering must not blink a long-lived AR
    # across consecutive frames (off-Japan bug) — gate every build, BEFORE
    # any write AND before the expensive recurrence (ponytail 12:16Z: the
    # gate needs only the AR-pass outputs). Limit: a day whose AR pass
    # FAILED this run but succeeded last leaves its cached sidecar pixels
    # unseen here — exceptional path, and the gate fails closed at the build.
    presence: dict[int, set] = {}
    if times:
        # a frame not in ar_times can ONLY ever fire this gate falsely:
        # presence iterates ar_times, so its pixels are unseen — unseen
        # must not be fatal (ponytail 08:16Z MUST: a transient AR day
        # failure froze the whole lane). Gate only frames the pass saw.
        # Physical blinks re-link as NEW ids in ar_filter, invisible here
        # by design — sr13_flicker's overlap chains are the real detector
        # (post-rebuild box gate, like d2).
        planned = sorted({fmt(t) for t in ar_times})
        for i, t in enumerate(ar_times):
            tag = fmt(t)
            for sid in np.unique(remap[labels6[i]]).tolist():
                if sid:
                    presence.setdefault(int(sid), set()).add(tag)
        flick = flickering_tracks({"frames": planned}, presence)
        if flick:
            raise RuntimeError(f"raw_exceedance: AR flicker in {len(flick)} "
                               f"tracks: {flick}")
    # §3: daily-mask recurrence. u8 runs saturate at 255 d, so history older
    # than window-start - 255 d cannot affect published frames — the mirror
    # is append-only, so an untrimmed recurrence grows forever (ponytail
    # 04:15Z). dur.seed reports the (trimmed) first day it actually used.
    t_lo = np.datetime64(str(w_start)) - np.timedelta64(255, "D")
    times_all = [np.datetime64(t, "h") for t in mirror.time.values
                 if pd.Timestamp(t).hour in (0, 6, 12, 18)
                 and np.datetime64(t, "h") >= t_lo]
    run_seed = _daily_runs(mirror, clim_hi, clim_lo, land, times_all)
    # the recurrence loop needs the full seed span, but build_frames only
    # ever reads [w_start-1d, end] — drop the rest (up to ~0.7 GB of C
    # grids held across the whole frame-writing phase, ponytail 06:16Z)
    seed_floor = str(np.datetime64(str(w_start)) - np.timedelta64(1, "D"))
    run_seed = {k: v for k, v in run_seed.items() if k >= seed_floor}
    # label ids are remapped per build (creation order among KEPT tracks)
    # while frames cache by name — any renumbering mixes two numberings in
    # read_presence (ghost flicker / wrong tooltip ids / pixels of later-
    # dropped tracks). Fingerprint the numbering itself, not the window
    # start: mid-week same-base re-runs can shift keep sets too
    # (ponytail 02:16Z + 03:24Z). _ar_window_pass recomputes the whole
    # window either way, so a wipe only costs frame rewrites.
    sig = label_sig(remap)
    if index.get("label_sig") != sig:
        logger.info("raw_exceedance: track numbering changed (%s -> %s), "
                    "full rebuild", index.get("label_sig"), sig)
        index["frames"] = []
    index["label_sig"] = sig
    build_frames(out, build_times, layer_fn, force, index["frames"], run_seed)

    index["frames"].sort()
    # retention: frames outside the published window would desync the raw
    # slider from index["window"] and accumulate forever (ponytail 17:17Z).
    # FULL-window builds only: a capped --days backfill must not delete the
    # ~83 published days it did not rebuild (ponytail 07:19Z data loss).
    if times and not days:
        prune_frames(out, index["frames"], fmt(times[0]))

    # window = what actually exists on disk, not what `times` planned:
    # stops window.end claiming a frame whose day-pass failed un-written,
    # and keeps a --days run from shrinking the published window (:566)
    index["window"] = {"start": index["frames"][0] if index["frames"] else None,
                       "end": index["frames"][-1] if index["frames"] else None}
    # track spans for the hover tooltip (frontend contract, 00:32Z post);
    # restricted to surviving frames; absent for v1 (no sidecars)
    live = set(index["frames"])
    if presence:
        index["schema"] = SCHEMA
        index["tracks"] = {str(tid): dict(
            {"t0": min(tags & live), "t1": max(tags & live)},
            **({"bridged": [t for t in bridged.get(str(tid), []) if t in live]}
               if bridged.get(str(tid)) else {}))
            for tid, tags in presence.items() if tags & live}
    else:
        index.pop("tracks", None)   # stale ids in no .trk (ponytail 05:17Z)
    # owner 2026-11-08: raw AR layer is now FILTERED like the events —
    # shared screen ewb_live/events/ar_filter.py drops objects touching
    # |lat|<15 or IBTrACS-co-located (see channel_design_ewb 22:55Z)
    index["layers"] = {
        "h": "heat exceedance (> P85 climatology)",
        "f": "freeze exceedance (< P15 climatology)",
        "a": ("AR mask: EWB detection minus tropical objects (centroid inside "
              "15 deg of the equator for a full day) and tropical cyclones "
              "(step-level); the IVT magnitude field itself is published "
              "separately in the .ivt sidecar and is never screened"),
        # 4th `.bin` plane, not a paintable lane: flags the AR cells that are
        # only a gap-bridged footprint, which the frontend paints faintly
        "b": ("AR gap-bridge footprint (display only — the track persisted "
              "through a 1-step mask dropout; not a physical AR cell)"),
    }
    index["cutoff"] = fmt(mirror_end)
    # feature-detect keys are published ONLY when the sidecars actually
    # exist on disk (ponytail 00:15Z): a key without bytes makes every
    # consumer fetch 720 404s per window and defeats the silent fallback.
    # One check on the newest frame suffices — sidecar writes share the
    # frame loop, so a missing newest sidecar means the lane does not
    # produce the feature. pop() clears a stale key from an older build.
    head = index["frames"][-1] if index["frames"] else None
    # duration feature-detect (contract §4): consumer paints runs only when
    # this key exists; seed = earliest mirror step the recurrence saw
    if head and (out / f"{head}{RUN_NAME}").exists():
        index["dur"] = dur_block(fmt(times_all[0]) if times_all else None)
    else:
        index.pop("dur", None)
    # AR IVT class plane feature-detect (v3): consumer paints ramp only when
    # this key exists; bins: 250-500, 500-750, 750-1000, 1000-1250, >=1250 kg m-1 s-1
    if head and (out / f"{head}{IVT_CLASS_NAME}").exists():
        index["ivt"] = {"schema": 2, "bins": IVT_CLASS_EDGES,
                        "ar_min": IVT_AR_MIN, "above_bit": IVT_ABOVE_BIT}
    else:
        index.pop("ivt", None)
    index["generated"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    tmp = _index_path(out).with_name(  # sweep matches *.tmp.*
        _index_path(out).name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(index, separators=(",", ":")))
    tmp.rename(_index_path(out))
    logger.info("raw_exceedance: %d frames indexed", len(index["frames"]))
    return out


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host
    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.exceedance")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[2])
    ap.add_argument("--days", type=int, default=0,
                    help="only the last N days (incremental backfill cap)")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build(args.root, args.days or None, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
