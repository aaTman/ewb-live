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

---

## Part 1c — streaming policy + packed daily store (owner directive 2026-11-17, ~11:30 UTC)

Owner: *raw granules must not be persisted; keep state minimal*. Implemented:

* `DEFAULT_KEEP_RAW = 0` — a raw granule is now streaming input: it lands in `raw/` only
  as an in-flight `.part`, is converted to the daily field, and is rolled immediately
  (`--keep-raw N` still keeps N days for re-derivation). The daily field is the only
  state. The disk-headroom guard is unchanged and still gates every fetch.
* **Packed daily precipitation**: `uint16` at `0.01 mm` steps (`PP_SCALE_MM`) instead of
  float32 — the producer's own precision, so the packing is lossless w.r.t. the data.
  65535 is the fill value (decoded to NaN by `load_imerg_daily`), so the ceiling is
  655.34 mm/day; `ingest_day` **clips** hotter cells explicitly and records
  `clipped_at_max` in `status.json` (never wraps). `mask_and_scale` is off in
  `_open_dataset` (granules must be read raw), so `_decode_precip()` unpacks explicitly;
  legacy float32 daily files pass through unchanged.
* Measured on an incompressible (pixel-noise) 0.1° global synthetic — a worst case:
  9.19 MB → **5.15 MB/day (−44 %)**; peak RSS of the real-size `ingest_day` **452 MB**.
  Real fields compress better; with the conservative 14 MB/day guard estimate a 90-day
  window still projects ~1.31 GB (peak ~1.47 GB in 18 batches), realistic ~0.4–0.7 GB.
* Streaming the *half-hourly* granules instead of daily was evaluated and rejected:
  CMR sizes today `GPM_3IMERGHHL` median 6.60 MB × 48 ≈ 317 MB/day vs one 25.5–26.5 MB
  daily granule — 12× the bytes, 48× the requests, and no consumer wants sub-daily time
  (§1: the flood rule is daily/3-day accumulation).
* Lat-cropping ±60° would save only ~13 % (band area ∝ sin lat); regridding to the ERA5
  0.25° grid would save another 6.25× but decides flood-detect's join inside the ingest
  — left to flood-detect/flood-clim as before.

## Part 1d — first live credential run: two account-side gates (2026-11-17)

netrc landed on ewb; login itself works (URS profile → 200; OPeNDAP accepts the Basic
auth). Two consent steps block the data, both owner-side, now surfaced verbatim in the
auth errors (`_AUTH_STEPS` step 5, `s3_credentials` hint, `_http_get` body excerpts):

