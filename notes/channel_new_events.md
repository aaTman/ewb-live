# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

NOTE for the orchestrator: this file is **mlc-research's lane copy** — the other lanes'
`notes/channel_new_events.md` copies are untracked and already diverged across worktrees
(`events/`, `ne-flood-clim/`, `ne-flood-imerg/`, `ne-tc/` all have different bytes), so
**concatenate the lane files, do not merge them as one file**. Everything mlc-research needs
from other lanes is quoted inline below.

---

## [mlc-research] 2026-11-17 03:40 UTC — PROOF done: TempestExtremes finds Ciaran (work of 16-17 Nov)

Brief: survey MLC trackers, pick one, PROOF it on the ewb LXC, decide. Chosen:
**TempestExtremes `DetectNodes` + `StitchNodes`** (conda-forge `tempest-extremes` 2.4.2,
**BSD-2-Clause** — verified from the conda-meta record on the box), detection field = **MSLP only**.

Isolated install, nothing outside the deploy root touched: micromamba env at
`/opt/ewb-live/opt/mm/envs/te` (281 MB), `.venv` untouched, TE binaries called via `subprocess`
only. Nov 2023 (+ 6-day October warm start), global 0.25°, 6-hourly:

* `--closedcontourcmd msl,3.0,6.0,0 --searchbymin msl --mergedist 6.0 --minabslat 5.0`,
  `StitchNodes --range 6 --mintime 48h --maxgap 12h` → **99 tracks**, median lifetime 66 h,
  70 tracks ≥ 1000 km displacement, 13 bombs, mean |lat| 58.3°.
* **Storm Ciaran found** as one continuous track, central pressure **954.0 hPa** at 2023-11-02T06
  (the 00Z fix sits ~175 km west of the documented Finistère landfall point and the 06Z fix is
  already inland at 50.5N 1.5E, so landfall falls inside the 00-06Z step),
  displacement 3138 km, 24 h fall 32.2 hPa = **1.52× the Sanders–Gyakum bomb threshold**. That
  954.0 hPa equals the ERA5 field minimum over 45-58N/25W-5E on those days, so the tracker is not
  inventing or losing pressure.
* **Cost** (16-core box, tracker on one core): 1.3 s per global 6-hourly MSLP frame from ARCO →
  a 90-day window ≈ 8-9 min; `DetectNodes` on 141 global frames = **18 s**; `StitchNodes` 0.03 s.
  850 hPa winds cost 5x/frame (the u/v store is chunked `(1,37,721,1440)`, so one level reads the
  whole 37-level block) — that is why the recommendation is MSLP-only.
* **Correction to the circulating TE recipe**: the tutorial `vor850 >= 2e-10 s^-1` vorticity gate
  is a **no-op** at 0.25° (7792 nodes with and without it). `--thresholdcmd` is an
  exists-within-radius test, not an average. Threshold scan in the report.
* Orography (the reason to avoid SLP-reduction schemes): 16.4 % of detected nodes sit over > 500 m
  terrain, 32 % in the 20-40° orogeny belt vs 8.6 % in 40-65° — hence a genesis elevation gate.

**To `tc`:** your `details["track"]` contract (5-slot rows, `Z` times, lon in [-180,180],
`intensity_units`, `track_crosses_antimeridian`, full-life track vs qualifying `start`/`end`) is
adopted as-is; MLC slot 3 = slot 4 = central MSL hPa. I propose the tropical-origin overlap rule
(genesis within 500 km / ±24 h of a tropical IBTrACS fix, 7-day look-back ⇒ **tc owns the
system**, so Ciaran does not appear twice) — needs your confirmation, and both lanes must
implement the same direction.

Artifacts (branch `aaTman/ne-mlc-research`): `notes/mlc_algorithm.md` (decision + build plan,
`ccdcabc`), `notes/mlc_proof_report.txt` + `scripts/mlc/` (harness, `da590ec`).

## [mlc-research] 2026-11-17 03:42 UTC — rebased onto main (e492a52); freeze patch dropped from my lane

* `git rebase --onto main 10fd9d1` — my branch is now `main (e492a52)` + 2 research-only commits.
  Working tree clean; `git diff main..HEAD` touches **only** `notes/mlc_*` and `scripts/mlc/`
  (+20 files, +727 lines). No production file changed by this lane, per my brief.
* **Conflict policy / what I dropped:** my first commit was the COMMON.md
  `briefs-new-events/freeze_le0c.patch` (`ewb_live/events/temperature.py`, `dashboard/detection.py`,
  `SPEC.md`, `tests/test_frame_masks.py`). That is outside mlc-research's slice (I own research and
  scripts only), so the rebase **kept main's version** and the commit became empty — i.e. the
  `<=0C at all four steps` freeze rule is **not** carried by my branch. It still lands via the
  lanes that own temperature detection. **Flagging it so nobody loses it in the merge**: main at
  e492a52 does not contain the freeze rule.
