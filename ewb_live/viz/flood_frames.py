"""Build the flood lane's daily raw-map frames: ``raw_exceedance/flood_<YYYYMMDD>.cls``.

Defect F0 (owner 2026-11-19, notes/audit_flood.md): the merged map shaded
heat/freeze/AR per day but had no flood raster, so the flood tab showed
other lanes' shading and no flood footprint. This publishes the flood lane's
own daily exceedance footprint next to the 6-hourly raw frames, on the SAME
grid contract as the ``.bin`` planes:

  ``flood_<YYYYMMDD>.cls`` = zlib(u8 class grid, DISPLAY orientation: row 0 =
  lat -90 (south), col 0 = lon -180, 0.25 deg, 721 x 1440). Class 0 = no
  evidence; classes 1..5 are the union-rule cells (daily >= ERA5 P95 AND
  >= 20 mm, OR trailing 72 h >= P95 AND >= 50 mm, land only, fully observed
  cells only — exactly ``events/flood.build_scan``'s mask, so map and events
  cannot diverge) binned by the mm/day-EQUIVALENT intensity
  ``max(daily_mm, accum_72h / 3)`` against ``BINS_MM`` edges
  [20, 50, 100, 200, 350]: class = number of edges reached. A cell that
  qualifies only through the 72 h lane can sit below the first edge
  (50 mm/72 h = 16.7 mm/day-equivalent) — it rides class 1, which the legend
  states honestly.

``raw_exceedance/index.json`` gains an additive ``flood`` key (the exceedance
build preserves unknown keys when it rewrites the index; this module merges
the key itself, run AFTER the exceedance step in weekly.py / dev_site.sh):

  {"rev": "v1", "rule": "...", "units": "mm/day-equiv",
   "bins_mm": [20, 50, 100, 200, 350], "days": ["YYYYMMDD", ...],
   "absent": ["YYYY-MM-DD", ...], "runs": {"YYYYMMDD": "late"|"early"|"final"}}

``days`` is what the frontend fetches (no 404 storms); ``absent`` lists days
inside the window with no observed global granule — an absent day draws NO
marks, which is the honest empty state, not a dry day.

Compute is the detector's own join (IMERG store + ERA5 precip climatology),
so it runs on the ewb LXC only::

    python -m ewb_live.viz.flood_frames [--days N] [--force]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import zlib
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

REV = "v1"
#: mm/day-equivalent class edges (class = #edges reached, 1..5)
BINS_MM = [20.0, 50.0, 100.0, 200.0, 350.0]
CLS_EXT = ".cls"
FLOOD_PREFIX = "flood_"
DAY_RE = re.compile(r"^flood_(\d{8})\.cls$")
#: the 72 h lane needs the two previous days to be complete on day one
LANE3_LOOKBACK = 2


def classes_from_lanes(mask: np.ndarray, daily_mm: np.ndarray,
                       accum_72h: np.ndarray) -> np.ndarray:
    """One day's union-mask cells -> u8 classes 0..5 (detector orientation).

    Intensity is the mm/day-equivalent ``max(daily, 72h/3)`` — one physical
    quantity for both lanes, so one ramp can label it. Mask-off cells are
    always 0; masked cells below the first edge (72 h-lane-only) ride 1.
    """
    d = np.nan_to_num(np.asarray(daily_mm, "float64"), nan=0.0)
    a3 = np.nan_to_num(np.asarray(accum_72h, "float64"), nan=0.0) / 3.0
    inten = np.maximum(d, a3)
    cls = np.searchsorted(np.asarray(BINS_MM, "float64"), inten,
                          side="right").astype("uint8")
    m = np.asarray(mask, bool)
    cls[m & (cls == 0)] = 1
    cls[~m] = 0
    return cls


def to_display(cls: np.ndarray) -> np.ndarray:
    """Detector lattice (lat 90..-90 DESC) -> contract display frame
    (row 0 = south), the same flip the raw frames apply to their planes."""
    return np.ascontiguousarray(cls[::-1, :])


def cls_bytes(cls: np.ndarray) -> bytes:
    return zlib.compress(np.asarray(cls, "uint8").tobytes(), 6)


def cls_from_bytes(raw: bytes, shape: tuple[int, int]) -> np.ndarray:
    b = zlib.decompress(raw)
    n = shape[0] * shape[1]
    if len(b) != n:
        raise ValueError(f".cls payload {len(b)} B != {n} B")
    return np.frombuffer(b, "uint8").reshape(shape)


def flood_index_block(days: list[str], absent: list[str],
                      runs: dict[str, str], source: str = "imerg") -> dict:
    """The additive index.json 'flood' key (frontend contract, module
    docstring). ``days``/``runs`` keys use compact YYYYMMDD; ``source`` says
    which rain product the frames were built from (the frontend tip follows
    it — PARDIG is an AI estimate and must not be labelled "observed")."""
    pardig = source == "pardig"
    src_label = ("AI-estimated precipitation (Google PARDIG, CC BY 4.0)"
                 if pardig else "GPM IMERG V07 (observation)")
    return {"rev": REV,
            "source": source,
            "source_label": src_label,
            "tip": (src_label + " over the detector rule: daily >= the ERA5 P95 "
                    "wet-day threshold AND >= 20 mm/day, OR trailing 72 h >= P95 "
                    "AND >= 50 mm (land only). Shaded by the mm/day-equivalent "
                    "accumulation max(daily, 72 h total / 3): 20-50, 50-100, "
                    "100-200, 200-350, >=350. Blank days carry no evidence."),
            "rule": (f"union of daily rain from {'PARDIG-NRT (AI-estimated)' if pardig else 'IMERG'} "
                     ">= ERA5 pooled wet-day P95 AND "
                     ">= 20 mm/day, OR trailing 72 h >= P95 AND >= 50 mm; "
                     "land only, fully observed cells only — the detector's "
                     "own mask (events/flood.py)"),
            "units": "mm/day-equiv",
            "bins_mm": list(BINS_MM),
            "days": sorted(days),
            "absent": sorted(absent),
            "runs": dict(sorted(runs.items()))}


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def merge_index(out: Path, block: dict) -> None:
    """Merge the 'flood' key into raw_exceedance/index.json (atomic rewrite).

    The exceedance build owns the other keys and preserves unknown ones; if
    there is no index yet the key is skipped — the merged map mounts on the
    exceedance index alone, so a flood-only index would serve nothing, and
    the next exceedance run will produce the frames the key refers to.
    """
    p = out / "index.json"
    if not p.exists():
        logger.warning("flood_frames: no raw_exceedance/index.json yet — "
                       "skipping index merge (run the exceedance build first)")
        return
    index = json.loads(p.read_text())
    index["flood"] = block
    tmp = p.with_name(p.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(index, separators=(",", ":")))
    os.replace(tmp, p)


def prune_frames(out: Path, floor: str) -> None:
    """Delete flood_*.cls for days older than the published window start."""
    for f in out.glob(FLOOD_PREFIX + "*" + CLS_EXT):
        m = DAY_RE.match(f.name)
        if m and m.group(1) < floor:
            f.unlink(missing_ok=True)


def build(root: Path, days: int | None = None, force: bool = False) -> Path:
    """Write one .cls per observed day of the published window; returns out.

    Incremental like the exceedance build (existing files skip unless
    --force); the window comes from data/results/meta.json exactly as
    exceedance.build takes it."""
    root = Path(root)
    out = root / "data/results" / "raw_exceedance"
    out.mkdir(parents=True, exist_ok=True)
    meta = json.loads((root / "data/results" / "meta.json").read_text())
    w_start = dt.datetime.fromisoformat(meta["window"]["start"]).date()
    w_end = (dt.datetime.fromisoformat(meta["window"]["end"])
             - dt.timedelta(microseconds=1)).date()
    start = max(w_start, w_end - dt.timedelta(days=days - 1)) if days else w_start

    from ..events import flood   # lazy: pulls the climatology modules

    # the load END is clamped to the last day the STORE serves: the meta
    # window rolls forward daily while the rain source lags (IMERG Late:
    # months; PARDIG-NRT: ~2-8 h), and a beyond-reach tail chunk (zero
    # fields) would abort the whole build. Ground truth is the store's own
    # file inventory — status/index files can list not-yet-arrived days.
    # Beyond-reach days join `absent` — no marks on the map for them, which
    # is the honest truth.
    source = (os.environ.get("EWB_FLOOD_SOURCE") or "pardig").strip().lower()
    if source == "imerg":
        from ..sources import imerg
        store = root / "data" / "imerg"
        daily = imerg.daily_dir(store)
        have = {dt.date.fromisoformat(f[6:10] + "-" + f[10:12] + "-" + f[12:14])
                for f in (p.name for p in daily.glob("imerg_*.nc"))
                if len(f) >= 14 and f[6:14].isdigit()}
    else:
        from ..sources import pardig
        store = root / "data" / "pardig"
        have = pardig.served_days(store)
    end_eff = w_end
    if have:
        ok = [d for d in have if d <= w_end]
        end_eff = min(w_end, max(ok)) if ok else start - dt.timedelta(days=1)
    if end_eff < start:
        logger.warning("flood_frames: %s store serves no day of %s..%s — "
                       "nothing to build", source, start, w_end)
        return out
    obs = flood.load_observations(start - dt.timedelta(days=LANE3_LOOKBACK),
                                  end_eff, root=store, source=source)
    land = flood.land_mask(*obs.grid)
    scan = flood.build_scan(obs, land=land)

    day_tags: list[str] = []
    runs: dict[str, str] = {}
    written = 0
    seen = set()
    for k, d in enumerate(scan.dates):
        if d < start or d > w_end:
            continue
        tag = f"{d:%Y%m%d}"
        if tag in seen:
            continue
        seen.add(tag)
        run = scan.runs.get(f"{d:%Y-%m-%d}", "unknown")
        if not bool(scan.present[k]):
            continue                      # no global granule: honest absence
        p = out / f"{FLOOD_PREFIX}{tag}{CLS_EXT}"
        if not (force or not p.exists()):
            day_tags.append(tag)
            runs[tag] = run
            continue
        cls = classes_from_lanes(scan.mask[k], scan.obs_lane[1][k],
                                 scan.obs_lane[3][k])
        _write_atomic(p, cls_bytes(to_display(cls)))
        day_tags.append(tag)
        runs[tag] = run
        written += 1
    absent = [x for x in scan.source.get("missing_days", [])
              if f"{start:%Y-%m-%d}" <= x <= f"{w_end:%Y-%m-%d}"]
    absent += [f"{d:%Y-%m-%d}" for d in
               (end_eff + dt.timedelta(days=i) for i in range(1, (w_end - end_eff).days + 1))]
    prune_frames(out, f"{w_start:%Y%m%d}")
    merge_index(out, flood_index_block(day_tags, absent, runs, source))
    logger.info("flood_frames: %d days written, %d published, %d absent "
                "(window %s..%s)", written, len(day_tags), len(absent),
                f"{start:%Y-%m-%d}", f"{w_end:%Y-%m-%d}")
    return out


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host
    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.flood_frames")
    ap.add_argument("--root", type=Path,
                    default=Path(__file__).resolve().parents[2])
    ap.add_argument("--days", type=int, default=0,
                    help="only the last N days of the window")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    build(args.root, args.days or None, args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