1. GES DISC granule GET → 401 **"Be sure to agree to the EULA."** — the account must
   accept the data-centre terms once (https://disc.gsfc.nasa.gov/data-use).
2. s3credentials → HTTP 500 via URS redirect *"Pre authorization required for this
   application"* → https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ
   once authorised, the AWS mirror route unlocks (HTTPS works without it).

Also fixed on the way: the preemptive-auth handler read `req.fullurl` (stdlib attribute is
`full_url`) — every offline test monkeypatches `_opener`/`_http_get`, so the bug only
existed against the live endpoint; now covered by a handler-level regression test.

---

# Flood event type — climatology design (role `flood-clim`)

Owner spec (2026-11-16): *"use ERA5 for climatologies for QPF, then use
IMERG/GPM for the past 90 days of data."* This note covers the **climatology
half**: the exceedance quantity, its estimator, and how the ERA5 thresholds are
supposed to be compared against IMERG observations. The observation ingest is
role `flood-imerg`; joining them is `flood-detect`. Code:
`ewb_live/climatology/precip_clim.py`; API contract for the other two roles:
`notes/channel_new_events.md`.

---

## 1. The exceedance quantity — decision

**Chosen: the wet-day-conditioned day-of-year percentile of (a) the UTC-day
precipitation total and (b) the trailing 72 h accumulation, at p95 and p99,
per 0.25° ERA5 grid cell, pooled over 1990-2019 with a flat ±15-day
day-of-year window.** Both quantities in mm of liquid-water equivalent;
`accumulation=1` = the UTC day 00:00-24:00, `accumulation=3` = days d-2…d
(trailing, ending 24:00 UTC of the indexed date).

Why this and not the alternatives:

| Candidate | Verdict | Why |
|---|---|---|
| **All-days percentile of daily totals** | rejected as primary | ERA5 puts nonzero precipitation over essentially **100 % of the globe on a typical day** (measured on 1990-01-31: 100 % of cells > 0 mm, 45 % ≥ 0.1 mm, only 9 % ≥ 1 mm). An all-days p95 in a dry season or a dry region therefore collapses toward zero, so a single drizzle day "exceeds" it. It also makes the p99 in arid cells meaningless. |
| **Wet-day-conditioned percentile (≥ 1 mm), daily** | **chosen** | This is the climate-index convention (ETCCDI `RX1day`/`R95p` use wet days, RR ≥ 1 mm) and it measures "unusual *for a rain day*", which is the physically meaningful statement for a rain flood. It also behaves sensibly across the seasonal cycle: monsoon p95 ≫ dry-season p95 without any regime detection. |
| **Rolling 3-day accumulation percentile** | **chosen as the companion** | Rain floods are driven by antecedent accumulation as much as by a single convective burst; a 3-day threshold catches the "50 mm/day × 3" type that a daily p99 can rate as merely-ordinary, and it de-emphasises single-cell spikes. Trailing window (days d-2…d) so the threshold for date *d* is the observed 72 h ending at the end of *d*. |
| **Return-period / GEV-Gumbel fit (e.g. 5-yr level)** | rejected for the live product | Needs annual maxima (30 samples/cell/year-block) fitted per grid cell; the GEV shape parameter is poorly determined at n≈30, so extrapolated 5-yr/20-yr levels are noisy exactly where the sample is thin (arid/complex terrain), the fit is a per-cell statistical model that must be re-derived when the reference period changes, and it is *not* day-of-year aware, so it would flag the same threshold in January and July. It also breaks symmetry with the temperature side of the product, which is percentile-based (EWB-native). A return-period layer remains a reasonable future *diagnostic* (the GEV of the annual maxima of the 3-day accumulation) if the owner wants "1-in-N-year" language on the dashboard. |
| **Absolute-threshold-only** (e.g. 20 mm/day) | rejected as primary, **kept as a floor** | A global absolute threshold flags the Amazon as permanently flooding and the Sahel as never flooding. Used as a *plausibility floor* on top of the percentile (see §4) so a few-mm day in a desert cannot become an "event". |

Estimator details, and why they match the rest of the product:

* **Pooled flat ±15-day window, pooled across years** — the same estimator
  family as the live temperature climatology (`owb_clim.py`, ±10 days there),
  so the two event families are methodologically comparable and share the
  1990-2019 reference period. Window length: ±15 days keeps ≥ 930 daily
  candidates per cell per day-of-year (30 yr × 31 d), which matters most for
  the p99 and for cells with a sharp seasonal rain window; ±10 would leave
  ~620. The trade (blurring a fast monsoon onset over ~15 days) is the same
  one the temperature product already accepts.
* **Exact per-cell quantile on the wet subset** (`quantile_from_sorted`): the
  pooled samples are sorted with NaN at the end and the per-cell position
  `q·(n-1)` is gathered, i.e. exactly `np.quantile(..., method="linear")` on
  each cell's own valid sample count (verified against a reference
  implementation in `tests/test_precip_clim.py`).
* **Reliability mask**: threshold = NaN where the pooled wet sample count is
  < 30, and `wet_sample_count` is published (uint8, saturating at 255) so a
  consumer can use its own floor. A percentile of a dozen rain days per 30
  years is not a climate threshold; in the Sahara this blanks the driest cells
  rather than inventing a 3 mm "p99".

Convenient cross-reference between the percentile and a return-period reading:
a wet-day p95 corresponds to an all-days exceedance probability of
`wet_fraction × 0.05`, so for a cell that rains on 30 % of days it is roughly
a 1-in-67-day event (~5 per year), and a wet-day p99 is roughly 1-in-1340 days
(~0.3 per year, i.e. between a "5-year" and "10-year" daily extreme). This is
why p95 (frequent, areal flooding) and p99 (rare, severe flooding) are the
right pair to publish; a formal GEV return period would be needed only to put
"1-in-N-years" text on the map.

