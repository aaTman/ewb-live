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
packbits(ar) ); each layer row-major over the mirror grid
(lat 90..-90, 721; lon 0..359.75, 1440), LAT_N*LON_N/8 bytes each.
index.json carries the grid + frame list + cutoff. Incremental: existing
frames skip. Run on the box: python -m ewb_live.viz.raw_exceedance
[--days N] [--force]; also a non-fatal weekly/viz tail.
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
            "grid": {"lat_start": 90.0, "lat_step": -0.25, "lat_n": LAT_N,
                     "lon_start": 0.0, "lon_step": 0.25, "lon_n": LON_N,
                     "lon_convention": "0-360"},
            "layers": ["heat", "freeze", "ar"],
            "encoding": ("zlib( packbits(heat) ++ packbits(freeze) ++ "
                         "packbits(ar) ), each layer row-major 721x1440, "
                         "129780 B per layer"),
            "frames": []}


def frame_bytes(heat: np.ndarray, freeze: np.ndarray, ar: np.ndarray,
                level: int = 6) -> bytes:
    """3 bool layers (same (lat, lon) shape) -> zlib(packbits x3)."""
    blob = (np.packbits(heat.ravel()) .tobytes()
            + np.packbits(freeze.ravel()).tobytes()
            + np.packbits(ar.ravel()).tobytes())
    return zlib.compress(blob, level)


def frame_layers(raw: bytes, shape: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode frame_bytes() output (used by tests; the JS decoder is the port)."""
    n = shape[0] * shape[1]
    stride = -(-n // 8)
    b = zlib.decompress(raw)
    if len(b) != 3 * stride:
        raise ValueError(f"frame payload {len(b)} B != {3 * stride} B")
    out = []
    for i in range(3):
        bits = np.unpackbits(np.frombuffer(b[i * stride:(i + 1) * stride], np.uint8))[:n]
        out.append(bits.reshape(shape).astype(bool))
    return tuple(out)  # type: ignore[return-value]


def _frame_name(t: np.datetime64) -> str:
    return pd.Timestamp(str(t)).strftime("%Y%m%d%H") + ".bin"


def build_frames(out: Path, times: list[np.datetime64], layer_fn, force: bool,
                 frames: list[dict]) -> int:
    """Write missing frames; returns count written. layer_fn(day_times) ->
    (heat, freeze, ar) bool arrays (len(day_times), LAT_N, LON_N)."""
    out.mkdir(parents=True, exist_ok=True)
    done = {f["t"] for f in frames}
    written = 0
    for group in _group_days(times):
        pending = [t for t in group if _frame_name(t)[:-4] not in done or force]
        if not pending:
            continue
        heat, freeze, ar = layer_fn(group)
        for t in pending:
            i = group.index(t)
            raw = frame_bytes(heat[i], freeze[i], ar[i])
            (out / _frame_name(t)).write_bytes(raw)
            entry = {"t": _frame_name(t)[:-4], "f": _frame_name(t), "bytes": len(raw)}
            if force and any(f["t"] == entry["t"] for f in frames):
                frames[:] = [f for f in frames if f["t"] != entry["t"]]
            frames.append(entry)
            done.add(entry["t"])
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
        return heat, freeze, ar_np[pos]
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

    index["frames"].sort(key=lambda f: f["t"])
    index["window"] = {"start": str(times[0]) if times else None,
                       "end": str(times[-1]) if times else None}
    index["cutoff"] = str(mirror_end)
    index["cutoff_note"] = ("frames stop at the ERA5 mirror end (pipeline's "
                            "own ERA5 copy); later dates are not rendered")
    index["updated"] = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    tmp = _index_path(out).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")))
    tmp.rename(_index_path(out))
    logger.info("raw_exceedance: %d frames indexed", len(index["frames"]))
    return out


def main(argv=None) -> int:
    from ewb_live._guard import require_ewb_host
    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.viz.raw_exceedance")
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
