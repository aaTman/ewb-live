"""IBTrACS best-track source: download, cache, parse (tc lane, 2026-11-16).

Data source (owner brief): NOAA NCEI *International Best Track Archive for
Climate Stewardship*, v04r01. Two products are read, chosen by the window
(audit TC-1, 2026-11-19): ``ibtracs.last3years.list.v04r01.csv`` (11.9 MB,
rolling last 3 years) whenever the window starts inside its reach, else the
full ``ibtracs.ALL.list.v04r01.csv`` (333 MB, every season — EWB's own
``extremeweatherbench.inputs.IBTRACS_URI``). The two carry the same
``Last-Modified`` publication stamp and are byte-for-byte equivalent for any
window the rolling product covers (measured on the live files: identical
fix/storm sets for the 90-day dev window), so the small file is strictly
preferred and the 333 MB pull only happens for genuinely old windows.
Override with ``EWB_LIVE_IBTRACS_URI`` to pin one product regardless of
window.

The full CSV is ~200 MB / 1.7 M rows / 174 columns. We keep only the columns
this lane needs and cache them as parquet under ``data/cache/ibtracs/`` (the
``sources`` cache convention; ``EWB_LIVE_IBTRACS_CACHE`` relocates the dir,
same env var the AR screen uses — our file name is distinct so the two lanes
never fight over one artifact). Refresh at most once per
``EWB_LIVE_IBTRACS_MAX_AGE_H`` (24 h); a failed fetch falls back to the stale
cache and, with no cache at all, returns ``None`` so detection degrades to
"no tropical cyclones this run" instead of dying on a NOAA blip.

Agency selection (documented choice — IBTrACS explicitly warns against mixing
agency values within one storm):
  * wind: ``USA_WIND`` first. It is the only globally complete, per-fix wind
    in v04r01 (1-minute sustained, kt; the JTWC-informed best estimate) and
    EWB's own ``IBTrACS_metadata_variable_mapping`` treats ``USA_WIND`` as the
    primary wind. Missing → ``WMO_WIND`` (the official RSMC value, 10-kt
    rounded, absent for whole basins), then the regional agencies in a fixed
    priority order (TOKYO, CMA, KMA, HKO, NEWDELHI, REUNION, BOM, NADI,
    WELLINGTON, DS824, MLC, NEUMANN).
  * pressure: the *same* agency as the selected wind when it carries a
    pressure, else USA_PRES, else WMO_PRES, else the first available.
  * every fix records ``wind_source``/``pressure_source`` ("USA", "WMO", …) and
    each event publishes the modal source in ``details`` — an event whose wind
    came from a regional agency is auditable.

Missing values: IBTrACS uses a single space ``" "`` for missing, ``"T"`` for a
technical miss and ships a *units row* as the first data line (SEASON ==
``"Year"``). Both are handled: the units row is dropped, numeric parsing is
``to_numeric(errors="coerce")``.

Fix cadence: v04r01 is 3-hourly for most agencies since 2015 and 6-hourly for
the Tokyo/DeWC61-only tracks. Nothing here assumes a step — the cadence is
measured per storm (``median_step_h``) by the consumer.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

NCEI_BASE = ("https://www.ncei.noaa.gov/data/international-best-track-archive"
             "-for-climate-stewardship-ibtracs/v04r01/access/csv")
DEFAULT_URI = f"{NCEI_BASE}/ibtracs.ALL.list.v04r01.csv"
# rolling last-3-years product: same publication cadence and content for any
# window it covers, 28x smaller (audit TC-1). Chosen automatically by uri_for().
LAST3_URI = f"{NCEI_BASE}/ibtracs.last3years.list.v04r01.csv"
# the rolling product's own reach, with a ~60-day safety margin against the
# publisher's window bookkeeping: a window starting older than this must use
# the ALL file or fixes would silently be missing from the cache.
LAST3_REACH = dt.timedelta(days=1095 + 60)
# per-fix wind/pressure agency priority (see module docstring)
WIND_AGENCIES = ("USA", "WMO", "TOKYO", "CMA", "KMA", "HKO", "NEWDELHI",
                 "REUNION", "BOM", "NADI", "WELLINGTON", "DS824", "MLC",
                 "NEUMANN")
# Averaging period of each agency's wind (IBTrACS v04r01 documentation): USA
# (and JTWC-informed) values are 1-MINUTE sustained, NEWDELHI is IMD 3-minute,
# every other listed agency (WMO/TOKYO/CMA/KMA/HKO/REUNION/BOM/NADI/
# WELLINGTON/DS824/MLC/NEUMANN) is 10-minute. The Saffir-Simpson bands are a
# 1-minute scale, so ewb_live/events/tc.py enforces ONE basis per storm with
# this map instead of applying the bands to a per-fix mishmash (audit TC-2).
WIND_BASIS = {"USA": "1-min", "JTWC": "1-min", "NEWDELHI": "3-min"}
BASIS_DEFAULT = "10-min"


def wind_basis_of(source: str) -> str:
    """Averaging period of a wind value carried by the named agency."""
    return WIND_BASIS.get((source or "").strip().upper(), BASIS_DEFAULT)
BASIN_NAMES = {
    "NA": "North Atlantic", "SA": "South Atlantic", "EP": "Northeast Pacific",
    "CP": "North Central Pacific", "WP": "Northwest Pacific",
    "NI": "North Indian Ocean", "SI": "Southwest Indian Ocean",
    "SP": "South Pacific", "SP ": "South Pacific", "AS": "Arabian Sea",
    "BP": "Bay of Bengal", "AN": "Australia region", "SH": "Southern Hemisphere",
    "?": "unassigned basin",
}
# standardised fix table (what load_tracks returns)
COLUMNS = ["sid", "season", "number", "basin", "name", "time", "lat", "lon",
           "wind_kt", "pressure_hpa", "nature", "wind_source",
           "pressure_source", "status", "track_type"]
_MAX_AGE_H = float(os.environ.get("EWB_LIVE_IBTRACS_MAX_AGE_H", "24"))
_TIMEOUT_S = float(os.environ.get("EWB_LIVE_IBTRACS_TIMEOUT_S", "900"))


def uri() -> str:
    """Window-agnostic CSV: env override, else EWB's own IBTRACS_URI, else ALL.

    For a download use :func:`uri_for` with the window start; this stays the
    identity of the *default* product (cache tag, fallbacks).
    """
    over = os.environ.get("EWB_LIVE_IBTRACS_URI")
    if over:
        return over
    try:
        from extremeweatherbench import inputs as ewb_inputs

        return ewb_inputs.IBTRACS_URI
    except Exception as exc:  # noqa: BLE001 — EWB is optional for this lane
        logger.debug("ibtracs: EWB URI unavailable (%s); using NCEI default", exc)
        return DEFAULT_URI


def uri_for(start: dt.date | dt.datetime | None = None) -> str:
    """Window-aware CSV choice (audit TC-1, 2026-11-19).

    The rolling ``last3years`` product is published on the same stamp as ALL
    and covers every window this lane scans (90-day rolling), at 11.9 MB vs
    333 MB — so pick it whenever ``start`` is inside its reach, and fall back
    to the full-history file only for older windows. An explicit
    ``EWB_LIVE_IBTRACS_URI`` pins one product and wins over the choice.
    """
    over = os.environ.get("EWB_LIVE_IBTRACS_URI")
    if over:
        return over
    if start is not None:
        s = dt.datetime.combine(start, dt.time()) if isinstance(start, dt.date) \
            and not isinstance(start, dt.datetime) else dt.datetime.fromisoformat(str(start))
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if now - s <= LAST3_REACH:
            return LAST3_URI
    return uri()


def cache_dir() -> Path:
    return Path(os.environ.get("EWB_LIVE_IBTRACS_CACHE") or "data/cache/ibtracs")


def cache_path() -> Path:
    """Parquet cache path for the default (ALL) product."""
    return cache_path_for(uri())


def cache_path_for(u: str) -> Path:
    """Parquet cache path, tagged with the source file name so an URI switch
    (e.g. ALL -> last3years) cannot serve the wrong window."""
    tag = url_name(u).removesuffix(".csv") or "ibtracs"
    return cache_dir() / f"fixes_{tag}.parquet"


def cache_candidates(start: dt.date | dt.datetime | None = None) -> list[Path]:
    """Caches this lane may legitimately serve a read from, in preference
    order: the window-aware product first, then the default product (whose
    cache may be the only one on a box that has never fetched the small one)."""
    out = [cache_path_for(uri_for(start))]
    dflt = cache_path()
    if dflt not in out:
        out.append(dflt)
    return out


def url_name(u: str) -> str:
    return u.rstrip("/").rsplit("/", 1)[-1] or "ibtracs.csv"


# --------------------------------------------------------------- download --
def _needed_cols(all_cols: list[str]) -> list[str]:
    keep = {"SID", "SEASON", "NUMBER", "BASIN", "NAME", "ISO_TIME", "NATURE",
            "LAT", "LON", "TRACK_TYPE", "STATUS", "DIST2LAND", "LANDFALL"}
    for ag in WIND_AGENCIES:
        keep |= {f"{ag}_WIND", f"{ag}_PRES"}
    return [c for c in all_cols if c in keep]


def parse_csv(raw: bytes) -> pd.DataFrame:
    """IBTrACS list CSV -> standardised fix table (see COLUMNS).

    Public so tests (and the AR lane) can parse the same bytes we would cache.
    """
    import io

    df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    cols = _needed_cols(list(df.columns))
    missing = [c for c in ("SID", "ISO_TIME", "LAT", "LON") if c not in cols]
    if missing:
        raise ValueError(f"ibtracs: unexpected header, no {missing}")
    df = df[cols]
    # the units row (SEASON == "Year") is metadata, not a fix
    df = df[df["SEASON"].astype(str).str.strip() != "Year"]

    out = pd.DataFrame(index=df.index)
    out["sid"] = df["SID"].astype(str).str.strip()
    out["season"] = pd.to_numeric(df["SEASON"], errors="coerce").astype("Int64")
    out["number"] = pd.to_numeric(df["NUMBER"], errors="coerce").astype("Int64")
    out["basin"] = df["BASIN"].astype(str).str.strip().replace({"nan": "", "None": ""})
    out["name"] = df["NAME"].astype(str).str.strip()
    out["time"] = pd.to_datetime(df["ISO_TIME"], errors="coerce",
                                 format="%Y-%m-%d %H:%M:%S")
    out["lat"] = pd.to_numeric(df["LAT"], errors="coerce")
    out["lon"] = normalize_lon(pd.to_numeric(df["LON"], errors="coerce"))
    out["nature"] = df["NATURE"].astype(str).str.strip().str.upper()
    out["nature"] = out["nature"].replace({"": "NR", "NAN": "NR"})
    # the CSV carries no STATUS column (that one is netCDF-only); TRACK_TYPE is
    # the track's provenance (main / US-PROVISIONAL / *_spur) and is kept so a
    # consumer can tell provisional data from a final best track
    out["track_type"] = df["TRACK_TYPE"].astype(str).str.strip().str.upper() \
        if "TRACK_TYPE" in df.columns else ""
    out["status"] = (df["STATUS"].astype(str).str.strip().str.upper()
                     if "STATUS" in df.columns else "")

    wind, wsrc = _pick_agency(df, "WIND")
    pres, psrc = _pick_agency(df, "PRES", prefer=wsrc)
    out["wind_kt"] = wind
    out["wind_source"] = wsrc
    out["pressure_hpa"] = pres
    out["pressure_source"] = psrc

    out = out.dropna(subset=["sid", "time", "lat", "lon"])
    # real SIDs are YYYYJJJ + N/S + 5 digits (13 chars); the mask also drops
    # any stray header/junk row that survived above
    out = out[out["sid"].str.match(r"^\d{7}[NS]\d{5}$")]
    # spur tracks are duplicate "what-if" tracks in the provisional file: they
    # would double-count a storm
    if "track_type" in out.columns:
        out = out[~out["track_type"].str.endswith("_SPUR")]
    return out[COLUMNS].sort_values(["sid", "time"]).reset_index(drop=True)


def _pick_agency(df: pd.DataFrame, suffix: str,
                 prefer: pd.Series | None = None) -> tuple[pd.Series, pd.Series]:
    """First agency (in WIND_AGENCIES order) carrying a value at each fix.

    ``prefer`` (the wind source of the same fix) is honoured with an earlier
    pass for pressure, so wind and pressure describe the same agency whenever
    possible — IBTrACS warns against mixing agency values inside one storm.
    """
    val = pd.Series(np.nan, index=df.index, dtype="float64")
    src = pd.Series("", index=df.index, dtype="object")
    passes = ([prefer] if prefer is not None else []) + [None]
    for pref in passes:
        for ag in WIND_AGENCIES:
            col = f"{ag}_{suffix}"
            if col not in df.columns:
                continue
            v = pd.to_numeric(df[col], errors="coerce")
            take = v.notna() & val.isna()
            if pref is not None:
                take &= pref.eq(ag)
            val = val.mask(take, v)
            src = src.mask(take, ag)
    return val, src


def normalize_lon(lon):
    """Longitude -> [-180, 180) (vectorised; NaN passes through)."""
    return (lon + 180.0) % 360.0 - 180.0


def fetch(force: bool = False,
          start: dt.date | dt.datetime | None = None) -> Path | None:
    """Ensure a fresh parquet cache for the window starting at ``start``
    (:func:`uri_for` picks the product); return its path, or None when
    unavailable.

    Fail-soft by design (same policy as ``events/tc_screen.py``): a NOAA outage
    or a torn download falls back to the stale cache instead of failing the
    detection run — first the same product's stale cache, then the other
    product's cache (a box that only ever fetched ALL keeps working when the
    window-aware choice switches to last3years).
    """
    cands = cache_candidates(start)
    p = cands[0]
    if p.exists() and not force:
        age_h = (dt.datetime.now()
                 - dt.datetime.fromtimestamp(p.stat().st_mtime)).total_seconds() / 3600.0
        if age_h < _MAX_AGE_H:
            return p
    u = uri_for(start)
    tmp = p.parent / (p.name + ".tmp")
    try:
        logger.info("ibtracs: downloading %s", u)
        raw = urllib.request.urlopen(u, timeout=_TIMEOUT_S).read()
        if not raw[:64].lstrip().startswith(b"SID,"):
            # an HTML error page / a 0-byte body: not the track file
            raise OSError(f"unexpected response head: {raw[:32]!r}")
        df = parse_csv(raw)
        if df.empty:
            raise OSError("no usable fixes parsed")
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        logger.info("ibtracs: cached %d fixes / %d storms -> %s",
                    len(df), df["sid"].nunique(), p)
        return p
    except Exception as exc:  # noqa: BLE001 — stale cache beats no cache
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        for c in cands:
            if c.exists():
                logger.warning("ibtracs: refresh failed (%s); using stale cache %s",
                               exc, c.name)
                return c
        logger.warning("ibtracs: no cache and download failed (%s); "
                       "tropical-cyclone detection will be empty this run", exc)
        return None


# ------------------------------------------------------------------ read ---
def load_tracks(start: dt.date | dt.datetime, end: dt.date | dt.datetime,
                *, force: bool = False) -> pd.DataFrame | None:
    """Fix-level table for storms active in [start, end] (see COLUMNS).

    Seasons are loaded as {start.year-1 … end.year+1} so southern-hemisphere
    seasons that straddle the new year (SEASON 2027 covers Nov 2026 – May 2027)
    are never clipped. The fix set is *all fixes of every storm with at least
    one fix in [start, end]*, not just the in-window fixes: a storm that
    straddles the window edge must be reported with its truncated side visible
    (audit TC-6), and the consumer clips the event with ``window=``. Returns
    None when the source is unreachable and no cache exists.
    """
    p = fetch(force=force, start=start)
    if p is None:
        return None
    try:
        df = pd.read_parquet(p)
    except Exception as exc:  # noqa: BLE001 — torn cache
        logger.warning("ibtracs: cache unreadable (%s)", exc)
        return None
    y0, y1 = pd.Timestamp(start).year, pd.Timestamp(end).year
    seasons = set(range(min(y0, y1) - 1, max(y0, y1) + 2))
    if "season" in df.columns:
        df = df[df["season"].astype("Float64").isin([float(s) for s in seasons])]
    t0, t1 = pd.Timestamp(start), pd.Timestamp(end)
    in_win = (df["time"] >= t0) & (df["time"] <= t1)
    df = df[df["sid"].isin(df.loc[in_win, "sid"].unique())]
    return fill_basin(df).sort_values(["sid", "time"]).reset_index(drop=True)


def source_status(as_of: dt.datetime | None = None,
                  window: tuple[dt.date, dt.date] | None = None,
                  published_sids: list[str] | None = None) -> dict | None:
    """Coverage of the cached archive for the UI's honest zero (2026-11-17).

    IBTrACS publication lags real time by days-to-weeks, so an empty
    tropical-cyclone lane must be distinguishable from "no cyclones
    happened": the pipeline publishes this block into meta.json and the
    dashboard quotes the archive's reach (latest fix + lag) whenever the
    lane prints zero. Reads ONLY the parquet (fail-soft) — this must never
    trigger a download or fail a publish.

    ``window`` + ``published_sids`` (ne-fix-tc 2026-11-19, audit TC-3) add the
    storm UNIVERSE, so a non-empty lane can also say what it did NOT publish:
    ``storms_in_window`` (storms with a fix in the window), ``published``
    (of those, how many became events) and ``pending``/``pending_names`` —
    storms whose last fix is at the archive's own edge (still live when the
    build ran, so the qualification rule cannot be evaluated yet). Without
    those, "44 storms tracked" was the only number and it conflated published
    events with storms seen (audit TC-4).

    Returns ``{"latest_fix", "lag_days", "cache_age_h", "note", ...}`` or None
    when no readable cache exists (the publish records the null explicitly,
    so "lane ran but archive unreachable" stays visible to the UI).
    """
    p = None
    cands = [c for c in cache_candidates(window[0] if window else None)
             if c.exists()]
    if not cands:
        return None
    # newest readable cache wins: after the TC-1 source switch a box may hold
    # both products, and "the archive's reach" must describe what the lane
    # actually read most recently, not a stale remnant of the other one
    p = max(cands, key=lambda c: c.stat().st_mtime)
    try:
        import pyarrow.parquet as pq

        times = pq.read_table(p, columns=["time"]).column("time").to_pandas()
        latest = pd.to_datetime(times, errors="coerce", utc=True).max()
        if pd.isna(latest):
            return None
        mtime = dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)
        universe: pd.DataFrame | None = None
        if window is not None:
            need = [c for c in ("sid", "time", "name", "basin")
                    if c in set(pq.ParquetFile(p).schema_arrow.names)]
            universe = pq.read_table(p, columns=need).to_pandas()
            universe["time"] = pd.to_datetime(universe["time"], errors="coerce")
            t0 = pd.Timestamp(dt.datetime.combine(window[0], dt.time()))
            t1 = pd.Timestamp(dt.datetime.combine(window[1], dt.time())) \
                + pd.Timedelta(days=1)
            universe = universe[(universe["time"] >= t0) & (universe["time"] <= t1)]
    except Exception as exc:  # noqa: BLE001 — honesty note, not a dependency
        logger.warning("ibtracs: source_status unreadable (%s)", exc)
        return None
    now = as_of or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    latest = latest.to_pydatetime()
    out = {
        "source": p.name.removeprefix("fixes_").replace(".parquet", ".csv"),
        "latest_fix": latest.astimezone(dt.timezone.utc).isoformat(),
        "lag_days": round(max(0.0, (now - latest).total_seconds() / 86400.0), 1),
        "cache_age_h": round((now - mtime).total_seconds() / 3600.0, 1),
        "note": "IBTrACS best-track publication lags real time by days to weeks",
    }
    if universe is None:
        return out
    try:
        pub = set(published_sids or [])
        last = universe.groupby("sid")["time"].max()
        # a storm whose LAST fix is the archive's last fix was still being
        # tracked when the file was cut: qualification (>=3 fixes >=34 kt over
        # >=12 h) cannot be judged until the next publication
        edge = pd.Timestamp(latest)
        if edge.tzinfo is not None:      # parquet times are naive UTC
            edge = edge.tz_localize(None)
        tail = last[last >= edge - pd.Timedelta(hours=12)]
        names = universe.groupby("sid")["name"].last()
        pend = [s for s in tail.index if s not in pub]
        out["storms_in_window"] = int(universe["sid"].nunique())
        out["published"] = len(pub)
        out["active_tail"] = int(len(tail))
        out["pending"] = len(pend)
        out["pending_names"] = [str(names.get(s) or "Unnamed").strip().title()
                                or "Unnamed" for s in pend[:4]]
    except Exception as exc:  # noqa: BLE001 — reach above must survive
        logger.warning("ibtracs: storm-universe note failed (%s)", exc)
    return out


def basin_name(code: str) -> str:
    return BASIN_NAMES.get((code or "").strip().upper(),
                           (code or "unassigned basin").strip() or "unassigned basin")


def basin_for(lat: float, lon: float) -> str:
    """Basin code from a fix position — the fallback IBTrACS itself needs.

    v04r01 leaves BASIN EMPTY for the whole North Atlantic: checked on the
    live ALL file, all 2,306 empty-BASIN storms are northern-hemisphere
    systems in the Atlantic box (lat 7..83, lon -137..63 — the outliers past
    -100/+40 are the same tracks as Gulf departures and European remnants).
    Label them from position instead of printing "unassigned basin" for a
    third of the season; anything we cannot place stays unknown.
    """
    if not (np.isfinite(lat) and np.isfinite(lon)) or lat < -10.0:
        return ""
    if -100.0 <= lon <= -10.0:
        return "NA"
    if lat >= 0.0 and lon < -100.0:
        return "EP"
    return ""


def fill_basin(df: pd.DataFrame) -> pd.DataFrame:
    """Fill the empty BASIN column from position (see ``basin_for``)."""
    if df is None or not len(df) or "basin" not in df.columns:
        return df
    empty = df["basin"].astype(str).str.strip().eq("")
    if empty.any():
        df = df.copy()
        guess = [basin_for(la, lo) for la, lo in zip(df.loc[empty, "lat"],
                                                     df.loc[empty, "lon"])]
        df.loc[empty, "basin"] = guess
    return df


def main(argv=None) -> int:  # pragma: no cover - CLI on the ewb LXC only
    """CLI: python -m ewb_live.sources.ibtracs [--force] [--days 90] [--basins]

    Downloads/refreshes the cache and prints a coverage summary (sanity check
    for the tc lane: fix/storm counts per season and per basin).
    """
    import argparse

    from ewb_live._guard import require_ewb_host

    require_ewb_host()
    ap = argparse.ArgumentParser(prog="python -m ewb_live.sources.ibtracs")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--basins", action="store_true", help="per-basin storm counts")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    end = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    start = end - dt.timedelta(days=args.days)
    df = load_tracks(start, end, force=args.force)
    if df is None:
        print("IBTrACS unavailable (no cache, download failed)")
        return 1
    print(f"source: {uri_for(start)}")
    in_win = (df["time"] >= pd.Timestamp(start)) & (df["time"] <= pd.Timestamp(end))
    print(f"window {start:%Y-%m-%d} .. {end:%Y-%m-%d}: "
          f"{int(in_win.sum())} fixes in window / {df['sid'].nunique()} storms "
          f"({len(df)} fixes incl. storm lives outside the window)")
    full_path = next((c for c in cache_candidates(start) if c.exists()), None)
    full = pd.read_parquet(full_path) if full_path else df
    print(f"full cache: {full['sid'].nunique()} storms "
          f"({full['season'].min()}..{full['season'].max()})")
    if args.basins:
        g = (df.assign(basin=df["basin"].replace({"": "?"}))
             .sort_values(["sid", "time"]).groupby("basin")["sid"].nunique()
             .sort_values(ascending=False))
        for b, n in g.items():
            print(f"  {b:<3} {n:>3}  {basin_name(b)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
