# Legacy review — /opt/ewb-dashboard, /opt/ewb-owb, /root/mlwp-icechunk on the ewb LXC

Reviewer: legacy-review (read-only pass, 2026-11-06). All facts below were read from the
box (`cat`/`ls`/`git log`/read-only SQLite/`journalctl`), from the live API at
`:8000`, or from our own repo. No state on the ewb box was modified.
UI screenshots: `notes/screenshots/legacy-dashboard-{main,detail,trend,methodology}.png`.

## Executive summary

- **Bring: the V&V harness concept** (`/opt/ewb-dashboard/verify/`) — a 99-check
  contract-transcription gate (`lib/spec.py` mirrors CONTRACT.md; every verdict row carries
  the command that produced it; UNVERIFIED ≠ PASS). Nothing in our repo gates the dashboard
  against SPEC.md like this. Highest-value transplant.
- **Bring: region labels** — vendored Natural Earth 110m countries geojson + shapely
  point-in-polygon of the event centroid (`pipeline/label_regions.py`, 83 lines, 177-feature
  file). Our event detail/story line has no place names; this is a 30-minute port with high
  user-visible payoff.
- **Bring: DESIGN-OWB.md** — the alabaster token spec with *measured* CVD (ΔE00) and
  contrast ratios per pair, plus rules like "soft tints are backgrounds, never chart strokes".
  Directly reviewable by the design agent as reference; our own token set already diverged
  (we kept dark+light OWB-derived theme), but the measurement discipline is the transferable part.
- **Bring (conditional): the p01/p99 climatology** at
  `/opt/ewb-owb/data/clim2/t2m_p01_p99_1990_2019.zarr` (5.3 GB, EWB-native layout) — see §2.
  It cannot replace EWB's 0.85/0.15 defaults (SPEC says EWB defaults, and this store has no
  0.85/0.15), but it is a drop-in "severe tier" extension and a basis to *validate* EWB's
  shipped climatology. Product decision needed.
- **Bring (patterns, not code): store-side lessons from /root/mlwp-icechunk** — the
  virtualizarr ≤2.8 partial-edge-chunk mis-offset hazard (must gate before ever building
  `FOUR_v100_GFS` in our store), flock + stall-watchdog, and snapshot-expire/GC for a
  long-lived repo. Our store's design (init/lead axes, era-groups, known-bad ledger, weekly
  single commit) is strictly better for EWB work; we keep ours.
- **Don't bring: the legacy detection semantics.** p99/p01 on daily max/min (not EWB's
  85/15 on 6-hourly t2m), 1° lattice, 6-hourly IVT with an inert Laplacian term, 8-connected
  daily labelling, land-only AR masks. All quantified as defects in their own FINDINGS.md;
  our pipeline follows upstream EWB and does not share these problems.
- **Don't bring: any of their runtime.** FastAPI/SQLite/6-h-refresh stack, `events.db`,
  the leaderboard frontend internals. EWB Live replaces the whole product; the DB schema is
  incompatible with our events.json/scores.json contracts.
- **Open question (owner): EWB's shipped climatology is unvalidated by anyone.** We could
  rebuild 0.85/0.15 from the ewb-owb raw annual zarrs (102 GB already paid for in GCS
  downloads) and diff against `gs://extremeweatherbench/...climatology.zarr` — a real
  trust artifact for METHODOLOGY. Cost: compute on the LXC + a grid-convention diff.
- **Open question (owner): the legacy box keeps running** (6-h refresh, ~8 min CPU/cycle;
  mlwp cron is now redundant with our store and its backup step fails daily, leaving
  1.4 GB uncompressed tars/day in `backups/`, 16 GB so far). Replacement plan and cleanup
  (including an SSH key HANDOFF says to remove) are the owner's calls, not ours.
- **Open question (owner): long-history backfill.** Their store references the full MLWP
  archive back to ~2020 (31,441 files, ~267 TB virtual); ours is a rolling 120-day window.
  If the product ever wants "events + scores beyond 90 days", the cheap path is extending
  our `--days`, not adopting their store — but the decision is a scope call.

---

## 1. /opt/ewb-dashboard (being replaced)