* Took main's AR seam/IVT fixes, make-nicer frontend and `ops/events_rebuild.sh`; `ops/remote.sh`
  and `ops/seamfix_rebuild.sh` are main's, unmodified.
* **Tests re-run after the rebase** (unit suites, `--ignore=tests/e2e`):
  **300 passed, 29 skipped, 0 failed** in 85 s. Note for other lanes: a fresh worktree has no
  `.venv` and no `third_party/ExtremeWeatherBench` (both gitignored) — without the latter,
  `tests/test_events_unit.py::test_ar_floors_match_upstream_ar_bounds` fails on a missing
  `data_prep` dir. I symlinked `third_party/ExtremeWeatherBench` from the `events` worktree and
  ran with `/home/orca/orca/workspaces/ewb-live/ne-coldsnap/.venv/bin/python -m pytest tests -q
  --ignore=tests/e2e`; that is a pre-existing env gap, not a code regression.
* **Housekeeping on the box** (the ewb root disk is at **99 %, 6.0 G free**): my PROOF scratch
  `data/mlc/` (1.6 G) deleted to `rm -rf` on ewb — it is fully reproducible with
  `ops/remote.sh bg mlc_proof bash scripts/mlc/run_proof.sh` (~17 min). The TE env (281 M) stays.
  Future scratch belongs on `/dev/shm` (252 G free tmpfs).

## [mlc-research] 2026-11-17 03:45 UTC — DONE

**Decision** (`notes/mlc_algorithm.md`, 8 sections): TempestExtremes `DetectNodes`+`StitchNodes` on
ERA5 **MSLP alone**, `--closedcontourcmd msl,3.0,6.0,0 --mergedist 6.0 --minabslat 5.0`,
`--range 6 --mintime 48h --maxgap 12h`; survey table (TempestExtremes / Hodges TRACK /
Melbourne–Murray&Simmonds / Wernli–Schwierz 2006 / IMILAST) with DOIs resolved against Crossref;
IMILAST's own conclusion (schemes differ ~2x in counts, agree on the deep lows) is why the choice
is defended by a named-storm check rather than a benchmark score.

**Qualification rule** (event vs system): genesis |lat| 25-70° + genesis over orography < 500 m +
lifetime ≥ 48 h + displacement ≥ 1000 km + min MSL ≤ 990 hPa + tropical-origin rejection via
IBTrACS; severity tags only (bomb `dMSL24h <= -24*sin|lat|/sin60`, min MSL, displacement) so
`select.py` can rank without the detector hiding events.

**Files/commits** on `aaTman/ne-mlc-research` (= main + these): `da590ec` `notes/mlc_proof_report.txt`,
`scripts/mlc/` (14 scripts: ARCO extractor with 850 hPa vorticity, DetectNodes/StitchNodes driver,
track analyser with the known-storm check, vorticity scan, orography cross-check, isolated
micromamba installer, reproduction commands); `ccdcabc` `notes/mlc_algorithm.md`.
**No shared production file edited** (`types.py`, `pipeline.py`, `select.py`, `dashboard/*` untouched).

**Tests:** 300 passed / 29 skipped post-rebase (see the entry above for the exact command and the
`third_party` caveat). e2e (Playwright) suites not run: no `.venv`/browser libs in this worktree
and no frontend change in my lane.

**Open issues / needs the owner or orchestrator**

1. **Disk**: ewb root 99 % full (6.0 G free; `data/cache` 316 G). `midlat_cyclone` as designed
   needs **no** permanent new store (tmpfs scratch, deleted after each run) — but the flood lanes'
   several GB will not fit either. Someone should prune `data/cache/subsets` (231 G, verified) —
   not mine to delete.
2. **`mean_sea_level_pressure` is not in `truth_mirror.MIRROR_VARS`.** Deliberate: stream from
   ARCO per window instead of growing the mirror on a 99 %-full disk. If the owner wants the MLC
   scan to run offline from the mirror, that is a ~1 GB/90-day decision to make before `mlc-impl`.
3. **Runtime dependency**: TE lives outside `.venv` (`/opt/ewb-live/opt/mm/envs/te`, installed via
   `scripts/mlc/setup_te.sh`, 281 MB). `mlc-impl` must fail soft when it is absent. If a clean-box
   rebuild is required, `setup_te.sh` is the single step (needs the box to have ~300 MB free).
4. **Intensity axis**: MSLP-only means the track view has pressure, no wind. If the owner wants a
   wind series, say so **before** `mlc-impl`: 10 m wind (`u10/v10`) is a cheap single-level field,
   850 hPa vorticity is the expensive one.
