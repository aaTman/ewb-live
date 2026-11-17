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
