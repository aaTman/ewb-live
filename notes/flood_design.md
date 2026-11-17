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