### What it is
A trailing-90-day extreme-weather leaderboard: heat waves, freezes, atmospheric rivers
detected from ERA5 (ARCO) only — no model scoring at all. FastAPI + SQLite
(`data/events.db`, rebuilt wholesale every 6 h by `ewb-refresh.timer`, ~475 s/run, reads
~25 min of IVT unless the per-window `data/ivt_*.npz` cache hits) + a vanilla-JS SPA on
port 8000. Detection runs on a **1° lattice** (every 4th point of the 0.25° grid) to bound
cost; 90-day IVT is hourly-untenable so ARs run at 6-hourly cadence. Still live at review
time: 1073 events, window 2026-08-02..2026-10-30, 290 HW / 11 FZ / 772 AR.
Definitions differ from ours: heat = daily max t2m > **p99**, freeze = daily min < **p01**
(computed in-house, ±15 d pooled window, 1991–2020, because EWB's shipped climatology
tops out at 0.90/0.10) — a deliberate deviation from EWB defaults that the CONTRACT chose
up front.

### History (8 git commits, 2026-10-21..23)
Built by a multi-agent "village" session (orchestrator + backend/frontend/Python-expert/design
workers, tasks T1–T5). Early commits got the pipeline running and the OWB restyle in;
the last three are a **verification + findings freeze**: a 99-check harness, four Python
patches as reviewable artifacts, and FINDINGS.md, before "import to the village repo".
The tree was then handed over and superseded by EWB Live. No remote; it is a frozen
evidence tree, not a moving codebase.

### What worked (worth transplanting)
1. **`verify/` harness.** `check.sh` runs 99 checks against `lib/spec.py`, a transcription
   of CONTRACT.md + OWB-REDESIGN.md; verdicts PASS/FAIL/UNVERIFIED/INFO, each row carries
   the exact reproducing command; reports stamped with mtimes of what was checked. It
   actually caught contract drift (AR rows serving `mean_intensity_c` holding kg/m/s;
   missing contract metric keys; daily-series gaps; `aria-sort` on the wrong role).
   Concept transplant: a `spec.py` mirroring our SPEC §2/§4 JSON contracts + checks over
   `data/results/*.json` and the served dashboard.
