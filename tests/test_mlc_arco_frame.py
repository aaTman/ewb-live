"""The midlat_cyclone lane on REAL ERA5 ARCO MSLP (the 0-event blocker).

``tests/test_mlc_track.py`` is all synthetic fields on the repo's ascending
lattice, and it stayed green while the lane published nothing for a 90-day dev
build. The defect lived in the *reader*: ARCO stores MSLP in **Pa** on a
latitude-descending, **longitude 0-360** grid, and ``mlc_era5._to_repo_frame``
shifted the longitudes by +180 **without re-sorting**. That ROTATES the axis
(``0 … 179.75, -180 … -0.25``), so the tracker's ``grid_spacing(lon)`` averaged
across the 359.75 deg jump and derived a 0.4998 deg cell on a 0.25 deg grid:
every closed-contour radius it measured was inflated, ``centre + 3 hPa closes
within 6 deg`` failed for every low, and ``find_peaks_for_time_slice`` returned
0 nodes on the dev frames (24-27 nodes on the same frames sorted). Root cause,
evidence and before/after counts: ``notes/mlc_fix.md`` and
``notes/mlc_diag_ciaran_20231102.txt``.

So these tests run the real thing: a 12° x 13° cutout of ERA5 0.25°
``mean_sea_level_pressure`` around Storm Ciaran, saved from ARCO
(``tests/fixtures/mlc_ciaran_cutout.npz``, 2023-10-31 12Z .. 2023-11-02 12Z,
9 frames; regenerate with ``scripts/mlc/diag_arco_frame.py --save-cutout``).
Ciaran is the check mlc-research pinned in the PROOF: minimum **954.0 hPa** at
2023-11-02 06Z, 50.5 N 1.5 W (the field minimum in the window), landfall in
Finistère inside the 2 Nov 00-06Z step, and a 24 h fall of 1.5x the bomb
threshold. Plus a synthetic field dressed in ARCO's units and axis order, which
pins Pa-vs-hPa and the lattice conventions the tracker's index math needs.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ewb_live.events import mlc as M
from ewb_live.events import mlc_track as T
from ewb_live.events.validate import validate_event
from ewb_live.sources import mlc_era5

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mlc_ciaran_cutout.npz"

CIARAN = {"lat": 50.5, "lon": -1.5, "mslp": 954.0, "t": dt.datetime(2023, 11, 2, 6)}


@pytest.fixture(scope="module")
def cutout():
    """``(mslp hPa (t,lat,lon), lats asc, lons asc in [-180,180), times)``."""
    if not FIXTURE.exists():
        pytest.skip(f"missing real-frame fixture {FIXTURE} (regenerate: "
                    "scripts/mlc/diag_arco_frame.py --time 2023-11-01T12 "
                    "--center 50,-20 --cutout 65 209 24 --save-cutout …)")
    with np.load(FIXTURE) as z:
        mslp = z["mslp_all"].astype(np.float32)
        times = [dt.datetime.fromisoformat(str(t)) for t in z["times"]]
        return (mslp, np.asarray(z["lats"], float), np.asarray(z["lons"], float),
                times)


def to_arco(mslp_hpa: np.ndarray, lats: np.ndarray, lons: np.ndarray,
            times: list[dt.datetime]) -> xr.DataArray:
    """Re-express a repo-frame stack the way ARCO stores it: Pa, latitude
    descending (90 N-first), longitude ascending 0-360."""
    m = np.asarray(mslp_hpa, np.float32) * 100.0            # hPa -> Pa
    la = np.asarray(lats, float)[::-1]                      # 90 N-first
    m = m[:, ::-1, :]
    lo = np.asarray(lons, float) % 360.0                    # -46 -> 314
    order = np.argsort(lo, kind="stable")                   # ascending 0..360
    return xr.DataArray(m[:, :, order],
                        dims=("time", "latitude", "longitude"),
                        coords={"time": np.array(times, dtype="datetime64[ns]"),
                                "latitude": la, "longitude": lo[order]},
                        name=mlc_era5.MSLP_VAR)


# ------------------------------------------------------- the real low, found
def test_real_frame_finds_ciaran_in_the_repo_frame(cutout):
    mslp, lats, lons, times = cutout
    frame = mslp[times.index(CIARAN["t"])]
    nodes = T.find_peaks_for_time_slice(frame, CIARAN["t"], lats, lons)
    assert nodes, "no closed low found on the real Ciaran frame"
    best = min(nodes, key=lambda n: n.mslp)
    assert T.haversine_km(best.lat, best.lon, CIARAN["lat"], CIARAN["lon"]) < 30
    assert best.mslp == pytest.approx(CIARAN["mslp"], abs=0.6)


def test_real_frame_survives_the_arco_axis_order(cutout):
    """THE regression: the same bytes in ARCO's units and axis order must still
    find the low.

    Before the fix the shifted-but-not-sorted longitude axis made
    ``grid_spacing(lon)`` 2x the true step, the measured contour radius 8-51 deg
    instead of 1-4 deg, and this call returned 0 nodes.
    """
    mslp, lats, lons, times = cutout
    frame = mslp[times.index(CIARAN["t"])]
    out = mlc_era5._to_repo_frame(to_arco(mslp, lats, lons, times))
    k = times.index(CIARAN["t"])
    got = np.asarray(out.isel(time=k).values, np.float32)
    np.testing.assert_allclose(got, frame, rtol=1e-3, atol=0.05)

    nodes = T.find_peaks_for_time_slice(got, CIARAN["t"],
                                        np.asarray(out.latitude.values, float),
                                        np.asarray(out.longitude.values, float))
    assert nodes, "ARCO-ordered Ciaran frame yielded 0 nodes (the 0-event blocker)"
    best = min(nodes, key=lambda n: n.mslp)
    assert T.haversine_km(best.lat, best.lon, CIARAN["lat"], CIARAN["lon"]) < 30
    assert best.mslp == pytest.approx(CIARAN["mslp"], abs=0.6)


def test_real_cutout_tracks_ciaran(cutout):
    """Nodes -> one stitched low over the window, on real data."""
    mslp, lats, lons, times = cutout
    res = T.generate_mlc_tracks(mslp, lats, lons, times)
    assert res.n_nodes > 2 * len(times), "too few lows found on real frames"
    hits = [t for t in res.tracks
            if T.haversine_km(t.lat[t.argmin], t.lon[t.argmin],
                              CIARAN["lat"], CIARAN["lon"]) < 60]
    assert len(hits) == 1, f"Ciaran is one low, not {len(hits)}"
    tr = hits[0]
    assert tr.min_mslp == pytest.approx(CIARAN["mslp"], abs=0.6)
    assert tr.times[tr.argmin] == CIARAN["t"]
    assert tr.duration_h >= 48.0 and tr.displacement_km > 1000.0, \
        "0 km would mean bad longitudes"
    assert tr.lat[-1] > tr.lat[0] and tr.lon[-1] > tr.lon[0], "Ciaran moved NE"
    bomb, fall, ratio = tr.bomb_ratio()
    assert bomb and ratio > 1.2, (fall, ratio)      # 32 hPa/24 h = 1.5x threshold


def test_real_frames_publish_ciaran_as_an_event(cutout):
    """End to end on real data with the PRODUCTION screens: Ciaran is an event."""
    mslp, lats, lons, times = cutout
    evs = M.scan_mlc_events(dt.date(2023, 10, 31), dt.date(2023, 11, 2),
                            fields=(times, mslp, lats, lons),
                            orography=(np.zeros((0, 0), np.float32),
                                       np.zeros(0), np.zeros(0)),
                            tropical_check=lambda lat, lon, t: False)
    ciaran = [e for e in evs if e.peak_value and e.peak_value < 960]
    assert ciaran, f"Ciaran missing from {[(e.start, e.peak_value) for e in evs]}"
    ev = ciaran[0]
    assert ev.event_type == "midlat_cyclone"
    assert ev.peak_value == pytest.approx(CIARAN["mslp"], abs=0.6)
    assert ev.details["bomb"] and ev.details["displacement_km"] > 2000
    assert validate_event(ev) == [], validate_event(ev)
    # the honest-zero record: this scan ran, and it is reported as ok
    assert M.lane_status()["status"] == "ok"
    assert M.lane_status()["n_events"] == len(evs)


# ------------------------------------------------------------ reader contract
def _synthetic_arco(low_lat=50.0, low_lon=-20.0, depth=40.0, sigma=2.0,
                    times=("2024-01-10T00", "2024-01-10T06"), nan_last=False):
    """A closed low on a 1 deg global grid, dressed as ARCO: Pa, lat descending
    (90 N-first), lon ascending 0-360. ``nan_last`` reproduces ARCO's habit of
    advertising time steps it has not filled in yet (an all-NaN frame)."""
    la = np.arange(88.0, -89.0, -1.0)                       # 90 N-first, descending
    lo = np.arange(0.0, 360.0, 1.0)                         # ARCO's 0..359
    t = [np.datetime64(x) for x in times]
    d = T.haversine_km(la[:, None], lo[None, :], low_lat, low_lon) / 111.195
    field = (1013.0 - depth * np.exp(-((d / sigma) ** 2) / 2.0)) * 100.0   # Pa
    stack = np.stack([field for _ in t]).astype(np.float32)
    if nan_last:
        stack[-1] = np.nan
    ds = xr.Dataset({mlc_era5.MSLP_VAR: (("time", "latitude", "longitude"), stack)},
                    coords={"time": t, "latitude": la, "longitude": lo})
    return ds


def test_reader_hands_over_hpa_on_ascending_uniform_axes():
    ds = _synthetic_arco()
    times, mslp, lats, lons = mlc_era5.read_mslp_window(
        dt.datetime(2024, 1, 10, 0), dt.datetime(2024, 1, 10, 6), ds=ds)
    assert times == [dt.datetime(2024, 1, 10, 0), dt.datetime(2024, 1, 10, 6)]
    assert mslp.shape == (2, 177, 360)
    assert 900.0 < float(mslp.min()) < 1100.0, "MSLP must reach the tracker in hPa"
    assert np.all(np.diff(lats) > 0), "latitude must come out ascending"
    assert np.all(np.diff(lons) > 0), "longitude must come out ascending"
    assert lons[0] == pytest.approx(-180.0) and lons[-1] == pytest.approx(179.0)
    T.check_grid(lats, lons)              # the reader validates its own contract


def test_reader_units_and_order_find_the_same_low():
    """Units are load-bearing: the tracker's thresholds are hPa, ARCO is Pa."""
    ds = _synthetic_arco()
    times, mslp, lats, lons = mlc_era5.read_mslp_window(
        dt.datetime(2024, 1, 10, 0), dt.datetime(2024, 1, 10, 6), ds=ds)
    nodes = T.find_peaks_for_time_slice(mslp[0], times[0], lats, lons)
    assert nodes, "the synthetic ARCO frame yielded no low"
    best = min(nodes, key=lambda n: n.mslp)
    assert T.haversine_km(best.lat, best.lon, 50.0, -20.0) < 60
    assert best.mslp == pytest.approx(973.0, abs=0.5)      # 1013 - 40 hPa

    # the same field left in Pa: every cell is above the 1013 hPa ceiling, so the
    # lane finds nothing — a units bug is a silent zero, hence this pin
    pa = np.asarray(ds[mlc_era5.MSLP_VAR].isel(time=0).values, np.float32)
    assert T.find_peaks_for_time_slice(
        pa, times[0], np.asarray(ds.latitude.values, float),
        np.asarray(ds.longitude.values, float)) == []


