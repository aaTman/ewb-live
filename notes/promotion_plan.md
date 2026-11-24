# Promotion plan — `feat/round3-compress-tracks-freeze` (NOT executed; no push, no PR)

Written 2026-11-19 by round3. Everything below is a plan for the owner to approve.

## 0. Branch state and the base question

* Branch: `feat/round3-compress-tracks-freeze`, from `chore/integrate-lane-fixes` (6951827) + 7 round-3 commits.
* Compared with `main` (= `origin/main` = `e492a52`, which is also the merge-base): **215 commits** (11 merges), **317 files**, `ewb_live/ ops/ dashboard/ pyproject.toml` = 53 files (+13 193 / −535).
* **There is no `develop` branch** — neither local nor on `origin` (`git ls-remote --heads origin`: `main`, `reconcile-live`, `ci/self-hosted-runner`). The PR rule says to STOP and ask rather than default to `main`; **owner decision needed: which branch is the PR base** (create `develop` from `main`? use `reconcile-live`?). The commit list above is therefore "vs main".

## 1. `ne-subsets` (e4fd539) — IS reachable from this branch

`git merge-base --is-ancestor e4fd539 HEAD` → **true**. It came in via `ne-coldsnap` (merge `1816b34`), which was built on the subsets line. Its content (3 real commits `ddf78a8`, `f6eaaf8`, `f2a545d` + merge) is: MLWP virtual-store-only reads, on-disk subsets cache retired — `ewb_live/sources/{aiwp,api,cache,_net,__init__,registry}.py`, `ewb_live/scoring/inits_cache.py`, `ewb_live/weekly.py` (+2/−…), `ops/probe_fcn_subset.py` (deleted), tests, notes. DEV ONLY (live keeps the subsets cache).

History is not rewritten. Two ways to keep it out of live (decide one; both leave the commit reachable in dev history):
1. **Revert on the PR branch** — tested in a scratch worktree: `git revert -m 1 e4fd539` on this branch applies cleanly to all 8 source files; the only conflicts are `notes/channel_new_events.md` (take current) and `tests/test_mlwp_store_guard.py` (a later commit added the PARDIG exemption to a file that the revert deletes — resolve by deleting it, it only guards the virtual-store-only code). Result: the PR diff contains no subsets code. The revert commit itself should be mentioned in the PR body and dropped when ne-subsets is eventually promoted.
2. **Rebuild the PR branch without it** — new branch from `main`, merge the lane branches individually in the order of §2 skipping `ne-subsets`; `ne-coldsnap` must then be re-based onto `main` (cherry-pick its ~10 non-subsets commits) because it contains the merge. Cleaner history, ~1 h of conflict work.
Recommendation: option 1.

## 2. What is in the PR (by lane)

| lane | what ships | key files |
|---|---|---|
| cold_snap | p15 lane without ≤0 °C, freeze-subsumes-cold-snap curation, About copy | `events/cold_snap.py`, `temperature.py` (`MAX_C_BY_TYPE`), `select.py`, `SPEC.md` |
| freeze ≤0 °C | **already in** (`FREEZE_MAX_C`, daily max over the four 6-hourly steps; ARCO path computes max, `recompute_merged_max_consec` applies it). Raw freeze on the LXC dev tree with the rule: **11**; same window with the rule off: **24** (= cold_snap raw). Nothing extra to ship; `e17ac82` is a redundant duplicate of what is here. | `tests/test_cold_snap.py`, `tests/test_frame_masks.py` |
| tropical_cyclone | IBTrACS source + lane, TrackViz detail renderer, wind field asset (`tc_wind.json` schema 3, per-6h grids, dissipation), track layer on the merged map, **marks leave the map after the last fix (round 3)** | `sources/ibtracs.py`, `events/tc.py`, `viz/tc_wind.py`, `dashboard/tracklayer.js`, `dashboard/trackviz.js` |
| midlat_cyclone | closed ERA5 MSLP lows tracker (mlc-v3, rule-tagged lane status), screens, quota severity | `events/mlc*.py`, `sources/mlc_era5.py` |
| flood | PARDIG rain (default; IMERG fallback), ERA5 pooled wet-day climatology, tracked events, 4th raster layer (daily `.cls` frames), flood detail viz; fails soft | `events/flood.py`, `sources/{pardig,imerg}.py`, `climatology/precip_clim.py`, `viz/{floodviz,flood_frames}.py` |
| UI | chips component, **compressed chips + legend (round 3)**, region control in the section header, lane legends | `dashboard/{app.js,index.html,mergedmap.js,styles.css}` |
| infra | `ops/dev_site.sh` (dev only; flocked), `pyproject.toml` +`scikit-image`, `serve.py` 204 for optional assets, `viz/timeline.py` track entries, `open_truth_window` HRES fallback | |
| dev-only inside the diff | `ops/dev_site.sh`, `ops/dev_site_report.py`, `ops/tcmap_preview.sh`, `ops/validate_virtual_store_scoring.py`, `notes/**`, `scripts/**`, `tests/e2e/**` screenshots — harmless on live but may be left out of the PR if the owner wants a lean diff |

AR: the 13 AR events the publish gate leaves are published; the gate itself is unchanged (curated 15, 2 below the 500 land-pixel floor — still an open owner item, see the channel).

