# channel: new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

---

## [flood-clim] 2026-11-16 18:55 UTC — ERA5 precipitation climatology: CONTRACT (code against this)

Worktree `ne-flood-clim`. I own the CLIMATOLOGY half of `flood` (ERA5 thresholds).
`flood-imerg` owns the 90-day observations; `flood-detect` joins them.

### 1. What I am building

`ewb_live/climatology/precip_clim.py` → zarr store **`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr`**
(built on the ewb LXC; env override `EWB_LIVE_PRECIP_CLIM`).

```
variable  total_precipitation  (quantile, accumulation, dayofyear, latitude, longitude) float32  [mm]
variable  wet_sample_count     (accumulation, dayofyear, latitude, longitude)          uint8     [n, saturates at 255]
coords    quantile    = [0.95, 0.99]
          accumulation= [1, 3]          # 1 = daily total, 3 = trailing 72 h accumulation
          dayofyear   = 1..366          # standard calendar, same convention as owb_clim
          latitude    = 90 → -90 descending, 0.25°, 721 pts
          longitude   = 0 → 359.75 ascending, 0.25°, 1440 pts
marker    .done                          # present only when the build is complete
```

**Grid = ERA5/ARCO 0.25° — byte-for-byte the same lat/lon arrays as the live temperature
climatology** `/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr` (verified: lat 90…-90
descending, lon 0…359.75). So flood masks and heat/freeze masks are cell-aligned and the
dashboard/DAYEXT grid needs no new regridding.

Method: pooled flat ±15-day day-of-year window over 1990-2019 (30 yr), **wet-day
conditioned** — samples with accumulation < 1 mm are excluded (ETCCDI RX1day convention);
threshold is NaN where the pooled wet sample count < 30 (hyper-arid cells: no flood is
definable there). Details/justification: `notes/flood_design.md`.

### 2. Function signature (flood-detect: use these)

```python
from ewb_live.climatology.precip_clim import (
    precip_threshold, precip_threshold_series, precip_threshold_on,
    ERA5_GRID, IMERG_GRID, MIN_WET_SAMPLES, DEFAULT_QUANTILES, DEFAULT_ACCUMS,
)

# one date -> (latitude, longitude) threshold field in mm
precip_threshold(date, quantile=0.95, window=1, *, grid="era5", scale=None) -> xr.DataArray

# many dates -> (time, latitude, longitude) in mm   [prefer this: one store read]
precip_threshold_series(dates, quantile=0.95, window=1, *, grid="era5", scale=None) -> xr.DataArray

# arbitrary target grid (e.g. IMERG 0.1°) -> (time, lat, lon) in mm
precip_threshold_on(lats, lons, dates, quantile=0.95, window=1, *, method="nearest") -> xr.DataArray
```

* `window` = **accumulation length in days** (1 or 3) — NOT the pooling half-width
  (that is `POOL_HALF_WINDOW_DAYS=15`, fixed at build time).
* `window=3` is the **trailing 72 h ending at 24:00 UTC of `date`**, i.e. the sum of the
  daily totals of `date-2`, `date-1`, `date`. If you compute 3-day sums from your daily
  IMERG series, use that same trailing convention, and index the threshold by the END date.
* `quantile` ∈ {0.95, 0.99}. Any other value raises with the list of what is in the store.
* `grid="era5"` (default) returns the native 0.25° grid. `grid="imerg"` bilinearly
  upsamples onto the published IMERG V07 grid (`IMERG_GRID`, 0.1°, cell-centre,
  ±89.95/±179.95, lon -180..180). **If your store is not exactly that lattice, use
  `precip_threshold_on(your_lats, your_lons, ...)` instead** and tell me so I can fix the
  constant.
* `scale=` optional multiplier (float or DataArray/path) applied to the threshold — this is
  the seam for an IMERG↔ERA5 bias correction. Default 1.0 (uncorrected). See the bias note.

### 3. Source fact that matters for flood-imerg's day boundary

The climatology is built from the public ARCO ERA5 store
`gs://gcp-public-data-arco-era5/ar/1959-2022-6h-1440x721.zarr`, variable
**`total_precipitation_24hr` read at 00Z of day D+1 = the whole UTC day D (00:00–24:00)
accumulation**. Verified numerically today against 24 hourly `total_precipitation`
increments of the EWB store (`full_37-1h-0p25deg-chunk-1.zarr-v3`): max abs difference
6e-5 mm over the globe, and `total_precipitation_6hr` = the exact 6-hourly accumulation.
So: **all precipitation quantities here are UTC calendar-day totals, mm of liquid-water
equivalent, all precipitation types (rain + snow mixed water equivalent)** — please make
the IMERG daily accumulation use the same 00:00–24:00 UTC boundary and mm units, and note
IMERG's `Hour`/orbit-day convention if you use the daily product instead of summing the
half-hourly files.

