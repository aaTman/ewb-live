# Flood event type — design notes

Shared file. **Part 1 = observation ingest (role `flood-imerg`, this file as written 2026-11-16).**
Part 2 = the ERA5 precipitation climatology (role `flood-clim`) — append it below as
`## Part 2 — climatology` rather than starting a second file, so the flood trio has one design doc.

## Part 1 — IMERG/GPM observation ingest (flood-imerg)

Owner spec: "use IMERG/GPM for the past 90 days of observations" (climatology comes from ERA5, Part 2).
Everything below was verified against NASA CMR + GES DISC endpoints on 2026-11-16 (UTC); "verified"
is used literally for the items I probed, the rest is cited from product metadata.

### 1. Which product

Runs of IMERG V07 (V07B is the current build; V06 retired):

| run | half-hourly collection | daily collection | latency | usable for "last 90 days"? |
|---|---|---|---|---|
| Early  | `GPM_3IMERGHHE` (`C2723758340-GES_DISC`) | `GPM_3IMERGDE` (`C2723754850-GES_DISC`) | ~4 h after the UTC day closes (measured: the 2026-11-15 Early-daily granule was produced 2026-11-16T04:29Z) | **yes — the only run that is current** |
| Late   | `GPM_3IMERGHHL` (`C2723754845-GES_DISC`) | `GPM_3IMERGDL` (`C2723754859-GES_DISC`) | ~14–18 h (no back-fill of the last ~1 day) | mostly (1–2 day hole) |
| Final  | `GPM_3IMERGHH` (`C2723754847-GES_DISC`) | `GPM_3IMERGDF` (`C2723754864-GES_DISC`) | ~3.5 months | **no** — the last ~3.5 months of the window do not exist yet |

**Chosen: `GPM_3IMERGDE` — IMERG Early Run, L3 daily, 0.1°, V07** (`3B-DAY-E.MS.MRG.3IMERG.<YYYYMMDD>-S000000-E235959.V07C.nc4`).

Why the daily product and not the half-hourly one:
* the flood rule is a daily (and 3-day) accumulation, so half-hourly time detail is not consumed;
* volume: Early-daily is **one granule/day ≈ 26.5 MB** (size from CMR) → ~2.4 GB per 90-day window,
  vs 48 granules/day ≈ 0.6 GB/day ≈ 55 GB per window for `GPM_3IMERGHHE`. The ewb LXC root FS is at
  **98 % (13 GB free)**, so half-hourly ingest is not affordable there;
* one request per day is the gentlest possible use of GES DISC, and a missing granule is a whole
  missing day — an unambiguous completeness signal (per-pixel sampling count is still carried, §3).

Late/Final are reachable through the same code (`product="late"|"final"`), so a later "quality
upgrade" (re-ingest a day from Late once it exists) is a config change, not a rewrite. Default stays
Early-only: one homogeneous source series for the whole rolling window, no run-to-run mixing.

Grid: producer 0.1° global grid, cell-centred — 1800 latitudes (−89.95 … 89.95), 3600 longitudes
(−180 … 179.95), lat ascending, lon −180-based. Code does **not** hardcode this: it reads `lat`/`lon`
out of each granule and asserts 0.1° spacing + global coverage, so the ingest cannot silently drift
if NASA changes the grid. flood-detect must remember this is **0.1° while ERA5 is 0.25°** (Part 2
covers regridding/bias).

### 2. Access route (least credentials wins)

| route | anonymous? | verdict |
|---|---|---|
| GES DISC HTTPS `data.gesdisc.earthdata.nasa.gov/data/GPM_L3/...` | no — 302 to `urs.earthdata.nasa.gov` | **chosen**: one free NASA Earthdata account, one `~/.netrc` entry, plain `requests` (no new dependency, no `earthaccess`) |
| GES DISC S3 direct `s3://gesdisc-cumulus-prod-protected/...` | no — anonymous GET returns `AccessDenied: Anonymous users cannot invoke requests against Requester Pays buckets`; the `/s3credentials` presign endpoint is Earthdata-protected too | same credential as HTTPS → no advantage for 26 MB/day; left as a future same-region speed-up |
| GES DISC OPeNDAP (`opendap.earthdata.nasa.gov/collections/<CID>/granules/...`, `gpm1.../opendap/...`) | no — 302 to `/login/urs` | works with the same netrc; useful for sub-daily/server-side subsets, not needed for whole-globe daily files |
| AWS Open Data registry | n/a | no GPM/IMERG dataset in the registry index (fetched and grepped: IMERG text appears only in GES DISC description/tutorial links, no `s3://` location) |
| Pangeo forge / `gs://pangeo-data/gpm_imerg/` | **yes (anonymous)** | **stale**: a 2020 zarr experiment (`late/chunk_time/…`, `early_test/chunk_space/…`, half-hourly V06-style variable names), last object written **2020-07-28** → cannot cover 2026 |
| GPM Tales / GPM_API (`gpm.giss.nasa.gov/gpm_api`) | anonymous in principle | host does not resolve from our networks today (DNS failure) → not an ingest path |
| NASA Giovanni | yes | aggregated plots/derived time-series, not the native 0.1° daily field → unusable as the observation grid |
| GIBS / Worldview WMTS tiles | yes | lossy colour-mapped tiles of a rate field → not quantitative |
| Google Earth Engine `NASA/GPM_L3/IMERG_V07` | no (Google project + quota) | heavier credential and awkward bulk egress for a global daily grid |
| ORNL DAAC / other mirrors | no (same Earthdata login) | no advantage |