## 3. What live needs (in order)

1. Owner approves the PR base (§0) and the subsets handling (§1); open the PR with `--base <that branch>`, verify with `gh pr view --json baseRefName`.
2. Deploy **code only** to `/opt/ewb-live` with the flocked path (`ops/remote.sh` / the `ops/dev_site.sh` pattern; never `rsync --delete` over `data/`, `logs/`, `.venv`, `third_party`). `uv sync` (or `pip install scikit-image`) in the live venv — a new dependency.
3. Seed what the new lanes read (all under `/opt/ewb-live/data`, written by the weekly cycle on first run, but verify they exist):
   * PARDIG rain cache: `python -m ewb_live.sources.pardig --days 96 --workers 16` (anonymous GCS; weekly step 1b also does it);
   * ERA5 pooled wet-day precip climatology `data/clim/precip_era5_pooled_1990_2019.zarr` (or `EWB_LIVE_PRECIP_CLIM`) — verified present on live (`/opt/ewb-live/data/clim/precip_era5_pooled_1990_2019.zarr` + `.meta.json`); note `ewb-live-precipclim.service` is in a *failed* state on the box (the store is there; check why before relying on a rebuild);
   * IBTrACS: downloaded by the TC lane into `data/cache/ibtracs` on first run (needs outbound HTTPS to NOAA/NCEI);
   * ERA5 truth mirror `data/cache/truth/era5.zarr` (already live; note it ends ~2–3 days before today — the freeze/cold-snap scans fall back to ARCO, viz to the HRES analysis).
4. Full refresh: **`python -m ewb_live.weekly --days 90`** on live (hours — the dev equivalents were: detect 36 min, viz 52 min, plus pardig/flood frames/tc_wind). It scores heat/freeze/AR and publishes TC/MLC/flood unscored; a code sync alone shows empty / unscored lanes. Chain order that the weekly already runs: pardig → detect → viz → flood_frames → publish → timeline → tc_wind.
5. Cache-busting: `serve.py` appends `?v=` to the new JS/CSS; confirm the `tracklayer.js`/`trackviz.js` assets are served (200) on live.
6. Post-deploy checks: counts per lane in `meta.json.event_counts` (dev: heat 15 / freeze 5 / cold_snap 8 / AR 13 / TC 44 / MLC 80 / flood 40 — live will differ with its own window); `meta.lane_status` has no `failed`/`no-data`; browser QA at 1440 and 390 in both themes (0 console errors, 0 overflow); run the full e2e against live (`EWB_LIVE_URL=https://ewb.espr.ai scripts/e2e.sh`, chromium) and compare by name with the dev result (§5).

## 4. Rollback

* Code: keep the previous tree as `/opt/ewb-live/_rollback_<date>/` (rsync copy before the sync) and `git` tag of the deployed commit; rollback = rsync the copy back and restart `ewb-live-web`.
* Data: the weekly writes new `data/results/*` in place; snapshot `data/results/` (≈110 MB) before the full refresh and restore it with the code. New caches (`data/pardig`, `data/cache/ibtracs`, `flood_*`) are additive and can stay.
* Lane-level kill switches without a code rollback: flood fails soft (`lane_status.flood=no-data` when the rain store is missing); TC/MLC/flood tabs hide when their lane has no events; `tc_wind.json` is optional (tracks still draw without it).

## 5. Verification this round (against :8051, tree `/opt/ewb-live-dev`)

* e2e (chromium, by name): 1st full run 3F/168P (all attributed + fixed: the track rule now expires at max(last fix, end); the legend strip has the one-lane height); final full run **1F/170P/2S** — the one failure `test_region_deep_link_waits_for_lazy_file` is a startup race (`window.__debug` undefined at the first poll), passes 3/3 alone and the file 9/9; the wait now guards `window.__debug &&`. Integrator baseline was 1F/166P (the first-screen test, now green).
* Unit: 599 passed / 28 skipped / 0 failed (with `EWB_DATA_PREP_DIR` set).
* Browser QA (`scripts/integration2_qa.py`, `notes/screenshots/round3/qa/`): 7 tabs + All × 1440/390 × light/dark, console errors 0, overflow 0, detail panels render for every lane.

## 6. Risks

* **Compute on live**: the full refresh is hours of ERA5/ARCO + PARDIG streaming; run it in the background under the existing lock; the box also serves production meanwhile.
* **PARDIG is an AI-estimated, provisional source (CC BY 4.0)** — flood copy says so; it is drier than IMERG at 45–67° (bias note in `notes/`), so flood counts are not comparable to the IMERG era.
* **Chips/legend are a visible redesign** on the live front page (first-screen budget); the group captions become dividers (≥1400 px) and the legend is compact by default with a "Scales" expander — owner has seen only the dev site.
* **Track marks now vanish after the last fix** (owner request): the live-tail TC keeps its mark; a frame beyond the data edge shows nothing for an ongoing-but-unflagged MLC (no `ongoing` flag on MLC) — cosmetic.
* **ne-subsets** must not reach live (§1); a plain merge of this branch would carry it.
* The e2e suite defaults `EWB_LIVE_URL` to the **live** URL: set it explicitly (as above) or the tests run against production.
* Scoring: TC/MLC/flood stay "Not scored" (owner decision TC-8 pending).
