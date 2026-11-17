"""IBTrACS source module (tc lane, 2026-11-16): parse + cache + agency choice.

Offline by design: the CSV is synthesised here in IBTrACS list-CSV shape
(missing values are a single space, the units row is the first data line,
spurious ``*_spur`` tracks exist in the provisional file), and the download is
monkeypatched. Network coverage of the real NCEI file happens on the ewb LXC
with ``python -m ewb_live.sources.ibtracs`` (see the tc DONE entry).
"""

import datetime as dt
import io

import numpy as np
import pandas as pd
import pytest

from ewb_live.sources import ibtracs

HEADER = ["SID", "SEASON", "NUMBER", "BASIN", "NAME", "ISO_TIME", "NATURE",
          "LAT", "LON", "TRACK_TYPE", "USA_WIND", "USA_PRES", "WMO_WIND",
          "WMO_PRES", "BOM_WIND", "BOM_PRES"]

UNITS_ROW = ("SID,Year,Best,,,Time,Best,C,deg,main,kt,hPa,kt,hPa,kt,hPa")


def csv_bytes(rows) -> bytes:
    lines = [",".join(HEADER), UNITS_ROW]
    for r in rows:
        vals = [str(r.get(c, " ")) for c in HEADER]
        lines.append(",".join(f" {v}" if v == "" else v for v in vals))
    return "\n".join(lines).encode()


def row(sid="2026270N28302", i=0, **kw):
    t = dt.datetime(2026, 9, 26) + dt.timedelta(hours=3 * i)
    r = {"SID": sid, "SEASON": "2026", "NUMBER": str(i + 1), "BASIN": "NA",
         "NAME": "HAINE", "ISO_TIME": t.strftime("%Y-%m-%d %H:%M:%S"),
         "NATURE": "TS", "LAT": f"{25.0 + 0.2 * i:.3f}",
         "LON": f"{-60.0 - 0.1 * i:.3f}", "TRACK_TYPE": "main",
         "USA_WIND": "45", "USA_PRES": "990"}
    r.update(kw)
    return r


# ------------------------------------------------------------------ parse --
def test_parse_drops_units_row_and_junk():
    df = ibtracs.parse_csv(csv_bytes([row(i=0), row(i=1)]))
    assert len(df) == 2
    assert list(df["season"]) == [2026, 2026]
    assert df["time"].iloc[0] == pd.Timestamp("2026-09-26 00:00:00")
    # units row must not survive as a fix
    assert "Year" not in set(df["season"].astype(str))


def test_parse_rejects_non_sid_rows():
    df = ibtracs.parse_csv(csv_bytes([row(SID="XXXX"), row(i=1)]))
    assert list(df["sid"]) == ["2026270N28302"]


def test_parse_drops_spur_tracks():
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0), row(i=1), row(i=2, TRACK_TYPE="US-PROVISIONAL_spur")]))
    assert len(df) == 2


def test_parse_missing_values_are_nan():
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0, USA_WIND="", USA_PRES="", WMO_WIND="", WMO_PRES=""),
        row(i=1, USA_WIND="T", USA_PRES="T"),
    ]))
    assert np.isnan(df["wind_kt"]).all()
    assert np.isnan(df["pressure_hpa"]).all()
    assert set(df["wind_source"]) == {""}


def test_agency_cascade_prefers_usa_then_wmo_then_regional():
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0),                                     # USA present
        row(i=1, USA_WIND="", USA_PRES="", WMO_WIND="60", WMO_PRES="975"),
        row(i=2, USA_WIND="", USA_PRES="", WMO_WIND="", WMO_PRES="",
            BOM_WIND="55", BOM_PRES="985"),
    ]))
    assert list(df["wind_kt"]) == [45.0, 60.0, 55.0]
    assert list(df["wind_source"]) == ["USA", "WMO", "BOM"]
    assert list(df["pressure_source"]) == ["USA", "WMO", "BOM"]


def test_pressure_follows_the_wind_agency_when_it_can():
    # WMO wind is only used when USA wind is missing; its pressure then comes
    # from WMO too, never a USA pressure paired with a WMO wind
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0, USA_WIND="", USA_PRES="1000", WMO_WIND="50", WMO_PRES="995")]))
    assert df["wind_source"].iloc[0] == "WMO"
    assert df["pressure_source"].iloc[0] == "WMO"
    assert df["pressure_hpa"].iloc[0] == 995.0


def test_longitudes_normalised_to_180():
    df = ibtracs.parse_csv(csv_bytes([row(i=0, LON="200.5")]))
    assert df["lon"].iloc[0] == pytest.approx(-159.5)


def test_nature_blank_becomes_nr():
    df = ibtracs.parse_csv(csv_bytes([row(i=0, NATURE="")]))
    assert df["nature"].iloc[0] == "NR"


# ------------------------------------------------------------------ cache --
def test_cache_path_is_tagged_by_source(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    monkeypatch.setenv("EWB_LIVE_IBTRACS_URI",
                       "https://x/ibtracs.last3years.list.v04r01.csv")
    assert ibtracs.cache_path().name == "fixes_ibtracs.last3years.list.v04r01.parquet"


def test_fetch_fail_soft_to_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    df = ibtracs.parse_csv(csv_bytes([row(i=0), row(i=1)]))
    p = ibtracs.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)

    def boom(*a, **k):
        raise OSError("NOAA down")

    monkeypatch.setattr(ibtracs.urllib.request, "urlopen", boom)
    assert ibtracs.fetch(force=True) == p          # stale cache wins
    out = ibtracs.load_tracks(dt.date(2026, 9, 1), dt.date(2026, 12, 1))
    assert len(out) == 2