⇒ **one free NASA Earthdata account is the only credential needed**; nothing anonymous can carry the
last-90-days global daily grid. Credentials are read at runtime from `~/.netrc` (machine
`urs.earthdata.nasa.gov`) or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`; **never stored in the repo**.
Discovery (CMR granule search, which granules exist, sizes) is anonymous, so `--dry-run`/gap surveys
work without credentials.

### 3. Quantities, day boundary, screening

* Variable list of `GPM_3IMERGDE` V07 (read from CMR variable associations):
  `precipitation`, `MWprecipitation`, `probabilityLiquidPrecipitation`, `precipitation_cnt`,
  `precipitation_cnt_cond`, `MWprecipitation_cnt`, `MWprecipitation_cnt_cond`, `randomError`,
  `randomError_cnt`, coords `time`, `time_bnds`, `lat`, `lon`. (No `Quality`/`IRprecipitation`/`PRW`
  in the V07 daily file, unlike the half-hourly product.)
* **Units are taken from the granule, never assumed.** `precipitation` carries a `units` attribute
  ("mm/hr" mean sampling-weighted rate in the producer's daily files → ×24 h; "mm/day" → as is);
  anything else raises, so a producer change cannot silently corrupt the accumulation. A global-mean
  sanity line (climatology ≈ 2–3 mm/day) is printed on every real ingest.
* **Day boundary: the UTC calendar day 00:00:00–23:59:59.999**, i.e. the granule's own
  `S000000-E235959` window (CMR `TemporalExtent` confirms `T00:00:00Z`–`T23:59:59.999Z`). The
  half-hourly path groups by `time.dt.date` in UTC (steps `S000000` … `S233000`, 48 of them) and sums
  `rate × 0.5 h`; both paths are tested against synthetic data (`tests/test_imerg.py`).
* **Fill/missing**: any non-positive value (the producer fill is `-9999`) → NaN. **No zero-filling, ever**:
  a day whose granule is absent is written as an all-NaN day flagged `missing`, a day whose granule
  reports `precipitation_cnt < 48` at a pixel is flagged `partial` at that pixel.
* **Screening carried into the daily store** (so flood-detect can choose, we do not silently decide
  for it): `precipitation` mm/day (float32), `precipitation_cnt` uint8 (how many of the 48 half-hourly
  estimates the daily mean used → the completeness signal), `probabilityLiquidPrecipitation` uint8
  (0 = missing, 1 = likely solid, 100 = likely liquid *or* no precipitation — per CMR definition it must
  be read together with positive precipitation, which is why it is stored as a mask, not applied as a
  filter). `load_imerg_daily(screen=...)` exposes `none` (default), `cnt` (drop pixels with
  `cnt < min_cnt`, default 48) and `polid` (drop pixels flagged likely-solid).
* Per-day index `status.json` records granule id, present/missing/partial, byte size, global mean and
  max, `precipitation_cnt` minimum and the ingest timestamp — the completeness view without opening
  the fields.

### 4. Storage layout (ewb LXC, `/opt/ewb-live/data/imerg/`; `EWB_LIVE_IMERG_DIR` overrides)

```
data/imerg/
  raw/GPM_3IMERGDE.07/2026/11/3B-DAY-E.MS.MRG.3IMERG.20261115-S000000-E235959.V07C.nc4   # kept: --keep-raw days (default 3)
  daily/imerg_20261115.nc      # per-day compressed NetCDF4: precipitation/cnt/polid, mm/day
  status.json                  # per-day completeness index
```

One file per day (not one growing zarr) makes idempotence, resume and the rolling window plain file
operations: downloads write `<name>.part` then atomically rename; a day is ingested only if its
daily file is absent or `--overwrite`; `roll_window(keep_days=90)` deletes older daily+raw files.
Per-day NetCDF4 (deflate) is ~6–12 MB → **90 days ≈ 0.6–1.1 GB** of daily fields, plus ≤3 raw
granules (~80 MB): fits the current 13 GB headroom. Full-window re-downloads are avoided because the
daily file is the cache of record.

### 5. Interface published to `flood-detect`

```python
load_imerg_daily(start: date, end: date, *, product="early", root=None,
                 screen="none"|"cnt"|"polid", min_cnt=48) -> xr.DataArray   # (time, lat, lon) float32 mm/day
load_imerg_mask(start: date, end: date, ...) -> xr.DataArray               # (time, lat, lon) uint8 bit flags
load_imerg_status(start: date, end: date, ...) -> pd.DataFrame              # per-day completeness
```

Signatures + grid + units are posted in `notes/channel_new_events.md`. `flood-detect` owns the join to
the ERA5 climatology (grid mismatch 0.1° vs 0.25° and the satellite-vs-ERA5 bias handling belong to
Part 2 / the join, not to the ingest).

### 6. Open / owner-dependent

* Earthdata credentials are **not** on the ewb LXC (no `~/.netrc`, no env vars) → the real fetch is
  blocked until the owner adds one account. Exact steps are in the DONE entry of
  `notes/channel_new_events.md`. Everything else (design, ingest, aggregation, masks, rolling window,
  unit tests on synthetic granules) is built and tested offline.
