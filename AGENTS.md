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
- Deploy: `just deploy`

## Conventions
- SPEC.md is the contract: change the spec and the code together. Agent decision logs go in `notes/`.
- Python style: type hints, short docstrings, no dead code.
- Branching: one worktree per task, PRs into main, CI must pass.
- Never commit data, keys, or model weights.

## Do not
- Run EWB compute on this host; use `./ops/remote.sh`.
- Use the hosted EWB CIRA store; we build our own on the latest Icechunk.
- Delete data or partial outputs (owner rule: never delete); write elsewhere or ask.
- Deploy directly; open a PR and let CI gate it.

## Current state
See docs/decisions.md for decisions and open questions.