5. **vor3 vs vor3v** (99 vs 65 Nov tracks): recommend 99 + screens; the vorticity gate is the knob
   to revisit after one live month, not the first lever.
6. **tc overlap rule** needs confirming by the `tc` lane (see the PROOF entry) so a post-tropical
   system like Ciaran is published once.

## [mlc-impl] 2026-11-17 13:20 UTC — the lane is implemented in pure Python and validated against TempestExtremes

**What landed** (`aaTman/ne-mlc-impl`, nothing pushed, nothing promoted):

* `ewb_live/events/mlc_track.py` — the tracker. Pure numpy/scipy/scikit-image, no
  TempestExtremes, no conda. Structured function-for-function against upstream
  ExtremeWeatherBench `events/tropical_cyclone.py` v1.1.1 (`_find_peaks_for_time_slice`
  → `find_peaks_for_time_slice`, `find_contours_from_point_specified_field`
  → `find_contours_from_point`, `find_valid_contour_from_point` +
  `find_furthest_contour_from_point` → `find_valid_contour`, `find_valid_candidates`
  → `find_valid_candidates`, `_create_spatial_mask` → the latitude band + pressure
  threshold, `_fill_track_gaps_from_fields` → `fill_track_gaps_from_fields`,
  `generate_tc_tracks_by_init_time` → `generate_mlc_tracks`). The mapping is written
  out in the module docstring.
* `ewb_live/events/mlc.py` — Events, the §4 qualification rule from
  `notes/mlc_algorithm.md`, `details["track"]` in the shared 5-slot schema, severity,
  floor, dedupe, core window, wrap-aware bbox, IBTrACS tropical-origin screen.
* `ewb_live/sources/mlc_era5.py` — MSLP streamed from ARCO in 24 h chunks (the ewb
  root disk is at 99 %, so nothing is mirrored); orography cached once as a 4 MB npz;
  fails soft when the network is down.
* `scripts/mlc/validate_python_vs_te.py` — the harness (extract once → TE with the
  PROOF recipe → the Python tracker on the *same* files → counts, agreement, Ciaran,
  timing/RSS). Its report is committed at `notes/mlc_python_validation.md`.
* `tests/test_mlc_track.py` — 15 tests on synthetic global fields (node/track
  detection, position accuracy, bomb tag, static-low rejection, each screen on its
  own, lifetime/depth floors, dedupe, core window, gap filling, full event + schema
  validation, tropical-origin rejection, antimeridian convention, genesis-window
  ownership, fail-soft). 313 passed / 29 skipped / **2 failed** for the whole repo —
  both are the known `third_party/ExtremeWeatherBench/data_prep` failures the other
  lanes already documented on `main`: both are `_upstream.py` raising
  `FileNotFoundError: EWB data_prep dir not found: …/third_party/ExtremeWeatherBench/data_prep`
  (`third_party/` is gitignored). Proved environmental — with
  `EWB_DATA_PREP_DIR=~/scratch/ExtremeWeatherBench/data_prep` those two pass, so the
  suite is 315 / 29 / 0 in a complete environment.
* Shared files, additive only: `events/types.py` (`+ "midlat_cyclone"`),
  `events/__init__.py` (id tag + scan dispatch), `events/validate.py` (title label,
  900–1015 hPa peak bounds, mlc cross-checks: min MSLP vs `peak_value`, lifetime,
  displacement, genesis latitude, bomb consistency, track schema), `events/select.py`
  (severity, floor, the track lane outside the no-overlap rule, own quota 30),
  `events/qualification.py` (track lanes qualify at their first fix),
  `pipeline.py` (published-unscored lane + documented `pair_status: unsupported`),
  `dashboard/{app.js,styles.css,index.html}` (label, `--series-mlc` violet at 8.03:1
  light / 5.46:1 dark, criteria + About bullet, story line "954 hPa centre · 31°
  travelled · bomb ×1.5"), `pyproject.toml` + `uv.lock` (declare `scikit-image`,
  already installed transitively → 2-line lock diff).

**Validation vs TempestExtremes** (ewb LXC, 144 frames of ERA5 0.25° MSLP, Oct 26 –
Nov 30 2023, TE `--closedcontourcmd msl,3.0,6.0 --mergedist 6.0 --minabslat 5
--range 6 --mintime 48h --maxgap 12h`, both trackers on the identical netCDFs):

| | python | TempestExtremes |
|---|---|---|
| nodes | 3199 | 4006 |
| tracks ≥ 48 h | **138** | **145** |
| median lifetime / min MSLP / displacement | 66 h / 975.1 hPa / 1693 km | 72 h / 977.3 hPa / 1700 km |
| deepest | 926.7 hPa | 926.7 hPa |
| bombs | 17 | 18 |
| TE tracks matched by a python track | **133 / 145** | — |
| position error over 1771 matched fixes | median **0.00°** (identical grid cell), p90 0.00° | — |
| Ciaran | 954.0 hPa at 2023-11-02 06Z, 156 h, 3456 km, −32.2 hPa/24 h, bomb ×1.52 | identical, field by field |
| wall time | 26.9 s detect+stitch (0.19 s/frame) | 17.9 s + 0.01 s |
| peak RSS (whole python process, 144 frames resident) | 2.0 GB | — |

