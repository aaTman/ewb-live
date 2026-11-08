"""IBTrACS TC colocation screen (owner 2026-11-08, G-40): a TC-like fixture
is dropped, a normal mid-latitude AR is kept."""

import numpy as np
import pandas as pd
import pytest

from ewb_live.events.tc_screen import tc_colocation_rule

DATES = np.array(["2026-09-25", "2026-09-26", "2026-09-27"], "datetime64[D]")
LATS = np.linspace(28.0, 52.0, 97)   # 0.25 deg
LONS = np.linspace(-86.0, -60.0, 105)


def _blob_mask(lat_c, lon_c):
    """A diagonal AR band across the region centered on (lat_c, lon_c)."""
    m = np.zeros((len(DATES), len(LATS), len(LONS)), bool)
    la = np.abs(LATS[:, None] - lat_c) + np.abs(LONS[None, :] - lon_c)
    band = la < 6.0
    m[:, band] = True
    return m


def _tracks(points):
    return pd.DataFrame(points, columns=["storm_id", "tc_name", "valid_time",
                                         "latitude", "longitude"])


def test_tc_center_in_component_is_dropped():
    mask = _blob_mask(40.0, -75.0)
    tr = _tracks([("2026268N12345", "OSCAR", "2026-09-26 06:00", 40.5, -75.2)])
    hit = tc_colocation_rule(DATES, LATS, LONS, mask, tr)
    assert hit is not None
    assert hit["ibtracs_sid"] == "2026268N12345"
    assert hit["tc_name"] == "OSCAR"
    assert hit["distance_km"] <= 250.0


def test_far_track_is_kept():
    mask = _blob_mask(40.0, -75.0)
    # same dates, but 1500 km away (Pacific near Baja)
    tr = _tracks([("2026268N99999", "ODALYS", "2026-09-26 06:00", 24.0, -110.0)])
    assert tc_colocation_rule(DATES, LATS, LONS, mask, tr) is None


def test_close_but_wrong_time_is_kept():
    mask = _blob_mask(40.0, -75.0)
    tr = _tracks([("2026268N12345", "OSCAR", "2026-10-06 12:00", 40.1, -75.1)])
    assert tc_colocation_rule(DATES, LATS, LONS, mask, tr) is None


def test_no_tracks_fail_soft():
    mask = _blob_mask(40.0, -75.0)
    assert tc_colocation_rule(DATES, LATS, LONS, mask, None) is None


def test_empty_mask_not_matched():
    tr = _tracks([("2026268N12345", "OSCAR", "2026-09-26 06:00", 40.5, -75.2)])
    empty = np.zeros((len(DATES), len(LATS), len(LONS)), bool)
    assert tc_colocation_rule(DATES, LATS, LONS, empty, tr) is None


def test_loader_maps_and_caches_local_csv(tmp_path, monkeypatch):
    """Loader uses EWB's own column mapping (SID->storm_id, ...) and caches."""
    csv = tmp_path / "ibtracs.csv"
    csv.write_text(
        "SID,ISO_TIME,NAME,LAT,LON,SEASON\n"
        "2026268N1,2026-09-26 06:00:00,OSCAR,40.5,-75.2,2026\n"
        "2025123N1,2025-05-01 06:00:00,ALFRED,-20.1,150.2,2025\n")
    monkeypatch.setenv("EWB_LIVE_IBTRACS_URI", csv.as_uri())
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path / "cache"))
    from ewb_live.events import tc_screen

    tr = tc_screen.load_ibtracs_tracks({2026})
    assert tr is not None and list(tr["storm_id"]) == ["2026268N1"]
    assert (tmp_path / "cache" / "tracks.parquet").exists()