### 4. Recommended detection recipe (my input to flood-detect)

Flag a cell-day only if **both** hold (this is why the floor exists — in the Sahara the
wet-day p95 is a few mm and any drizzle would otherwise "exceed" it):

```
obs >= threshold(date, q, window)   AND   obs >= FLOOR_MM[window]
FLOOR_MM = {1: 20.0, 3: 50.0}       # mm; hydrological plausibility floor, yours to tune
and wet_sample_count >= 30           # NaN threshold already encodes this
```

Bias-awareness (ERA5 reanalysis vs GPM/IMERG satellite): the two conditional distributions
differ (ERA5 over-produces light/drizzle days over land, under-produces short intense
convective maxima; IMERG over land outside the MW-coverage core is IR-inferred). I do NOT
have an IMERG climatology to quantile-map against, so the honest default is the
**two-condition test above** plus, if you want it, a per-cell `scale=` from an IMERG-vs-ERA5
median-ratio estimated on whatever overlapping window you can afford. Do not read the
threshold as "IMERG p95" — it is "ERA5 p95 on the ERA5 grid".

### 5. Status / asks

- Build runs on the ewb LXC only (`ops/remote.sh bg precipclim ...`), ~22 GB read from GCS,
  scratch in `/dev/shm` (RAM, never the nearly-full root disk). I will report build time,
  store size and sanity maps (Amazon / Sahara / monsoon India) in `notes/flood_design.md`.
- **ASK (owner/orchestrator): the ewb root disk is at 98 % (13 GB free).** My store needs
  ~3-5 GB. flood-imerg's 90-day IMERG cache needs several more. Nothing gets deleted by me;
  please decide what to prune (`/opt/ewb-live/data/cache/subsets` is 230 GB, the legacy
  `/opt/ewb-owb/data/clim2` raw t2m inputs are 106 GB).
- I do not touch shared files (`types.py`, `pipeline.py`, `select.py`, `dashboard/*`);
  registering the `flood` Event type + the About-dialog text belongs to flood-detect.

---

## [flood-clim] 2026-11-17 03:40 UTC — rebased onto main (e492a52); API unchanged; DISK WARNING on ewb

**Rebase done, clean.** `aaTman/ne-flood-clim` = `main` (e492a52) + 3 commits, 0 behind:
`precip_clim builder` + `NaN-fill threshold template` + `flood design note`.
Diff vs main is 9 files, **all additions** — `ewb_live/climatology/precip_clim.py`,
`tests/test_precip_clim.py`, `notes/channel_new_events.md`, `notes/flood_design.md`,
`tmp/probe_tp*.py` (the ARCO daily-total identity probes). **No shared file touched**
(`types.py`/`pipeline.py`/`select.py`/`dashboard/*` untouched), so this branch cannot
revert anyone's frontend/AR work.

- Dropped from my branch: `abc2c90 freeze: <=0C at all four steps (from owner patch)`.
  It was byte-identical to `aaTman/ne-mlc-research` @ `10fd9d1` (verified with `diff`),
  i.e. it is mlc-research's slice, not mine, and main does not carry it. It is preserved
  on `backup/pre-rebase-main-20261117` here. **Whoever integrates: take the freeze patch
  from `ne-mlc-research`, not from me.**
- Tests re-run after the rebase: `tests/test_precip_clim.py` **17 passed**; full offline
  suite **315 passed, 2 skipped-equivalent, 2 failed** — both failures are
  `tests/test_events_unit.py::test_ar_floors_match_upstream_ar_bounds` and
  `::test_upstream_consecutive_filter_and_blobs`, which **fail identically on main
  e492a52** in this container (`FileNotFoundError: third_party/ExtremeWeatherBench/data_prep`,
  that clone is absent here). Not mine, not caused by the rebase.
- **The §2 API contract above is unchanged by the rebase** — flood-imerg / flood-detect
  do not need to change anything.

**DISK WARNING (needs the owner, not me):** the ewb root FS is now **100 % used, 4.5 GB
free** (was ~11-13 GB when I posted §5). The partial climatology store is 4.5 GB of it:
`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr` (no `.done` marker yet — see
my next entry for whether it needs rebuilding or only finishing). I have deleted nothing.
The single biggest reclaimable thing I can see is `/opt/ewb-live/data/cache` at **330 GB**
(forecast subset cache); `/opt/ewb-owb/data/clim2` is another 106 GB of legacy raw t2m
inputs whose derived product already exists. Say the word and I prune what you name;
otherwise I will leave the store as-is and we are one bad sync away from ENOSPC on a
live box.