## 2. Source and the daily-total identity

Public ARCO ERA5 (anonymous GCS, no credentials):

```
gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr
  total_precipitation_24hr  -> read at 00Z of day D+1 = the whole UTC day D
```

Verified 2026-11-16 against the hourly EWB store
(`ar/full_37-1h-0p25deg-chunk-1.zarr-v3`, `total_precipitation`):

* `24hr @ 00Z(D+1)` = the sum of the 24 hourly increments of day D — global max
  abs difference **6e-5 mm** (float32 noise), identical mean/max/wet-fraction;
* `6hr @ T` = the exact accumulation over (T-6 h, T] (max diff 1.5e-5 mm).

One daily field per day instead of 24 hourly ones: **~22 GB compressed instead
of ~530 GB** for 30 years, i.e. a 4-minute download at the observed 284 MB/s
rather than hours of anonymous GCS traffic. The daily field is one zarr chunk
per timestep (721×1440 float32), so *latitude subsetting does not save
network* — the build reads whole global fields and keeps its rows.

ERA5 `tp` is all precipitation types as liquid-water equivalent, so a
threshold over a snow-dominated cell is a snow-water-equivalent threshold;
rain-on-snow floods are in scope, melt-only floods are not distinguishable
(§5 open items).

## 3. Grids, the IMERG mismatch, and bias

* **Product grid**: ERA5/ARCO 0.25°, latitude 90→-90 DESCENDING, longitude
  0→359.75 — byte-identical coordinate arrays to the live temperature
  climatology `t2m_owb_1990_2019.zarr`, hence to the heat/freeze daily masks and
  the dashboard DAYEXT grid. Flood masks therefore overlay the existing lanes
  without regridding.
* **IMERG is 0.1°** (V06/V07 half-hourly, 1800×3600, cell-centre, lon -180..180).
  A 0.25° threshold and a 0.1° observation are not the same random variable:
  the finer grid has more spatial intermittency, so pointwise exceedance
  frequency rises as the observation grid gets finer than the climatology grid.
  **Recommendation for `flood-detect`: aggregate IMERG up to the ERA5 grid**
  (area-weighted mean of the 0.1° daily totals into 0.25° cells, i.e. a
  conservative 2.5×2.5 areal average) and threshold there — this is the
  direction that does not inflate exceedance, and it also matches how the
  tracker will want contiguous areal objects. The opposite direction (upsample
  thresholds to 0.1°) is supported via
  `precip_threshold_on(lats, lons, dates, ...)` / `grid="imerg"`, defaulting to
  **nearest neighbour** on purpose: interpolating a threshold field smooths
  exactly the extreme maxima we threshold on. `method="bilinear"` exists for
  areal/visual use.