2. **`pipeline/proposals/FINDINGS.md` evidence discipline.** Build-stamped numbers ("re-query,
   do not quote"), an explicit "corrections to the premises" section that documented their
   own docs being stale vs code (the 50%-of-peak termination *was* implemented; HANDOFF/README
   were wrong), superseding unreproducible numbers with instrument-owned ones
   (`analysis_*.py` reproduce the live AR table exactly from the cached IVT field). And a
   good engineering decision: they refused to ship the AR threshold fix as a silent patch
   because it redefines a served contract column — flagged as a contract decision instead.
3. **Region labels** (`label_regions.py` + vendored `ne_110m_admin_0_countries.geojson`):
   centroid point-in-polygon → "S. Sudan (Africa)" style labels; the live UI leans on them
   (leaderboard, win-streak cards, search). Our dashboard has bbox + centroid but no names.
4. **Methodology page discipline** (`docs/METHODOLOGY.md`): every number either served by
   the API or a labelled source constant, nothing else printed; honest caveats written for
   readers ("'live' means rebuilt 5.6 h ago from input that ends 6 days ago"; the daily
   series counts *peaks*, not exposure). Exactly the trust posture our truth-pill work wants.
5. **Operational shape:** wholesale DB rebuild + per-request reads (no restart on data
   change), per-window IVT npz cache to avoid re-fetching 88 GB, `EWB_DATA_DIR` smoke-test
   pattern with cheap `--years 3`/`--days 10` passes before the expensive run.
6. **Small hard-won traps documented** (most already in our notes/skills, confirmed
   independently here): `https://storage.googleapis.com/...` opens an ARCO zarr with zero
   variables and no exception (guard in `common.open_zarr_store`); zarr 3.4 codec registry
   rejects inherited numcodecs-Blosc encodings on `to_zarr` — write the daily cache as
   NetCDF4 instead; descending latitude; 0–360 lon; banker's-rounding marching-squares
   produces degenerate/2×-area polygons for thin objects (their fix: union-of-cell-boxes,
   escalate-tolerance simplification, bbox fallback instead of NULL).

### What failed / was abandoned, and why
- **Non-EWB thresholds by design** (p99/p01 daily max/min): their own README admits EWB's
  shipped climatology has no 0.99/0.01, so they rolled their own — a defensible product
  choice that makes their numbers incomparable to EWB cases. Our pipeline uses EWB's
  85/15 on 6-hourly t2m; we do not want this.
- **1° grid + 6-hourly IVT killed the AR mask.** Measured in FINDINGS §3: at 1°,
  `|blurred Laplacian| ≥ 2.5` covers 79% of the globe, so the EWB mask degenerates exactly
  to `IVT ≥ 400`; the min-object-size is the only thing bounding ARs. Absurd areas follow
  (max 2.97 M km²; ocean+land variant hits 24 M km² basin-crossing blobs and needs a real
  segmentation algorithm — recorded as a scope decision, unresolved).
- **8-connected daily labelling** (vs EWB's 4-connected): corner-touching merges inflate
  area/duration, and — insidiously — feed merged cell counts to the 50%-of-peak decline
  test, suppressing the termination rule the docs *thought* was missing. Also a 30.1%
  geom-vs-area disagreement because peak polygons were traced 4-connected while tracked
  objects were 8-connected. Our events code follows upstream; keep it that way.
- **Land-only AR detection** (`mask &= land_frac>=0.5`) misuses EWB's
  `build_atmospheric_river_mask_and_land_intersection` — landfall is a product, not a
  filter; contract keys `land_intersection_area_km2`/`landfall_angle_deg` were never emitted.
- **Unit bug in a served column**: AR `threshold_value = 126.85` = 400 − 273.15, a K→C
  conversion applied to a flux. Caught only by the harness's unit-consistency check.
- **Docs-vs-code rot** at the top of the HANDOFF list (claims contradicted by the shipped
  code) — the meta-failure: handoff prose drifted within *days* in a multi-agent session.
  Transplant lesson: keep claims pointing at instruments, not at prose.
- **Never integrated MLWP/model data** — the thing the product is actually about; their
  dashboard remained an observations-only leaderboard. Nothing to salvage there.

### Reusables ranked by value
1. `verify/` harness pattern → adapt to our data/results contracts (ops + design).
2. Region labels: `label_regions.py` + the vendored geojson → `ewb_live/events/` curation
   attrs (small, contained; keep upstream scan untouched, add as a post-scan attribute).
3. DESIGN-OWB.md + OWB-REDESIGN.md as design reference (tokens, CVD measurements,
   "answer-first" mapping table OWB→events).
4. METHODOLOGY.md writing pattern for our methodology/tooltip copy.
5. `analysis_*.py` idea: scripts that reproduce live numbers from caches (their AR replica
   reproduced builds exactly) — our offline regression tests are the analogue; keep
   extending that habit.
6. Their IVT npz cache naming/window pattern if our AR rescans show the same re-read cost.

### Things to avoid
- Any import of `pipeline/` or `app/` code: it's a 1° grid, different physics, different
  contracts.
- Quoting their event counts/scores anywhere as comparable to EWB-based numbers.
- Hand-assembled patch files as an inter-agent artifact (their own root-cause of rejects;
  `make_cumulative.py` was the fix).
- Letting HANDOFF-style prose become the source of truth — verify against code/history.

## 2. /opt/ewb-owb (ERA5 climatology + LLM ticket runner)

### What it is
No git, one session, 2026-10-27. `clim.py` (≈170 lines, clean, self-contained):
`fetch` pulls ARCO 0.25° 6-hourly (00/06/12/18Z) `2m_temperature` year-by-year into local
zarrs — `data/clim2/t2m_6h_{1990..2019}.zarr`, 30 × 3.4 GB ≈ 102 GB, all `.done`;
`quantiles` tiles the grid, pools raw values within ±10 days of each day-of-year across
all 30 years, and writes per-(doy, hour, cell) **p01/p99**:
`data/clim2/t2m_p01_p99_1990_2019.zarr`, 5.3 GB, shape `(quantile=2, dayofyear=366,
hour=4, lat=721, lon=1440)` float32, zarr v3, latitude descending, lon 0–360 —
**layout deliberately matching EWB's shipped climatology**. `data/clim` is an abandoned
first attempt (near-empty).
`qdispatch.py`: dispatches self-contained code tickets (JSON: prompt/out/max_tokens) to
the fleet vLLM (`qwen38-flash-next` at 192.168.0.175:8080), temperature 0, thinking off,
accepts only exactly-one-fenced-code-block replies, pytest-driven acceptance
(`test_q001.py`). A tiny offline code-gen harness; nothing dashboard-relevant, the pattern
is mildly interesting.

### Is the climatology useful to us? (the real question)
Upstream EWB: heat waves default to **quantile 0.85** (daily series vs hour-of-day
climatology), freezes **0.15**; `VALID_QUANTILES = [0.10, 0.15, 0.25, 0.50, 0.75, 0.85,
0.90]`; shipped store `gs://extremeweatherbench/datasets/surface_air_temperature_1990_2019_climatology.zarr`,
shape `(7, 366, 4, 721, 1440)` float64 zarr v2 on the same 0.25° descending/0–360 grid —
verified live by fetching its `.zarray`. Our pipeline reads it via
`defaults.get_climatology(q)` (lazy, bbox×doy subsets — per our skill). EWB's own builder
(`data_prep/era5_climatology.py`) applies a **triangular-weighted rolling mean**
(±`half_window_days`) to the time series *before* the per-(doy, hour) quantile; ewb-owb
instead pools **raw** 6-hourly values in a uniform ±10-day window. Same axis layout,
different estimator.

Assessment:
- **Replace EWB's climatology for our defaults: no.** It lacks 0.85/0.15 (and everything
  except p01/p99), and SPEC binds us to EWB defaults. Method differences (raw-pool vs
  weighted-rolling) would also make threshold fields non-EWB.
- **Extend: yes, cleanly, if the product wants a severe tier.** p99/p099-style severity
  tiers are exactly the WindBorne "percentile TIER" presentation our design notes already
  reference. Because the layout mirrors EWB's store, it plugs into the same
  `defaults.get_climatology(q)`-style accessor with quantile 0.01/0.99 — a documented
  deviation, one 5.3 GB copy from the ewb box, no recomputation.
- **Validate: genuinely valuable.** Nobody has cross-checked the shipped EWB climatology.
  The raw annual zarrs are already downloaded; building 0.85/0.15 both EWB-way and pooled-way
  and diffing against the shipped store gives us (a) confidence in the threshold field our
  event masks and viz depend on, (b) a defensible methodology claim. Compute-on-LXC task
  (HARD RULE: no EWB compute here), ~1–2 h class.
- **Cost side:** 102 GB raw on the ewb box (disk there is fine: 492 GB, 29% used). If we
  adopt severe tier, copy only the 5.3 GB quantile store; the annuals stay as the cold
  asset for the validation build.

### Things to avoid
- `clim.py` assumes zero NaNs in global t2m (raises otherwise) — fine for ARCO t2m, but
  don't reuse for other variables without a mask path.
- The quantile tile worker writes with `region=` against a template built with
  `da.zeros` — a crashed run leaves a partially-zero store with no `.done`; check
  `.done` before trusting any store we copy.

## 3. /root/mlwp-icechunk (existing CIRA MLWP Icechunk store)

### What it is
Virtual (zero-copy) Icechunk store over the **whole** `s3://noaa-oar-mlwp-data` archive:
40,950 enumerated keys, 31,441 ingested (~267 TB referenced), 9,495 excluded (`Derived/`),
1 **duplicate** (same ETag+size republished under another model dir — deduped), **13
quarantined** — the same 13 upstream-truncated IFS-forced files our own survey found and
ledgers as `known_bad`. Store = 1.4 GB metadata at `store/mlwp.icechunk`. Daily 06:00 cron
(`scripts/update.py`: sync → magic-byte QC → flock'd resumable ingest in ≤100-file commits
→ metadata-only tar backup, last 4 kept; snapshot expire + `garbage_collect` every 8
windows). Covers 8 live models back to ~2023 plus retired `FOUR_v100_GFS` (2020–2023-10,
via per-file groups). No git repo; CHANGELOG.md is the history (3 sessions, 2026-10-22..27).

### Design vs our store (`notes/mlwp-store.md`, `ewb_live/stores/`)
| axis | theirs (ewb) | ours (EWB Live) |
|---|---|---|
| primary axes | `time` = **valid time**, with `init_time`/`lead_time` as 1-D coords; deliberately non-monotonic across files | `(init_time, lead_time)` real dims; 2-D `valid_time` coord — EWB-convention native; theirs is awkward for EWB's init×lead selection |
| regime/era handling | groups `MODEL__YYYYMMDD` per structural regime (same May-2025 chunk-change conclusion we reached independently — good cross-validation) | same idea: era groups + lazy concat in `open_model()` |
| edge cases | per-file groups `MODEL__F__YYYYMMDDHH` for files whose compressed partial edge chunks can't tile zarr cells (`FOUR_v100_GFS`) | refused/not built (outside 120-day window); builder keeps source chunk grid |
| ledger | sqlite state (pending/ingested/duplicate/quarantined with error text, never silently dropped) | known-bad ledger in root-group attrs, 14-day retry, converges weekly update |
| update | daily cron, batches of ≤100 files, multi-commit | weekly (weekly.py step 1), plan → append/rewrite/idempotent-no-op, exactly one commit, staged build, self-heal |
| history | full archive ~2020→now | rolling 120 days |
| hygiene | no git; daily cron **fails at backup** (`zstd` binary missing → traceback after ingest, exit≠0 daily; uncompressed 1.4 GB tars accumulating, 16 GB so far) | git-tracked, tested (13/13), verify script |

Verdict: ours wins on conventions, update semantics and ops hygiene; theirs wins on
historical reach and a couple of hard-learned integrity gates. We keep ours (per task
instruction); adopt their lessons:

1. **virtualizarr ≤2.8 silently mis-offsets virtual refs** when a compressed partial edge
   chunk must share a zarr grid cell — detected only by value spot-checks vs source, not by
   exceptions (their CHANGELOG 2026-10-27; earlier commits were corrupt and were deleted).
   Our live-model build is immune (store chunks = source chunks, exact tiling), but if we
   ever build `FOUR_v100_GFS` or any non-tiling-chunk model, we need their
   parse-time `partial_chunks()` gate + per-file-group fallback + spot-check validation
   (`scripts/mlwp_verify_store.py` already compares values — extend it, don't trust
   successful commits).
2. **Snapshot expire + GC cadence** for a long-lived repo (they measured ~6 MB/file store
   growth without it). Our weekly commits on `main` will age similarly over a year — add a
   periodic expire+GC to `weekly.py` or an ops runbook step.
3. **flock + stall watchdog** (240 s stall → pool rebuild) for the reference-generation
   process pool — cheap insurance for the unattended weekly run.
4. **Duplicate-by-ETag handling** as a documented no-op for us (per-model init keys make it
   moot today; worth a line in the store notes so a future backfill checks it).
5. Their **full-archive reach** is the answer if the product ever asks for >120-day
   history: extend our `--days` (era-grouped design supports it; `FOUR_v100_GFS` caveat
   above applies) rather than federating two stores.

### Things to avoid
- Do not read from their store for EWB Live: valid-time primary axis + different commit
  lifecycle, and it lives on a box we're replacing. Treat it as a sibling artifact.
- Don't copy their backup scheme: the tar-the-whole-metadata daily pattern is exactly what
  broke (uncompressed accumulation, silent cron red).

## Dispositions (proposed)
- `/opt/ewb-dashboard`: leave running until EWB Live is live on this box, then archive the
  tree (it contains FINDINGS/verify/DESIGN evidence we reference) and stop the timer.
  Owner decision.
- `/opt/ewb-owb`: keep the box + `data/clim2` as the climatology cold asset pending the
  severe-tier/validation decisions; nothing else to reuse.
- `/root/mlwp-icechunk`: keep as-is (it still feeds nothing of ours after cutover); the
  `zstd` cron-red and eventual shutdown are owner decisions.

## DONE
- Reviewed all three systems strictly read-only (files, git log, read-only SQLite,
  journalctl, live API GETs, read-only zarr metadata over HTTPS, read-only sqlite URI).
  Nothing on the ewb box was written, run, or restarted. Observed (untouched): infra agent
  mid-rsync of EWB Live to `/opt/ewb-live` on the same box.
- Report above; screenshots under `notes/screenshots/legacy-*.png`; summary posted to
  `notes/channel_infra.md`; design agent nudged.
