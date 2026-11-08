"""Build data/results/raw_exceedance/ — raw ERA5 detector output, 6-hourly.

Second global map (brief notes/briefs/raw_exceedance_map.md): the per-frame
exceedance masks BEFORE the AND/3-day/area/curation filters, no event boxes.
Layers per 6-hourly frame: heat (2t > OWB p85), freeze (2t < OWB p15) — the
exact frame-level operators of upstream build_exceedance_mask minus only the
daily min-over-4-steps pass, land-only as the detector is; AR = EWB's own
build_atmospheric_river_mask_and_land_intersection (IVT>=400 & Laplacian),
per 6-hourly step. Source: the local ERA5 mirror (2t + u/v/q on exactly the
levels AR detection uses — no ARCO fetch needed); frames end at the mirror
end, which is what the pipeline's own ERA5 copy covers (index `cutoff`).

Frame file <YYYYMMDDHH>.bin = zlib( packbits(heat) ++ packbits(freeze) ++
packbits(ar) ); each layer row-major over DISPLAY orientation: lat -90..90
(row 0 = south), lon -180..179.75 (col 0 = west) — mirror-native (N→S,
0-360) is flipped/rolled at load. index.json (frontend contract, channel
13:40/14:05): {schema, generated, grid{res_deg,ny,nx,lat0,lon0,step_lat,
step_lon}, frames:["YYYYMMDDHH",... asc], cutoff:"YYYYMMDDHH",
window:{start,end}}. Incremental: existing frames skip. Run on the box:
python -m ewb_live.viz.exceedance [--days N] [--force]; also the weekly tail.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SCHEMA = 1
OUT_DIRNAME = "raw_exceedance"
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
                level: int = 6) -> bytes:
    """3 bool layers (same (lat, lon) shape) -> zlib(packbits x3).

    Per-ROW packing (axis=1): matches the JS decoder's ceil(nx/8) row
    stride for any nx, not just nx % 8 == 0."""
    blob = (np.packbits(heat, axis=1).tobytes()
            + np.packbits(freeze, axis=1).tobytes()
            + np.packbits(ar, axis=1).tobytes())
    return zlib.compress(blob, level)


def frame_layers(raw: bytes, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode frame_bytes() output (used by tests; the JS decoder is the port)."""
    ny, nx = shape
    rs = -(-nx // 8)
    plane = ny * rs
    b = zlib.decompress(raw)
    if len(b) != 3 * plane:
        raise ValueError(f"frame payload {len(b)} B != {3 * plane} B")
    out = []
    for i in range(3):
        bits = np.unpackbits(np.frombuffer(b[i * plane:(i + 1) * plane], np.uint8))
        out.append(bits.reshape(ny, rs * 8)[:, :nx].astype(bool))
    return tuple(out)  # type: ignore[return-value]


def _frame_name(t: np.datetime64) -> str:
    return pd.Timestamp(str(t)).strftime("%Y%m%d%H") + ".bin"


def _to_display(a: np.ndarray) -> np.ndarray:
    """Mirror-native (lat 90→-90, lon 0→360) to contract display frame:
    row 0 = south, col 0 = lon -180. Pure index ops, applied per layer."""
    return np.roll(a, -(LON_N // 2), axis=-1)[..., ::-1, :]


def build_frames(out: Path, times: list[np.datetime64], layer_fn, force: bool,
                 frames: list[str]) -> int:
    """Write missing frames; returns count written. layer_fn(day_times) ->
    (heat, freeze, ar) bool arrays (len(day_times), LAT_N, LON_N)."""
    out.mkdir(parents=True, exist_ok=True)
    done = set(frames)
    written = 0
    for group in _group_days(times):
        pending = [t for t in group if _frame_name(t)[:-4] not in done or force]
        if not pending:
            continue
        try:
            heat, freeze, ar = layer_fn(group)
        except Exception:  # noqa: BLE001 — one bad day (e.g. AR mask dropping
            # a valid_time step) must not skip every later day or the index
            logger.exception("raw_exceedance: day %s failed — skipping day",
                             _group_label(group))
            continue
        for t in pending:
            i = group.index(t)
            raw = frame_bytes(heat[i], freeze[i], ar[i])
            (out / _frame_name(t)).write_bytes(raw)
            tag = _frame_name(t)[:-4]
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


def _make_layer_fn(mirror, clim_hi, clim_lo, land):
    """layer_fn for one day of 6-hourly mirror steps."""
    def layer_fn(day_times):
        import dask
        from extremeweatherbench.events import atmospheric_river as ar_ev
        sl = slice(str(np.datetime64(day_times[0])), str(np.datetime64(day_times[-1])))
        day = mirror.sel(time=sl)
        t2m = day["2m_temperature"]
        heat = np.zeros((len(day_times), LAT_N, LON_N), bool)
        freeze = np.zeros_like(heat)
        for i, t in enumerate(day_times):
            tt = pd.Timestamp(t)
            doy = min(tt.dayofyear, 366)
            hi = clim_hi.sel(dayofyear=doy, hour=tt.hour,
                             latitude=t2m.latitude, longitude=t2m.longitude).to_numpy()
            lo = clim_lo.sel(dayofyear=doy, hour=tt.hour,
                             latitude=t2m.latitude, longitude=t2m.longitude).to_numpy()
            v = t2m.isel(time=i).to_numpy()
            heat[i] = (v > hi) & land
            freeze[i] = (v < lo) & land
        ds = (day[["u_component_of_wind", "v_component_of_wind",
                   "specific_humidity"]]
              .rename({"u_component_of_wind": "eastward_wind",
                       "v_component_of_wind": "northward_wind",
                       "specific_humidity": "specific_humidity",
                       "time": "valid_time"})
              .chunk({"valid_time": 1, "level": -1, "latitude": -1,
                      "longitude": -1}))
        with dask.config.set(scheduler="threads", num_workers=6):
            m = ar_ev.build_atmospheric_river_mask_and_land_intersection(
                ds, output_variables=["atmospheric_river_mask"])["atmospheric_river_mask"]
            ar_np = np.asarray(m > 0, bool)
            mt = np.asarray(m.valid_time)
        pos = [int(np.where(mt == t)[0][0]) for t in day_times]
        return _to_display(heat), _to_display(freeze), _to_display(ar_np[pos])
    return layer_fn


def build(root: Path, days: int | None = None, force: bool = False) -> Path:
    """Incremental build over the meta window, capped at the mirror end."""
    root = Path(root)
    out = root / "data/results" / OUT_DIRNAME
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
    if force:
        index["frames"] = []

    land = temperature_events().build_land_mask(
        xr.DataArray(mirror.longitude, dims="longitude"),
        xr.DataArray(mirror.latitude, dims="latitude")).to_numpy()
    clim_hi = owb_clim.get_climatology_owb(0.85).sortby("latitude")
    clim_lo = owb_clim.get_climatology_owb(0.15).sortby("latitude")
    layer_fn = _make_layer_fn(mirror, clim_hi, clim_lo, land)

    times = [np.datetime64(t, "h") for t in mirror.time.values
             if w_start <= pd.Timestamp(t).date() <= end
             and pd.Timestamp(t).hour in (0, 6, 12, 18)]
    if days:
        times = [t for t in times
                 if t >= np.datetime64(str(start)) ]
    logger.info("raw_exceedance: %d candidate frames %s -> %s (mirror end %s)",
                len(times), times[0] if times else "-",
                times[-1] if times else "-", mirror_end)
    build_frames(out, times, layer_fn, force, index["frames"])

    index["frames"].sort()

    def fmt(t) -> str:
        return str(np.datetime64(t, "h")).replace("-", "").replace(
            "T", "")[:10]

    # retention: frames outside the published window would desync the raw
    # slider from index["window"] and accumulate forever (ponytail 17:17Z)
    if times:
        floor = fmt(times[0])
        for t in [f for f in index["frames"] if f < floor]:
            (out / f"{t}.bin").unlink(missing_ok=True)
            index["frames"].remove(t)

    index["window"] = {"start": fmt(times[0]) if times else None,
                       "end": fmt(times[-1]) if times else None}
    # owner 2026-11-08: the raw map stays the RAW upstream masks; label the
    # AR layer so consumers know the event list additionally applies the
    # IBTrACS tropical-cyclone screen (ewb_live/events/tc_screen.py)
    index["layers"] = {
        "h": "heat exceedance (> P85 climatology)",
        "f": "freeze exceedance (< P15 climatology)",
        "a": ("AR mask — raw upstream EWB detector output, before the "
              "IBTrACS tropical-cyclone screen applied to the event list"),
    }
    index["cutoff"] = fmt(mirror_end)
    index["generated"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    tmp = _index_path(out).with_suffix(".json.tmp")
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