def test_fetch_without_cache_reports_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path / "none"))

    def boom(*a, **k):
        raise OSError("NOAA down")

    monkeypatch.setattr(ibtracs.urllib.request, "urlopen", boom)
    assert ibtracs.fetch() is None
    assert ibtracs.load_tracks(dt.date(2026, 9, 1), dt.date(2026, 12, 1)) is None


def test_fetch_writes_parquet_and_loads_window(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    raw = csv_bytes([row(i=0), row(i=1),
                     row(i=2, ISO_TIME="2025-01-01 00:00:00", SEASON="2025")])

    class Resp(io.BytesIO):
        def read(self_):
            return raw

    monkeypatch.setattr(ibtracs.urllib.request, "urlopen",
                        lambda *a, **k: Resp(raw))
    assert ibtracs.fetch(force=True) is not None
    df = ibtracs.load_tracks(dt.date(2026, 9, 20), dt.date(2026, 9, 30))
    assert len(df) == 2                    # the 2025 fix is outside the window
    assert list(df.columns) == ibtracs.COLUMNS


def test_load_tracks_covers_southern_hemisphere_season_edges(tmp_path, monkeypatch):
    """SEASON 2027 spans Nov 2026 - May 2027: a Nov-2026 window must see it."""
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0, SEASON="2027", ISO_TIME="2026-11-16 00:00:00")]))
    p = ibtracs.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    out = ibtracs.load_tracks(dt.date(2026, 11, 1), dt.date(2026, 11, 17))
    assert len(out) == 1


def test_basin_name_known_and_unknown():
    assert ibtracs.basin_name("WP") == "Northwest Pacific"
    assert ibtracs.basin_name("") == "unassigned basin"
    assert ibtracs.basin_name("ZZ") == "ZZ"


# ------------------------------------------------------- source_status ----
# (honest zero, 2026-11-17: an empty TC lane must be explainable by the
# archive's own reach — meta.json quotes these fields; the UI prints them.)
def _cache_with(tmp_path, monkeypatch, df):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    p = ibtracs.cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def test_source_status_reports_the_archive_reach(tmp_path, monkeypatch):
    df = ibtracs.parse_csv(csv_bytes([
        row(i=0, ISO_TIME="2026-11-11 18:00:00"),
        row(i=1, ISO_TIME="2026-11-12 12:00:00")]))
    _cache_with(tmp_path, monkeypatch, df)
    st = ibtracs.source_status(as_of=dt.datetime(2026, 11, 17, 12,
                                                 tzinfo=dt.timezone.utc))
    assert st is not None
    assert st["latest_fix"].startswith("2026-11-12T12:00")
    assert st["lag_days"] == 5.0          # 2026-11-12 12:00 -> 2026-11-17 12:00
    assert "lags" in st["note"]


def test_source_status_none_without_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path / "none"))
    assert ibtracs.source_status() is None


def test_source_status_none_on_a_torn_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("EWB_LIVE_IBTRACS_CACHE", str(tmp_path))
    ibtracs.cache_path().write_bytes(b"not a parquet")
    assert ibtracs.source_status() is None


def test_source_status_never_downloads(tmp_path, monkeypatch):
    """The freshness probe is read-only: a cache hit must not touch urllib."""
    df = ibtracs.parse_csv(csv_bytes([row(i=0)]))
    _cache_with(tmp_path, monkeypatch, df)

    def boom(*a, **k):
        raise AssertionError("source_status must not download")

    monkeypatch.setattr(ibtracs.urllib.request, "urlopen", boom)
    assert ibtracs.source_status() is not None


def test_atlantic_basin_is_derived_from_the_genesis_fix():
    """v04r01 ships BASIN EMPTY for the whole North Atlantic. Without a
    fallback a third of the season prints "unassigned basin" in the UI; the
    label comes from the storm's first fix, the position IBTrACS itself keys
    BASIN on."""
    assert ibtracs.basin_for(14.5, -45.0) == "NA"      # Cape Verde -> Caribbean
    assert ibtracs.basin_for(25.0, -99.0) == "NA"      # Gulf of Mexico edge
    assert ibtracs.basin_for(15.0, -120.0) == "EP"     # off Mexico -> E Pacific
    assert ibtracs.basin_for(18.0, 40.0) == ""         # not ours to guess
    assert ibtracs.basin_for(-25.0, 130.0) == ""       # southern: leave unknown
    assert ibtracs.basin_for(float("nan"), -40.0) == ""

    df = pd.DataFrame({"sid": ["A", "A", "B", "B"], "basin": ["", "", "WP", ""],
                       "lat": [12.0, 20.0, 20.0, -20.0], "lon": [-42.0, -30.0,
                                                                 140.0, 100.0]})
    out = ibtracs.fill_basin(df)
    assert out["basin"].tolist() == ["NA", "NA", "WP", ""]
    assert df["basin"].tolist() == ["", "", "WP", ""], "must not mutate the input"