* **Systematic bias (ERA5 reanalysis vs GPM/IMERG satellite).** The two
  conditional distributions differ: ERA5 over-produces light precipitation days
  over land (a wet-day *distribution* shifted toward small amounts, and its
  wet-day count is not IMERG's), and it under-represents short, intense
  convective maxima that a constellation pass can see; IMERG over land outside
  the microwave-coverage core is IR-inferred (indirect, cold-cloud-top
  extrapolation, wet/dry bias depending on regime), and IMERG's own early/late
  runs do not cover the poles. I cannot quantile-map the ERA5 thresholds onto
  IMERG's distribution without an IMERG climatology, which the 90-day
  observation lane cannot supply, and a 20-year IMERG-Final reanalysis fetch
  (~200 GB, Earthdata credentials) is out of this role's scope. So:
  1. **Default (shipped): `scale=1.0`, i.e. thresholds are "ERA5 percentile on
     the ERA5 grid", not "IMERG percentile".** The honest reading of an
     exceedance is "IMERG measured more rain in a cell than ERA5's 95th
     percentile for that cell/date", which is why the absolute floor in §4 is
     mandatory rather than decorative.
  2. **Provided seam**: `precip_threshold*(..., scale=...)` accepts a scalar or
     a per-cell `DataArray`/path and multiplies the threshold. When an IMERG
     reference distribution exists, a per-cell median-ratio (or a
     percentile-of-percentile remap) drops straight in without touching the
     climatology build.
  3. **Cheap partial calibration available to `flood-detect`** if wanted:
     compare IMERG daily vs the *same* ERA5 daily totals (the source store
     covers 1959-2021, so 2019-2021 is available at zero extra cost) over the
     overlap, per cell or per climate region, and feed the ratio to `scale=`.
     A 2-3 year sample fixes the *scale* bias but not the distributional shape.
  4. If the owner wants a genuinely bias-matched product, the decision to
     request is a **0.1° IMERG-Final 2001-2021 percentile climatology** built
     alongside this one (the same estimator, IMERG's own grid, ~200 GB of
     Earthdata download). Then "p95" means p95 *of the observation dataset* and
     the ERA5 product becomes the cross-check instead of the reference.

## 4. Recommended detection recipe (input to `flood-detect`)

```python
obs  = <IMERG daily mm on the ERA5 0.25 deg grid>            # aggregate 0.1 -> 0.25
th   = precip_threshold_series(dates, q, w, grid="era5")      # mm, NaN where unreliable
mask = (obs >= th) & (obs >= FLOOR_MM[w])                     # FLOOR_MM = {1: 20 mm, 3: 50 mm}
```

* Both the 1-day and the 3-day lane are available; the 3-day lane is the one
  that behaves like a flood, the 1-day lane is the one that matches "flash"
  reporting. Publish which one made the event (put it in `details`).
* `FLOOR_MM` is a hydrological plausibility floor, not tuned science: 20 mm/day
  or 50 mm/72 h is roughly where runoff-driven flooding becomes credible for a
  dense areal object. `flood-detect` owns the final number; the constant lives
  in `precip_clim.FLOOR_MM` so the About text and the detector read one source.
* Reliability: a NaN threshold means "no flood is definable here" (hyper-arid
  or too few rain days). Do **not** `fillna(0)` — that turns the Sahara into a
  flood factory.
* Snow: an exceedance in a cold cell can be water-equivalent snow; if the
  event list should not include those, mask by 2 m temperature or by snowfall
  partitioning (a decision for `flood-detect`, not a climatology problem).
* Land: the store has thresholds over the ocean too (ERA5 has precip over
  ocean). IMERG is near-global; the land mask / population exposure filter
  belongs to `flood-detect`.

## 5. Build report (ewb LXC)

Mechanics: two resumable phases — (1) download the daily totals into a
RAM-backed memmap in `/dev/shm` (the box's root disk is at ~98 %, so temp space
is deliberately never on disk), (2) compute the pooled quantiles tile by tile
(91×180 lat/lon tiles, same partition as `owb_clim`) and region-write into the
zarr store. The download is manifest-per-day and the compute is
manifest-per-tile, so either phase resumes after a kill. Disk and RAM guards
abort before starting (and mid-build below 2 GB free) — this is a live box
serving https://ewb.espr.ai.

<!-- BUILD_REPORT_START -->

### Run 1 — 2026-11-16, ewb LXC (`ops/remote.sh bg precipclim … build --workers 12 --threads 24`)

| phase | measured |
|---|---|
| download (daily totals → `/dev/shm` memmap) | 10 959 days (1989-12-29 lead-in … 2019-12-31), **160.3 s**, 45 512 288 640 B uncompressed = **283.9 MB/s** from anonymous GCS, 0 errors, 1 lat pass (rows 0:721) |
| compute (64 tiles = 8 lat × 8 lon) | started 21:39:33, last tile written 23:56 → **≈ 2 h 17 min wall**; the transient unit consumed **1 d 12 h 43 min CPU** (≈ 10.8× on 12 workers) |
| verify + finalize | **17.3 s**, single process, ~250 MB peak |
| store on disk | **4.5 GB** zarr v3 (`total_precipitation` 4.3 GB, `wet_sample_count` 149 MB), chunks `(1,1,31,91,180)` / `(1,31,91,180)`, 4 608 chunks, all present |
| RAM | `/dev/shm` scratch **45.5 GB** (tmpfs = RAM, on a 252 GB box); a worker's own arrays are ~270 MB (`res` 97 MB + `cnt` 48 MB + ~120 MB per-day working set), so 12 workers ≈ 3 GB. joblib additionally memory-mapped the array it ships to workers (leaked `/dev/shm/joblib_memmapping_folder_*` warnings) — keep the scratch in `/dev/shm` and do not move it to the root disk |

The run **did not close the store**: the tile workers had written all 4 608
chunks by 23:56, but the parent process was stopped (`systemctl stop`, 00:54,
at another agent's request) and never wrote the `.done` gate or the provenance
sidecar, so `open_climatology()` correctly refused the store. Two bugs made
that dangerous and invisible, both fixed in `precip_clim.py`:

1. **No progress was visible.** The per-tile `log.info` runs inside a
   joblib/loky worker, whose logging never reaches the runner's log file: a
   healthy two-hour build logged nothing after `compute: 64 tiles to do`. The
   parent now logs `compute: n/N tiles done (…s, …GB free)` per tile.
2. **Resumability relied on a single parent-written state file**, which the
   killed run left behind as nothing (`0 already done` on restart). Each tile
   now writes its own marker (`<store>.tiles/p{pass}_t{i}_x{j}.done`) from the
   process that wrote it, so the record is per-tile and atomic.

`finalize` is the recovery path: it re-derives completeness from the store
itself (fingerprint attrs + per-tile record, falling back to full chunk
coverage for stores built before the markers existed), then re-checks the
product invariants by decoding **every** slice, and only then writes
`.meta.json` + `.done`. On this store: 48 slices, **1.52 × 10⁹ cells**,
**0 violations** of {no negatives, threshold ≥ wet-day floor, p99 ≥ p95,
NaN ⟺ pooled wet count < 30}; NaN 96 916 724 cells (**6.4 %** — the
reliability mask), threshold range **1.16 … 863.6 mm**. No recompute was
needed; the 2 h 17 min of compute was kept.

### Sanity numbers (from `python -m ewb_live.climatology.precip_clim regions --doys 15,200`, full JSON in `notes/flood_regions.json`)

Thresholds are mm; `nan` is the fraction of cells with no definable flood
(hyper-arid / too few rain days); `wetn` is the pooled wet-day sample count
(uint8, saturates at 255). doy 15 = mid-January, doy 200 = mid-July.

| region | 1-day p99 med (doy15 → doy200) | 3-day p99 med (doy15 → doy200) | nan frac 1-day (15 → 200) | wetn med (1-day, 15 → 200) |
|---|---|---|---|---|
| amazon | 33.8 → 20.4 | 71.8 → 36.8 | 0.00 → 0.07 | 255 → 255 |
| sahara/sahel | 13.9 → 5.3 | 16.1 → 5.2 | **0.88 → 0.98** | 10 → 0 |
| sahel (monsoon belt) | *all NaN* → 26.4 | 14.4 → 41.7 | 1.00 → 0.03 | 0 → 255 |
| monsoon india | 31.5 → 55.9 | 50.2 → **126.5** | 0.25 → 0.00 | 53 → 255 |
| nw europe | 19.0 → 24.8 | 35.2 → 39.1 | 0.00 → 0.00 | 255 → 255 |
| us gulf | 53.9 → 37.4 | 76.0 → 71.0 | 0.00 → 0.00 | 255 → 255 |
| south china | 28.4 → 73.2 | 47.7 → **143.3** | 0.00 → 0.00 | 255 → 255 |

What these say, and why they are the right sanity tests:

* **The seasonal switch is real and in the right places.** The Sahel has *no
  definable flood threshold in January* (100 % NaN at p95/1-day, pooled
  wet count 0) and a live one in July (26.4 mm p99 daily median, 3 % NaN):
  the estimator is measuring the rain-day distribution, not a constant. India
  and south China triple from January to July; the southern Amazon does the
  opposite (its box is north of the equator, so January is its wet season).
* **Hyper-arid cells blank instead of inventing a threshold.** 88 % of the
  Sahara box is NaN in January and a 3-day lane rescues some of it (54 % NaN),
  which is exactly the intended asymmetry — a rare multi-day desert storm is a
  more meaningful object than a single drizzle day.
* **The absolute floor is doing work.** The Sahara's *defined* p99 daily
  thresholds are still only 14–20 mm (those cells do get convective outliers),
  so without `FLOOR_MM = {1: 20, 3: 50} mm` a desert flash would "exceed" its
  own percentile. The floor is the reason the two-condition test is mandatory.
* **The largest values are oceanic and monsoonal.** Global max 863.6 mm
  (3-day p99, doy 131, 16.75° N 68° E — the Arabian Sea/Somali-Jet ITCZ, the
  most intense convective region in ERA5); ~3 400 cells exceed 400 mm/72 h at
  p99 around doy 125-155, essentially all over the Arabian Sea, Bay of Bengal
  and western Pacific. **`flood-detect` must apply a land mask** (and the
  areal aggregation of §3) or the dashboard will paint the Indian Ocean as a
  flood.

Maps (`notes/flood_maps/`, world + Amazon + Sahara/Sahel + monsoon India, p95
and p99, 1-day and 3-day, doy 15 and 200): `precip_p{95,99}_{1,3}day_doy{15,200}.png`.
The first render of these was misleading twice over, and `write_maps` now avoids
both traps: the store is 0…359.75, so a world panel must rotate the **field**
together with its longitudes (rotating only the axis sheared/blanked it), and a
721 × 1440 `pcolormesh` is 1 M quads whose antialiasing seams rasterise as
**fake zonal stripes** over the smooth ocean — the panels are drawn with
`imshow` on a strictly ascending −180…180 axis instead (`_plot_window` does the
rotation; `test_plot_window_rotates_the_lattice_onto_minus_180_to_180` locks it).
Sanity-check any future map change against the ITCZ/monsoon pattern: bright
band along the summer monsoon trough, NaN over the Sahara/Arabia/Atacama/
Australia.

### Loader smoke test against the real store (`tmp/smoke_loader.py`)

```
precip_threshold        (721, 1440)  mm  mean=15.6 mm nan=0.111
precip_threshold_series (5, 721, 1440) mm  mean=43.5 mm      # p99, 3-day, July
grid='imerg'            (1800, 3600)  lat ±89.95  lon ±179.95
precip_threshold_on     (5, 41, 40)   # arbitrary lattice
wet_sample_count        (721, 1440)  median=255 max=255 (uint8, saturates 255)
scale=0.8               ratio to unscaled=0.800
bad quantile            "quantile 0.9 not in climatology [0.95, 0.99]"
bad window              "accumulation window 2 d not in climatology [1, 3]"
```

That run is also how a **shipped API bug** surfaced: `precip_threshold(date)`
on the default `grid="era5"` raised `target longitudes must be in
[-180, 180)` — the guard rejected the store's *own* 0…359.75 longitude
lattice, so the documented default call path was broken (the synthetic tests
use a tiny 0…1.25° grid and could not see it). Both longitude frames are now
accepted (`_target_indices` reduces mod 360 anyway), locked by
`test_target_indices_accept_both_longitude_frames`. **If flood-detect already
coded against the default path, it was hitting this — pull and re-run.**

### Disk, after the build (owner decision pending)

The ewb root filesystem is now **100 % used, 4.5 GB free** (it was ~11 GB when
I posted §5 ASK). The climatology is 4.5 GB of that. Nothing was pruned. The
disk guard (`MIN_FREE_GB = 6.0`, `STOP_FREE_GB = 2.0`) is what stopped the
post-reboot retry at 02:51 rather than filling the box — it is working as
designed, and it also means **the build cannot be re-run here until ~6 GB is
freed** (candidates in §6). `/dev/shm` is empty (the LXC was rebooted ~01:51,
which is why the 45.5 GB scratch is gone; a re-run re-downloads it in 160 s).

<!-- BUILD_REPORT_END -->

## 6. Open items / for the owner

1. **Disk (owner decision, blocking any rebuild)**: the ewb root filesystem is
   now at **100 % / 4.5 GB free** (it was ~11 GB when this product started;
   the climatology's 4.5 GB is part of the difference). Nothing was pruned.
   Candidates that are *not* mine to touch: `/opt/ewb-live/data/cache` —
   **330 GB** of forecast subset cache (`data/cache/subsets` alone was 230 GB
   at the time of the earlier estimate) — and `/opt/ewb-owb/data/clim2`
   (106 GB of legacy raw 6-hourly t2m inputs, read-only reference for the t2m
   climatology, whose derived product already exists at
   `data/clim/t2m_owb_1990_2019.zarr`). `data/mlc` is 1.6 GB, `data/icechunk`
   0.9 GB, `data/results` 110 MB.
2. `joblib` is imported (like `owb_clim.py`) but is not declared in
   `pyproject.toml`; it comes in transitively today. Someone who owns
   `pyproject.toml` should add it (and whatever `flood-imerg` needs) — I did
   not edit that shared file to keep the merge cheap.
3. A wet-day-conditioned product is what shipped; the all-days variant is one
   flag away (`--wet-min 0`) but doubles the disk. Say so if `flood-detect`
   wants it before the disk is committed.
4. Optional science upgrades, none needed for v1: (a) an IMERG-native
   percentile climatology (§3), (b) a GEV/Gumbel return-period layer for
   "1-in-N-year" wording (§1), (c) a snow/rain partition mask so that
   snow-water exceedances are not labelled floods.
5. **Deploy-dir hazard (for the orchestrator).** `/opt/ewb-live` was last
   synced from this worktree (2026-11-16 17:26), i.e. it runs my *pre-rebase*
   base **plus the owner's `freeze <= 0 °C` patch**, which my rebased branch
   deliberately no longer carries (§channel: it is `ne-mlc-research`'s slice).
   A plain `ops/remote.sh sync` from my branch would therefore silently revert
   that freeze rule on the live box, so I have been updating the box with
   targeted `scp` of `ewb_live/climatology/precip_clim.py` only. Do not let
   any of my worktrees sync the deploy dir until the freeze patch has landed
   on `main` (or is re-applied from `aaTman/ne-mlc-research`).

## 7. Rebuild runbook (if the estimator or reference period changes)

```bash
# 0. need >= 6 GB free on the box's root FS (guard MIN_FREE_GB), and /dev/shm
#    room for the 45.5 GB scratch; check first:
ops/remote.sh status
# 1. build (writes per-tile markers; the parent logs one line per tile)
ssh root@192.168.0.121 'rm -rf /opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr{,.tiles} \
    && rm -f /opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr.meta.json'
ops/remote.sh bg precipclim env OMP_NUM_THREADS=1 python -m \
    ewb_live.climatology.precip_clim build --workers 8 --threads 24
ops/remote.sh logs precipclim -f      # expect "compute: n/64 tiles done (…)"
# 2. close + audit (build writes .done itself if it runs to completion; finalize
#    is the recovery path when a run dies after its writes)
python -m ewb_live.climatology.precip_clim status     # chunk_coverage: missing 0
python -m ewb_live.climatology.precip_clim finalize   # decodes every slice
python -m ewb_live.climatology.precip_clim regions --doys 15,200
python -m ewb_live.climatology.precip_clim maps --dir /tmp/flood_maps --quantile 0.99
# 3. API contract smoke test (this is what caught the lon-frame bug)
ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate && python -' < tmp/smoke_loader.py
```

`finalize` refuses to certify a store whose fingerprint attrs disagree with the
build config, whose tiles are unaccounted for, or which violates the product
invariants — so the gate can never be opened on a partial product.

---

# Part 3 — the detection lane (`flood-detect`): decisions the brief left open

`ewb_live/events/flood.py`. IMERG daily rain aggregated **up** onto the ERA5
0.25° lattice, compared against Part 2's wet-day percentiles plus an absolute
floor, land-only, tracked by the shared footprint/core tracker. The points below
were genuinely ambiguous; each is decided here with the reason, because each one
is a place where a plausible implementation gives a wrong answer.

## 1. The join is a segmented sum in float64, never a cumsum

`aggregate_to_era5_grid()` assigns every 0.1° source centre to the 0.25° target
cell that contains it (nearest-centre on a circle, so the ONE target column whose
box wraps the antimeridian is handled by the same arithmetic as every other),
then sums with `np.bincount(..., weights=…, minlength=…)` per band. The first
implementation used a 2-D cumsum with corner subtraction and was **silently
wrong by ~1 mm on a global field** — float32 cancellation over 12×13-cell sums.
Sums now accumulate in float64 and the result is cast to float32 once; a constant
field must come back exactly, and `test_aggregate_handles_the_seam_column_and_a_
constant_field` pins that. The mean excludes NaN source cells and a target cell
with **any** unobserved contributor becomes NaN (`require_all`): a partly-missing
cell is not a lighter rain rate.

## 2. Land gate = the mask the rest of the product already uses

`ewb_live/viz/build.land_mask` (Natural Earth 110 m through EWB's
`regionmask_land_110`, "0 is land"). Verified rather than assumed:

* it is order-agnostic (descending lat == flipped ascending, 0 cells differ);
* against ERA5's own `land_sea_mask` on the same 721×1440 grid the two differ in
  321k of 1.04 M cells (31 %) — pure coastline-resolution difference (110 m
  vectors vs a ~40 km Gaussian grid), with land fractions 0.332 vs 0.339;
* spot points agree, including the one that looks like an ocean false positive
  at first sight: (-43.75, 169.5) is **not** the Tasman Sea, it is New Zealand's
  Southern Alps — one of the wettest places on Earth (the lane's #3 event,
  576 mm per 72 h on 2026-08-26, is real orography).

A marine-tighter mask would be a defensible product choice, but the flood lane
must not be the only hazard using a different definition of "land"; if the owner
switches to ERA5's `land_sea_mask`, every lane should move together.

## 3. The tracker is fed the CALENDAR-complete sequence

`track_events()` documents "one date per day … complete days only", and its core
is measured in **calendar** days (`age = (day − start).days + 1` vs a per-step
streak). Handing it only the observed days makes both halves of the lane lie:

* `lag=N` on a compacted stack means "N *observed* days", so one IMERG outage
  silently turns a 72 h accumulation into a 4-day one;
* the core decays by arithmetic alone, and an event runs on through a day the
  lane knows nothing about.

So `build_scan` keeps every calendar day of the window, with unobserved days as
NaN rows and an all-False mask. Consequences, both intended: a gap **poisons**
every 72 h window that contains it, and an outage **ends** the open events, which
is what makes the brief's hard requirement a structural guarantee rather than a
check — *no published interval can contain an unobserved day*. The cost is
stated plainly: a genuine 5-day flood with one missing granule becomes two
events. That is the conservative direction, and `details.unobserved_days` is kept
as a tripwire (it must be empty; the test asserts it).

## 4. A flood names the rain that caused it

The 72 h lane flags the day an extreme accumulation **ends**, and the threshold
moves with the day of year — so an accumulation can stop being extreme, then
become extreme again when the percentile steps down, and the flag day can land
two days after the rain. On the live window that is 162 of 766 events, including
one titled "Flood 2026-08-30 to 2026-08-30" whose only in-window rain was 1.04 mm
while 121.69 mm had fallen on the 28th. The interval stays the tracker's, but the
event now carries `rain_peak_daily_mm` / `rain_peak_day` (max daily total, over
the same footprint, across the two days of look-back). `peak_daily_mm` remains the
in-window number, so `peak_3day_mm ≥ peak_daily_mm` stays a strict invariant.
(Argmax detail that only live data showed: NaN must be excluded *before* the
`np.where`, or a NaN in the look-back wins the argmax and the attribution
reports "no rain" — see `test_attribution_survives_an_unobserved_day_in_its_lookback`.)

## 5. Severity is percentile-relative, not millimetres

`(peak_excess_ratio − 1) × peak_area_km2 × days`, mirroring heat/freeze's
"anomaly × area × duration". `peak_excess_ratio` is the largest `obs / p95` over
the event's footprint, with the threshold read on the event's own box (a few zarr
reads per event) instead of holding two global threshold fields in memory. A raw
millimetre ranking would put a p96 day in the monsoon trough above a p99 day in
Provence; the ratio is what makes 60 mm in the Cévennes and 150 mm in Bangladesh
comparable at all.

## 6. Published, never scored

`select.UNSCORED_TYPES = {"flood"}`: EWB has no precipitation-flood benchmark, so
the pipeline records a documented `unsupported` pair status per (event, model)
instead of leaving phantom blockers in `pair_coverage`, and the flood window is
not clamped by the ERA5 truth cutoff (its observations are IMERG, ~1 day from
real time, and the newest days are exactly where a flood lives). `TOP_N_BY_TYPE`
gives flood 30 rather than 15: the 90-day window carries many short-lived events
(live: 766 detected → 30 published).
