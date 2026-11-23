# EWB Live

## Goal
A live-updating extreme-weather benchmark dashboard, modelled on Brightband's Operational WeatherBench. Every week it finds the extreme events of the last 90 days (heat waves, freezes, atmospheric rivers) with ExtremeWeatherBench (EWB) defaults, scores AI weather models and IFS HRES on them with EWB's default metrics, and publishes a scorecard. Used by Taylor; public target is ewb.espr.ai (local only for now). `SPEC.md` is the binding contract.

## Stack
- Language/framework: Python >=3.12, `uv` + hatchling; ExtremeWeatherBench (git dependency), icechunk, virtualizarr, zarr 3, xarray stack. Dashboard is static HTML+JS (Plotly/Leaflet).
- Data: Sierra MLWP forecasts (s3://noaa-oar-mlwp-data) as an Icechunk virtual store (references only), IFS HRES (s3://ecmwf-forecasts), ERA5 ARCO truth, GHCN stations. Outputs: `data/results/{events,scores,scorecard,meta}.json` plus weekly `history/` snapshots. Caches under `data/cache/` (gitignored).
- Compute: no EWB compute on this checkout. Edit here, run on the `ewb` LXC (230, `/opt/ewb-live`) through `ops/remote.sh`. Heavy entry points are host-guarded (`ewb_live/_guard.py`). CPU/RAM heavy, no GPU.

## Commands
- Setup: `just setup`
- Test: `just test` (e2e Playwright tests are excluded by default; `just e2e` runs them against the deployed dashboard)
- Lint: `just lint`
- Train/run: `just run` (pipeline, last 90 days) / `just weekly` (full weekly cycle)
- Deploy: only after the PR is merged and CI is green: rsync the code dirs
  (`ewb_live/ dashboard/ ops/ scripts/ tests/`, never `third_party/`) from a clean
  worktree checked out AT THE MERGED SHA to `ewb:/opt/ewb-live/`, verify with an
  md5 manifest against that SHA (`md5sum -c`, 0 FAILED), re-apply
  `chmod +x scripts/public_scrub_check.sh`, restart `ewb-live-web`, append a dated
  entry to `ewb:/opt/ewb-live/DEPLOYED_FROM`. Never deploy while the weekly runs.

## Conventions
- SPEC.md is the contract: change the spec and the code together. Agent decision logs go in `notes/`.
- Python style: type hints, short docstrings, no dead code.
- Branching: one worktree per task, PRs into `develop`, CI must pass. `main` is a
  frozen legacy branch: never base work, CI, or deploys on it.
- Never commit data, keys, or model weights.

## Do not
- Run EWB compute on this host; use `./ops/remote.sh`.
- Invoke `./ops/remote.sh run|bg|sync` from any worktree whose tree is not exactly
  the deployed SHA: it SILENTLY RSYNCS ITS OWN worktree to the box first, and did
  poison the live tree twice on 2026-11-23 (mid-weekly contamination). Prefer
  explicit ssh/rsync; when using remote.sh, run it from a worktree at the SHA
  recorded in the box's `DEPLOYED_FROM`.
- Use the hosted EWB CIRA store; we build our own on the latest Icechunk.
- Delete data or partial outputs (owner rule: never delete); write elsewhere or ask.
- Deploy directly; open a PR and let CI gate it.

## Current state
See docs/decisions.md for decisions and open questions.
