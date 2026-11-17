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

<!-- BUILD_REPORT_PLACEHOLDER -->

## 6. Open items / for the owner

1. **Disk**: the ewb root filesystem is at 98 % (~11 GB free at the time of
   writing). This product needs ~3-5 GB. Nothing was pruned. Candidates that
   are *not* mine to touch: `/opt/ewb-live/data/cache/subsets` (230 GB of
   forecast subset cache), `/opt/ewb-owb/data/clim2` (106 GB of legacy raw
   6-hourly t2m inputs, read-only reference for the t2m climatology — the
   derived product already exists at `data/clim/t2m_owb_1990_2019.zarr`).
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