# ----------------------------------------------------------- lattice guards
def test_reader_drops_frames_arco_has_not_filled():
    """ARCO's time index runs ahead of its data (all-NaN frames at the tail).

    Peak detection would ignore them, but the tracker's gap filling picks the
    lowest cell in a hole, so an empty frame could become a NaN central pressure
    in a published track. The reader hands over only what has data.
    """
    ds = _synthetic_arco(times=("2024-01-10T00", "2024-01-10T06", "2024-01-10T12"),
                         nan_last=True)
    times, mslp, lats, lons = mlc_era5.read_mslp_window(
        dt.datetime(2024, 1, 10, 0), dt.datetime(2024, 1, 10, 12), ds=ds)
    assert times == [dt.datetime(2024, 1, 10, 0), dt.datetime(2024, 1, 10, 6)]
    assert mslp.shape[0] == 2 and np.isfinite(mslp).all()

    ds_all_nan = _synthetic_arco(times=("2024-01-10T12", "2024-01-10T18"),
                                 nan_last=True)
    ds_all_nan[mlc_era5.MSLP_VAR].values[0] = np.nan
    t2, m2, _, _ = mlc_era5.read_mslp_window(
        dt.datetime(2024, 1, 10, 12), dt.datetime(2024, 1, 10, 18), ds=ds_all_nan)
    assert t2 == [] and m2.size == 0        # -> the lane reports no-data, not 0 lows