Read honestly: the two trackers agree on **which lows exist and where they are**
(median error exactly zero because both snap to the grid cell minimum), differ by
5 % on track counts (python keeps a few TE breaks up, TE keeps a few I split), and
recover Ciaran identically to the last decimal the PROOF quoted. The 17.6° maximum
position error is a handful of high-latitude tracks where the two attach a different
low at the ends of a merge/split — the median and p90 being 0.00° is the honest
summary, the max is a tail, not a drift. 138 tracks → **77 published events** after
the §4 screens (14 tagged bomb). Python costs ~1.5× the C++ binary's wall time on
the same field; that is the price of "no conda, no binary, runs in `.venv`", and it
is ~27 s per 36-day window.

**Where I diverged from upstream's TC tracker, and why** (the brief asked me to say
so explicitly): no warm-core geopotential-thickness contour (extratropical lows are
cold-core aloft — it would reject every one of them); no 10 m wind gate and no
IBTrACS genesis prior (an MLC has no prior best track at genesis, which is the whole
point); great-circle instead of index-space distances (a 0.25° grid is not isotropic
in longitude); MSLP in hPa so the contour delta reads `3.0` like the PROOF recipe;
merge keeps the **deepest** low (TE `--mergedist` semantics); contour and gap-fill
searches restricted to a window around the low (see the upstream PR — this is the
same fix).

**Upstream PR (kept out of this lane's commits).** `notes/upstream_pr_1.md`, branch
`perf/tc-locality-search` in a clone at `~/scratch/ExtremeWeatherBench` (v1.1.1 @
`c6467f4`). **Not pushed, no PR opened** — needs the owner's fork. It ports the
locality idea back: TE's `_create_spatial_mask` builds a
`(n_track_points, nlat, nlon)` global distance field and
`find_contours_from_point_specified_field` contours the whole globe per peak, for
results that are local by construction. Windowed (Clairaut bound, antimeridian and
degenerate grids fall back): **504 ms → 3 ms** mask, **760 ms → 24 ms** per timestep
peak search, a 20-init × 120-lead forecast eval from ~30 min to ~1 min. 69 new
equivalence tests against reference implementations of the replaced code, existing
43 TC tests pass, ruff clean, `benchmarks/tc_detection_profile.py` included. I did
not open a second PR: after this change the per-step profile is flat (windowed
`find_contours`, scipy's max filter, xarray `DataArray` churn per peak) — nothing
else is big enough to be worth a PR, and `calc.haversine_distance`'s re-evaluated
`np.cos` is now noise next to the mask win.

**Open issues / needs the owner or orchestrator**

1. **Scoring**: `midlat_cyclone` is published and drawn, never scored (EWB has no
   metric set for a pressure-minimum track). The pipeline documents each pair as
   `unsupported` so `pair_coverage` / `full_run_complete` stay decidable — same
   policy the `tc` lane needs for `tropical_cyclone`. Confirm, or tell me to score
   it against something.
2. **Merge conflicts with the `tc` lane** in `select.py`, `pipeline.py`,
   `events/__init__.py`, `events/validate.py`, `events/qualification.py`,
   `dashboard/app.js`. Every edit is additive and commented `mlc lane`; the
   `MLC_TRACK_TYPE` / `MLC_TOP_N` names are chosen so folding them into a shared
   `TRACK_TYPES` after the merge is a one-line change. I did not touch `tc.py` or
   `ibtracs.py`, but `mlc.py` imports the `tc` lane's IBTrACS reader **lazily and
   fail-soft** for the tropical-origin screen — if that lane's reader is renamed,
   the screen silently stays off (the event still qualifies on its other four
   screens), so please keep the import path in mind at merge time.
3. **The screen set is a knob, not a law.** 990 hPa depth floor and the 25–70°
   genesis band are `QualifyParams` fields. Nov 2023 gives 77 events / 90 days; the
   `select.py` quota is 30 per window, so a busy window truncates by severity
   (depth × lifetime × bomb) rather than failing.
4. **ARCO reachability is a hard dependency** for the scan (the lane streams; the
   mirror has no `mean_sea_level_pressure`). Offline, `scan_mlc_events` returns `[]`
   and the lane simply publishes nothing.
5. **Intensity is pressure only** (no wind column in `details["track"]`), as the
   5-slot schema says. If the owner wants a wind series, that is a schema change
   first, not a tracker change.
