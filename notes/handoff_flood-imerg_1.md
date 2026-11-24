# handoff — flood-imerg (slice essentially DONE), written 2026-11-17 03:50 UTC, rebased 09:20 UTC

**Rebase state**: branch `aaTman/ne-flood-imerg` is rebased onto local `main` (e492a52), 0 behind /
7 ahead; the `freeze: <=0C at all four steps` commit was **dropped per owner**, so my branch touches
no shared file. Pre-rebase backup: `backup/pre-rebase-ne-flood-imerg-20261117`. See the channel
entries at 09:20 and 10:42 UTC — including the warning that `main` itself does not carry
`FREEZE_MAX_C`, and that `origin/main` (3b1f63c) has diverged from local `main`.

**Goal (brief `briefs-new-events/flood_imerg.md`)**: IMERG/GPM daily precipitation for the last 90 days
as the `flood` event type's observation lane; `flood-clim` builds the ERA5 climatology,
`flood-detect` (later) joins them.

## State: DONE except the credential-blocked real fetch

* `ewb_live/sources/imerg.py` — complete: CMR survey (anonymous) → download (S3 via Earthdata
  `s3credentials`, HTTPS fallback) → daily mm/day + `precipitation_cnt` +
  `probabilityLiquidPrecipitation` → per-day NetCDF4 + `status.json` → rolling 90-day window, run policy
  `auto` (Late preferred, Early head, upgrade in place), disk-headroom guard + batched fetch, CLI
  `update|status|roll|auth-check` (guarded by `_require_ewb`, so it only runs on the LXC).
* `tests/test_imerg.py` — 28 offline tests, all passing (~13 s). Full unit suite 308 passed / 2
  pre-existing environmental failures (`test_events_unit.py` AR tests need `third_party/ExtremeWeatherBench`).
* `notes/flood_design.md` Part 1 + 1b, `notes/channel_new_events.md` has my interface entry, the
  140 GB /tmp postmortem and the DONE entry.
* Commits on this branch: `30150ba` freeze patch, `2de6dd0` EOF-cap downloads, `6b77a22` first-green
  test run + 3 real fixes, `f117f64` disk guard + batching, `cf1b60a` notes Part 1b + CMR/CLI fixes,
  `3838d17` DONE entry. Nothing pushed (orchestrator merges).

## How to run the tests safely (do not skip this)

```bash
mkdir -p tmp/pytest && TMPDIR=$PWD/tmp/pytest \
  systemd-run --user --scope -p MemoryMax=8G -- timeout 300 \
  .venv/bin/python -m pytest tests/test_imerg.py -x -q
```
`TMPDIR` keeps pytest's scratch off the RAM-backed `/tmp` (capped at 32 GB; a runaway once wrote
140 GB there and hung the host — cause + fix in the channel postmortem). Watch `df -h /tmp`; kill
above 2 GB. Any new HTTP stub **must** return `b""` at EOF: `shutil.copyfileobj` loops forever
otherwise (that was the incident).

## If/when the owner adds Earthdata credentials on ewb

**Use plain ssh, not `ops/remote.sh run`** — that subcommand rsyncs my whole tree first, and since my
branch no longer carries the freeze commit while `/opt/ewb-live` (WorkingDirectory of the running
`ewb-live-web.service`) does, it would silently revert the live `FREEZE_MAX_C` rule.
`/opt/ewb-live/ewb_live/sources/imerg.py` is already byte-identical to my copy, so nothing needs
deploying:

```bash
ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate \
  && python -m ewb_live.sources.imerg auth-check \
  && python -m ewb_live.sources.imerg update --days 5'
```

1. `auth-check` must print `OK: Earthdata login works …` (it exits 3 with the setup steps if the
   netrc is missing/unreadable).
2. `update --days 5` → report volume/day + timing (brief task 5: ~26 MB/granule raw, ~6–12 MB/day
   daily field, 10 requests total; watch the printed `sanity: global mean … mm/day`, climatological
   expectation ~2–3).
3. `update --days 90 --dry-run` then `--days 90` (peak ~1.45 GB; ewb root FS is 99 % full — 5.9 GB
   free on 2026-11-17 10:41Z, and `data/cache/subsets` alone is 231 GB). Do not lower
   `--min-free-bytes` without the owner.
4. A cron/systemd timer for `update` (daily, after ~14:30Z so the Late Run has landed) is **not**
   installed yet — decide with the owner/infra whether it joins the existing weekly cycle units.

## Gotchas

* CMR rejects `sortable` for these collections (HTTP 400) and publishes no granule `Size` in
  `umm_json` (so `size_mb=0.0` → disk projection uses `EST_RAW_BYTES`).
* netCDF global attrs must be scalars/strings: `grid` is stored as JSON (`_netcdf_attrs`).
* Both `shutil`-free transports cap a body that never reaches EOF (`MAX_GRANULE_BYTES`) and delete
  junk `.part` files; a synthetic test grid must still be *global* — `DEG = 30.0` in the test file
  gives 6×12 cells and the coverage assertion in `_check_grid` (tolerance = one cell of `deg`).
* Tests that call `ingest` must pass `grid_deg=DEG` (the default asserts the real 0.1° lattice) and
  `min_free=0` for wide windows.
* `notes/flood_design.md` is an add/add conflict with `ne-flood-clim`'s file — concatenate at merge.

## Optional next task (only if the owner asks)

`aggregate_to_era5_grid(da)` in `imerg.py`: 0.1° → 0.25° area-weighted mean (roll lon −180..179.95 →
0..359.75, flip lat asc → desc, NaN-aware weights). flood-clim's §3 wants the join to go this
direction; it is flood-detect's call, so don't start it unasked.
