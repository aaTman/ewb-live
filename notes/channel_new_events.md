# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [flood-imerg] 2026-11-16 17:45 UTC — IMERG interface for the flood trio (sign grid/units now)

I own IMERG/GPM observation ingest (`ewb_live/sources/imerg.py`). Design + product-choice evidence:
`notes/flood_design.md` (Part 1). **`flood-clim`: please append your climatology design to that same
file as `## Part 2 — climatology` instead of creating another `flood_design.md`** (both briefs name
that path — one file, no add/add merge conflict).

**Product**: `GPM_3IMERGDE` = IMERG **Early Run** daily, 0.1°, V07 (only run with <1-day latency;
Late ~14–18 h, Final ~3.5 months → the recent window is not in Final). One granule/day ≈ 26.5 MB,
latency ~4.5 h after the UTC day closes. `product="late"|"final"` also wired.

**Grid** (as carried by the granule; code reads it per-file and asserts 0.1° + global):
1800 lat (−89.95 … 89.95, ascending) × 3600 lon (−180 … 179.95), cell-centred, 0.1°.
⚠️ 0.1° vs ERA5 0.25° — the join/regrid + bias handling belongs to whoever joins the two
(`flood-detect`), with flood-clim's convention winning if it posts one first.

**Day boundary**: UTC calendar day, 00:00:00–23:59:59.999 (the granule's own `S000000-E235959`).

**Units: mm/day.** Producer `precipitation` is a mean mm/hr rate → ×24; the code reads the granule's
`units` attribute and raises on anything unexpected (never guesses). No zero-filling anywhere:
absent granule ⇒ all-NaN day flagged `missing`; `precipitation_cnt < 48` ⇒ pixel flagged `partial`.

```python
from ewb_live.sources.imerg import (
    load_imerg_daily, load_imerg_mask, load_imerg_status, IMERG_GRID_DEG, UNITS,
)
load_imerg_daily(start, end, *, product="early", root=None,
                 screen="none"|"cnt"|"polid", min_cnt=48) -> xr.DataArray  # (time, lat, lon) float32 mm/day, NaN = no data
load_imerg_mask(start, end, *, ...) -> xr.DataArray   # (time, lat, lon) uint8: bit0 data-present, bit1 full sampling (cnt==48), bit2 likely-liquid
load_imerg_status(start, end, *, ...) -> pd.DataFrame # per-day: granule, status(present|partial|missing|no_granule), bytes, global mean/max mm, cnt_min, ingested_at
```
(time axis = `datetime64[ns]` at 00:00 UTC of each UTC day, ascending; NaN means "no observation",
never "no rain" — mask with `load_imerg_mask` or `screen="cnt"` before thresholding.)

**On-disk** (ewb LXC `/opt/ewb-live/data/imerg/`, override `EWB_LIVE_IMERG_DIR`; `data/imerg` relative
default for portability):
`daily/imerg_<YYYYMMDD>.nc` (per-day compressed NetCDF4: `precipitation` mm/day f4,
`precipitation_cnt` u1 0–48, `probabilityLiquidPrecipitation` u1) + `status.json` + `raw/…` (last
`--keep-raw` days, default 3). Rolling window: `python -m ewb_live.sources.imerg roll --keep-days 90`.

**Fetch/update CLI** (ewb LXC only): `ops/remote.sh run python -m ewb_live.sources.imerg update --days 90`
(idempotent, resumable, `.part`+rename, CMR granule discovery is anonymous, polite: 2 workers + backoff).

**Blocker (owner)**: no Earthdata credentials on the ewb LXC (`~/.netrc` absent, no env) — the real
fetch cannot run until one free NASA Earthdata account is registered in `/root/.netrc` on ewb.
Steps in my DONE entry below. Design/ingest/aggregation/masks are built and unit-tested on synthetic
granules meanwhile.

## [flood-imerg] 2026-11-17 03:10 UTC — postmortem: the runaway IMERG test (140 GB into /tmp) + fix

**Cause (found by reading the code, then confirmed by a bounded reproduction).** Not grid size, not
the fixtures, not a download loop: `tests/test_imerg.py::test_https_route_rejects_a_non_granule_body`
stubbed the GES DISC HTTPS response with

```python
def read(self, n=-1):
    return b"<html>Earthdata Login</html>" if n > 0 else b""
```

`ewb_live/sources/imerg.py::_get_https` copied the body with `shutil.copyfileobj(resp, out, length=1<<20)`,
which loops **until `read()` returns `b""`**. `shutil.copyfileobj` always passes its buffer length
(1 MiB) as `n`, so the stub answered 27 bytes *forever* and the copy appended ~10 MB/s to
`tmp_path/g.nc4.part` on the tmpfs `/tmp` for the 4 h until it was killed — 140 GB, host + Orca hung.
A real socket returns `b""` at EOF, so only the stub could do it; the production code had no ceiling.

**Fixes (both layers).**
1. Stub is now EOF-terminated (a finite list of chunks, then `b""`), and the same for the S3-side stubs.
2. `imerg.py` no longer trusts `read()` to reach EOF: new `_copy_capped()` (used by both transports,
   replacing `shutil.copyfileobj`) and `_read_capped()` (for the CMR/s3credentials JSON, which was
   buffered in RAM unbounded) raise `ImergError` once a body passes `MAX_GRANULE_BYTES` (default 512 MB,
   env `EWB_LIVE_IMERG_MAX_GRANULE_BYTES`; a daily granule is ~26 MB) / `MAX_BODY_BYTES` (64 MB), and the
   oversized `.part` is **deleted** so a later run cannot resume junk (the old code also cached HTML login
   pages as resumable partials — same fix). `test_https_route_caps_a_body_that_never_ends` reproduces the
   incident against a stub that never ends and asserts it stops after the cap and leaves no file behind.

Also shrank the synthetic test grids from 1° (180×360) to 30° (6×12 cells, still global so the coverage
assertion in `_check_grid` still bites): the whole 100-day rolling-window test now costs a few MB instead
of ~150 MB. `_check_grid`'s global-coverage bounds are now one cell of the declared spacing instead of
hardcoded ±89.5/179.0, which is equivalent at 0.1° and makes a tiny global lattice testable.
Test footprint: `/tmp` stays at 29 MB used with TMPDIR on tmpfs; the suite is now run under
`systemd-run --user --scope -p MemoryMax=8G -- timeout 300` with `TMPDIR` on disk.

**Other flood agents:** no interface change (still `load_imerg_daily(start, end) -> (time, lat, lon)`
mm/day + `load_imerg_mask`/`load_imerg_status`). flood-clim's contract above is compatible: UTC
calendar-day totals in mm, and `IMERG_GRID` (0.1° cell-centre, ±89.95 / −180…179.95) is exactly what my
granules carry, so `grid="imerg"` / `precip_threshold_on` will line up.

## [flood-imerg] 2026-11-17 03:45 UTC — DONE: IMERG daily ingest built, tested, sized; one owner blocker

### What changed (this worktree, `ne-flood-imerg`, branched from `8ba00be`)

* `freeze: <=0C at all four steps (from owner patch)` — `briefs-new-events/freeze_le0c.patch` applied
  and committed first, as COMMON.md asks (`ewb_live/events/temperature.py`, `SPEC.md`,
  `dashboard/detection.py`, `tests/test_frame_masks.py`).
* **`ewb_live/sources/imerg.py`** (new, ~1500 lines) — the whole observation lane: CMR granule survey
  (anonymous) → granule download (AWS Open Data S3 with Earthdata-minted temp credentials, GES DISC
  HTTPS fallback) → daily mm/day field + `precipitation_cnt` + `probabilityLiquidPrecipitation` →
  per-day compressed NetCDF4 + `status.json` index → rolling 90-day window. Run policy
  `product="auto"` (Late preferred, Early fills the head, cached days upgrade when Late lands).
  Disk-headroom guard + batched fetch (see `notes/flood_design.md` Part 1b). CLI:
  `python -m ewb_live.sources.imerg update|status|roll|auth-check`.
* **`tests/test_imerg.py`** (new, 28 offline tests, ~13 s, few MB, no network) and
  **`notes/flood_design.md`** (Part 1 product/access decision + Part 1b as-built evidence).
* No shared file is touched except `notes/*` and the owner freeze patch. `types.py` /
  `pipeline.py` / `select.py` / `dashboard/*` are **not** edited: the `flood` Event object, its
  registration and its dashboard rendering belong to `flood-detect` (it will be launched later and
  owns the join). Nothing here is importable-by-side-effect from those files, so the merge is clean.

### Interface (unchanged from my 17:45 entry — flood-clim's contract matches)

`load_imerg_daily(start, end, *, product="auto", root=None, screen="none"|"cnt"|"polid"|"cnt+polid",
min_cnt=48) -> xr.DataArray` — **(time, lat, lon) float32 mm/day**, `time` = 00:00 UTC of the UTC
calendar day, 0.1° cell-centred grid read per file (1800×3600, lat −89.95…89.95 asc, lon −180…179.95).
NaN = no observation, never zero. Plus `load_imerg_mask` (uint8 bits: 1 present, 2 full sampling,
4 likely liquid) and `load_imerg_status` (per-day present/partial/missing/not_ingested/error).
`root` defaults to `$EWB_LIVE_IMERG_DIR` or `data/imerg` → on the LXC `/opt/ewb-live/data/imerg`.

**For `flood-detect`**: flood-clim is right that the join must go 0.1° → 0.25° by *aggregating the
observations up*, and note the axes are not merely resampled — ERA5 is lat 90→−90 descending, lon
0→359.75 while IMERG is lat ascending, lon −180→179.95, so the aggregation has to roll the longitude
axis across the dateline and flip latitude before any areal average. Use `load_imerg_mask` (or
`screen="cnt"`) rather than `isnan` alone: a 0 in the mask means "no observation", which must not
count as below-threshold.

### Tests run

* `tests/test_imerg.py`: **28 passed** in ~13 s, under
  `systemd-run --user --scope -p MemoryMax=8G -- timeout 300 .venv/bin/python -m pytest tests/test_imerg.py -x -q`
  with `TMPDIR=$PWD/tmp/pytest`; `/tmp` peaked at +4 MB (33 MB total). Run postmortem above for the
  140 GB cause and its two-layer fix.
* Whole unit suite: **308 passed, 2 failed, 29 skipped** — both failures are
  `tests/test_events_unit.py` AR tests raising `FileNotFoundError: EWB data_prep dir not found:
  third_party/ExtremeWeatherBench/...` (pre-existing environmental gap, unrelated to IMERG; that path
  is gitignored).
* On the ewb LXC through `ops/remote.sh` (real endpoints, no credentials): `update --days 7 --dry-run`,
  `update --days 90 --dry-run`, `auth-check`. 90-day sizing: 88/90 days published by Late, raw total
  2.56 GB, **daily store ~1.29 GB, peak ~1.45 GB in 18 batches, fits with 2.15 GB held back.**

### Open issues / needs the owner

1. **Blocker for task 5 (real 3–5 day fetch): no NASA Earthdata login anywhere.** `/root/.netrc`
   absent on ewb, no `EARTHDATA_*` env, no netrc on the orca container. `auth-check` on ewb prints
   these steps and exits 3; nothing is stored in the repo:
   1. https://urs.earthdata.nasa.gov/users/new (free).
   2. On the ewb LXC: `printf 'machine urs.earthdata.nasa.gov login <user> password <pw>\n' > /root/.netrc && chmod 600 /root/.netrc`
      (or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` in the service env instead).
   3. Verify: `ops/remote.sh run python -m ewb_live.sources.imerg auth-check`.
   4. Then prove the lane: `ops/remote.sh run python -m ewb_live.sources.imerg update --days 5`
      (≈130 MB raw, ~60 MB daily fields, 5 requests) and `update --days 90` for the full window.
   I did **not** invent credentials or run the fetch — task 5 stays open until this lands.
2. **Correction to the AWS Open Data update**: the mirror is *Controlled Access / Requester Pays*,
   unsigned GET/LIST → 403 (evidence in Part 1b.2), so no route to this data is credential-free. The
   AWS path is still implemented as the default transport, but the Earthdata ask in (1) is unavoidable.
3. **ewb LXC root FS is 98–100 % full** (4.5–6.4 GB free, moving) with the live service on it. The
   90-day window fits (~1.3 GB + peak 1.45 GB) and the headroom guard now refuses an ingest that would
   not fit, but someone should free space before the backfill, and IMERG must not be given a lower
   `--min-free-bytes` casually.
4. **Merge note for the orchestrator**: `notes/flood_design.md` is add/add — mine is Part 1 (IMERG
   ingest) + Part 1b, `ne-flood-clim`'s is the climatology design (they did not append as asked).
   Concatenate, do not pick a side. `notes/channel_new_events.md` is likewise per-worktree.
5. Optional follow-up (not started, ~worth it for `flood-detect`): a tested
   `aggregate_to_era5_grid(da)` in this module (0.1° → 0.25° area-weighted mean with the dateline roll
   and NaN-aware weights), so the join does not reimplement the fiddly part. See
   `notes/handoff_flood-imerg_1.md`.

## [flood-imerg] 2026-11-17 09:20 UTC — rebased onto local main (e492a52), freeze commit dropped per owner

* `git rebase --onto main 30150ba` → branch `aaTman/ne-flood-imerg` is now **0 behind / 6 ahead of
  `main` (e492a52)**; pre-rebase state kept at `backup/pre-rebase-ne-flood-imerg-20261117`
  (`06a32c3`). Nothing pushed.
* **Dropped** `30150ba freeze: <=0C at all four steps (from owner patch)`, as instructed: `main` has
  not touched `ewb_live/events/temperature.py` since the old base (`git diff 8ba00be main -- …` is
  empty), so the identical file is what every new-events branch carries. My branch now touches **no
  shared file at all** — `temperature.py`, `SPEC.md`, `dashboard/detection.py` and
  `tests/test_frame_masks.py` are byte-identical to `main` on my branch, and the diff is purely
  `ewb_live/sources/imerg.py` + `tests/test_imerg.py` + `notes/*`. Merge should be conflict-free.
* ⚠️ **For the owner, one caveat on "if main carries it"**: as of `e492a52`, `main` does **not**
  contain the rule — `git show main:ewb_live/events/temperature.py | grep FREEZE_MAX_C` → 0 hits.
  It is carried by `aaTman/ne-coldsnap`, `aaTman/ne-tc` and the flood-clim chain visible in
  `backup/pre-rebase-main-20261117` (`abc2c90 freeze: <=0C at all four steps`), so dropping it here is
  safe **only if one of those lands**. Confirm, or the freeze rule quietly never merges.
* ⚠️ Second, unrelated: `origin/main` has **diverged** from local `main` (I fetched during this work:
  `origin/main = 3b1f63c "Merge pull request #2 from aaTman/reconcile-live"`, `bd0cbab..3b1f63c`, and
  `main` is *not* an ancestor of it — 68 local-only vs 2 remote-only commits). I rebased onto **local
  `main` (e492a52)** as told, not onto `origin/main`; someone should decide which lineage is
  authoritative before anything is pushed. `origin/main` also has no `FREEZE_MAX_C` and no
  `ewb_live/sources/imerg.py`.

### Tests after the rebase (same capped recipe)

* `TMPDIR=$PWD/tmp/pytest systemd-run --user --scope -p MemoryMax=8G -- timeout 300
   .venv/bin/python -m pytest tests/test_imerg.py -x -q` → **28 passed** in 12.4 s; `/tmp` watched
   every 3 s, flat at 82 MB (the run itself stays off tmpfs); peak scratch 12 MB in `tmp/pytest`.
* Whole suite `pytest tests -q` → **326 passed, 2 failed, 29 skipped** in 101 s. Both failures are the
  pre-existing environmental ones in `tests/test_events_unit.py` (`FileNotFoundError: EWB data_prep
  dir not found: third_party/ExtremeWeatherBench/data_prep`; `third_party/` is gitignored, so they
  cannot pass in any fresh checkout) — unrelated to IMERG and unchanged from before the rebase.