def test_check_grid_rejects_a_rotated_longitude_axis():
    lats = np.arange(-80.0, 80.0 + 1e-9, 0.25)
    lons = np.arange(-180.0, 180.0, 0.25)
    T.check_grid(lats, lons)                                # the contract: fine
    T.check_grid(lats[::-1], lons)                          # ARCO-style N-first: fine

    rotated = np.concatenate([lons[lons >= 0], lons[lons < 0]])   # 0..179.75, -180..
    assert rotated[0] == 0.0 and not np.all(np.diff(rotated) > 0)
    with pytest.raises(ValueError, match="longitude axis must be strictly ascending"):
        T.check_grid(lats, rotated)
    with pytest.raises(ValueError, match="longitude axis must be uniform"):
        T.check_grid(lats, np.array([0.0, 0.25, 0.5, 1.5, 1.75, 2.0]))
    with pytest.raises(ValueError, match="latitude axis must be monotonic"):
        T.check_grid(np.array([-10.0, 0.0, -5.0, 5.0, 10.0, 15.0, 20.0]), lons)


def test_rotated_axis_inflates_the_measured_contour_radius(cutout):
    """Why the rotated axis was fatal, measured on the real low.

    ``np.roll`` is exactly what a +180 longitude shift does to the data columns:
    they keep their physical order, only the LABELS rotate (globally
    ``0 … 179.75, -180 … -0.25``). The contour mapper derives longitudes from the
    column index, so the radius it measures for Ciaran's +3 hPa ring goes from
    ~1 deg (a valid low) past the 6 deg gate (no low at all).
    """
    mslp, lats, lons, times = cutout
    f = mslp[times.index(CIARAN["t"])]
    p = T.Params()
    r = int(np.argmin(np.abs(lats - CIARAN["lat"])))
    c = int(np.argmin(np.abs(lons - CIARAN["lon"])))
    halfwin = int(np.ceil(p.max_gc_distance_contour_degrees / T.grid_spacing(lats))) + 1
    lhw = int(np.ceil(halfwin / max(0.2, float(np.cos(np.radians(lats[r]))))))
    level = float(f[r, c]) + p.delta_hPa

    def radius(field, lat_c, lon_c, axis):
        rings = [k for k in T.find_contours_from_point(field, (lat_c, lon_c), level,
                                                       halfwin, lhw)
                 if len(k) > 4 and np.allclose(k[0], k[-1])]
        assert rings, "no closed +3 hPa ring around the real low"
        return min(T.find_valid_contour_from_point(k, (lat_c, lon_c), lats, axis)
                   for k in rings)

    d_true = radius(f, r, c, lons)
    shift = int(np.searchsorted(lons, 0.0))     # negative longitudes moved to the end
    rotated = np.roll(lons, -shift)
    assert rotated[0] == 0.0 and not np.all(np.diff(rotated) > 0)
    # rolling the labels keeps every column's own data, so the low sits at the
    # column whose label still reads 1.5 W
    d_bad = radius(np.roll(f, -shift, axis=1), r, (c - shift) % lons.size, rotated)
    assert d_true < p.max_gc_distance_contour_degrees, "the low is a valid node"
    assert d_bad > p.max_gc_distance_contour_degrees, (
        f"rotated axis measured {d_bad:.2f} deg vs true {d_true:.2f} deg: the "
        "defect that rejected every low is gone from the mapper")
