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

---

## Part 1b — as built, after the AWS Open Data update (flood-imerg, 2026-11-17)

Everything in Part 1 stands except the four deltas below; each was re-verified from the primary
source on 2026-11-17 03:20–03:35 UTC (the numbers cited in Part 1 that are re-stated here held up).

### 1b.1 Run policy is `auto`, not Early-only

Default `product="auto"` = **Late Run where it exists, Early Run only for the head the Late Run has
not reached** (`survey_window` picks per day; `Granule.product` records which run served a day;
`ingest_day(upgrade=True)` rebuilds a cached Early head day once the Late granule lands, so the
window converges on the preferred run without losing days). Pin one run with
`product="early"|"late"|"final"` for a strictly homogeneous series. This replaced Early-only because
the measurement below says the Late run costs almost nothing in freshness:

| collection | CMR hits 2026-11-04…11-18 | last day published (today = 11-17, 03:30Z) | granule `Update` stamp |
|---|---|---|---|
| `GPM_3IMERGDE` (Early daily) | 12 | **2026-11-15** | D+1 ≈ 04:27–04:31 Z |
| `GPM_3IMERGDL` (Late daily)   | 12 | **2026-11-15** | D+1 ≈ 14:28–14:29 Z |
| `GPM_3IMERGDF` (Final daily) | **0** | — (≳3.5-month latency: the recent window does not exist) | — |
| `GPM_3IMERGHHL` (Late half-hourly) | 600 | 2026-11-16 (≈46 of 48 steps/day in the window) | up to 02:24 Z on D+1 |

Both daily runs are at D−2 right now, so `auto` usually takes the Late granule for every day and the
Early run is a safety net for the hours before 14:29 Z, not a standing 15-hour hole. Final is
confirmed unusable for a rolling window by absence, not by inference. Half-hourly reaches one day
further but at 48 granules/day ≈ 0.6 GB/day (≈55 GB/window) against 26 MB/day for daily: the daily
product remains the pipeline (see Part 1 §1), and `accumulate_half_hourly()` stays tested for a
future switch.

### 1b.2 AWS Open Data: real, but **not** anonymous (owner's update, checked)

The owner's update expected anonymous S3 with no Earthdata login. The registry entries are there —
`nasa-gpm3imerghhl`, `nasa-gpm3imergde`, `nasa-gpm3imergdl`, `nasa-gpm3imergdf` — all pointing at
one bucket, `gesdisc-cumulus-prod-protected` in `us-west-2`, and all flagged **Controlled Access**.
Verified with unsigned requests (2026-11-17):

```
GET https://gesdisc-cumulus-prod-protected.s3.us-west-2.amazonaws.com/?list-type=2
  → 403 AccessDenied: "Anonymous users cannot invoke requests against Requester Pays buckets."
GET …/GPM_L3/GPM_3IMERGDL.07/2026/319/3B-DAY-L…V07B.nc4 → 403
```

So the AWS mirror cannot remove the Earthdata login; it is the same account as GES DISC HTTPS, whose
`/s3credentials` endpoint hands the ingest hour-long temporary S3 credentials. **The AWS route is
still the default transport** (`route="auto"`: same-region S3 first, GES DISC HTTPS fallback, then
`ImergAuthError` with the owner steps if no login exists) — it avoids the Apache redirect and is the
faster path — but the credential ask in §6 stands. `s3_key()` maps a CMR https URL to the object key;
`_get_s3` re-mints credentials on `AccessDenied`/`ExpiredToken` and both transports write `.part` →
rename, reject non-HDF5/NetCDF bodies and **cap a body that never reaches EOF** (see 1b.4).

### 1b.3 Disk budget on a filesystem that is 98–100 % full

The ewb LXC root FS was at **100 % (4.5 GB free)** on 2026-11-17 (6.4 GB by the time of the dry run —
it moves), and it hosts the live service. Two changes make the ingest safe there:

* **`require_headroom()`** — measured before anything is downloaded: the run is refused unless
  `free − projected_peak ≥ MIN_FREE_BYTES` (default 2 GiB, `--min-free-bytes`,
  `EWB_LIVE_IMERG_MIN_FREE_BYTES`). The error states the numbers and the two levers. `update
  --dry-run` prints the same verdict without needing credentials.
* **Bounded peak** — `ingest()` fetches in batches of `batch_days` (default `keep_raw + 2`) days and
  rolls `raw/` between batches, so peak is one batch of granules plus the daily store instead of a
  full raw backfill. `project_window()` reports that peak (it is the guard's input).

Measured on the LXC, 2026-11-17 03:34Z, `update --days 90 --dry-run`:

```
published granules: 88 of 90 days ({'late': 88}); gaps: [2026-11-16, 2026-11-17]
raw total 2.56 GB (kept: last 3 days), daily store ~1.29 GB for 88 days (~12 MB/day compressed)
peak during the run ~1.45 GB in 18 batch(es) of 5 days
disk: 6.42 GB free → fits (2.15 GB must stay free)
```

so **90 days ≈ 1.3 GB of daily fields + ≤3 raw granules (≈80 MB), peak ≈1.5 GB** — it fits, with the
guard leaving the live service 2 GiB.

### 1b.4 Bugs the first full test run found (all now tested)

The unit suite had never completed before the /tmp runaway (see the channel postmortem), and once it
did it took four real bugs with it: a **dict-valued global attr** (`grid`) made *every* daily NetCDF
write fail, so no daily file had ever been produced; **`load_imerg_mask` lit `full_sampling` on
absent days** (the `CNT_UNKNOWN` fill satisfied `cnt >= 48`), so a missing day did not read as 0;
**`date.fromtimestamp(ts, tz)`** in the CLI does not exist, so every CLI subcommand died at argument
parsing; and **CMR rejects `sortable`** for these collections (HTTP 400 "Parameter [sortable] was not
recognized"), so `survey()` could never have returned a granule list. Also carried away: the download
copy now goes through `_copy_capped`/`_read_capped` instead of `shutil.copyfileobj`, an oversized or
non-granule `.part` is deleted instead of resumed, and `ingest`'s `already` entries carry the cached
mean so an incremental run still reports a window mean. 28 offline tests, ~13 s, few MB.

CMR gives **no `Size`** for these granules in `umm_json`, so `Granule.size_mb` is 0.0 and the disk
projection falls back to `EST_RAW_BYTES` (30 MB) — the numbers above are estimates for that reason.
