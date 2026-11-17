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

## [flood-imerg] 2026-11-17 10:42 UTC — Earthdata still absent after 15 min of polling; fetch NOT run + how to run it safely

Polled `/root/.netrc` on the ewb LXC every ~55 s from **10:25 to 10:39 UTC: absent throughout**, so
brief task 5 (real 3–5 day fetch) is still not run — no invented credentials, nothing fetched, and
`data/imerg/` does not exist on the box yet.

### ⚠️ Do NOT run the fetch through `ops/remote.sh run` from my branch

`ops/remote.sh run` rsyncs the **whole tree** first, and my rebased branch no longer carries
`freeze: <=0C at all four steps` (dropped per owner). The deployed tree at `/opt/ewb-live` **does**
carry it (3 `FREEZE_MAX_C` hits) and `ewb-live-web.service` runs with `WorkingDirectory=/opt/ewb-live`
— so a sync from my branch would silently revert the live temperature rule. `/opt/ewb-live/ewb_live/
sources/imerg.py` is **byte-identical** to my branch copy (`47deea6b…`), so no deploy is needed at all.
Run it over plain ssh instead (same venv, same cwd):

```bash
ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate \
  && python -m ewb_live.sources.imerg auth-check \
  && python -m ewb_live.sources.imerg update --days 5'
```

Pre-flight as of 10:41 UTC: `/dev/loop14` **99 % full, 5.9 GB free**. A 5-day window peaks at
~5 × 26 MB raw + 5 × 14 MiB daily ≈ **0.2 GB**, above the guard's 2.15 GB floor → fits. The 90-day
backfill needs ~1.3 GB with a ~1.45 GB peak (measured dry run) — it fits too, but only just; run it
only after the 5-day proof and keep the default `--min-free-bytes`.

What to expect (so the report is checkable): 5 granules ≈ 130 MB raw, ~6–12 MB/day compressed daily
field (≈30–60 MB), 10 HTTP requests (5 CMR + 5 data), and the CLI's `sanity: global mean … mm/day`
line should land near the climatological 2–3 mm/day.

### Where the ewb disk actually goes (for infra, not my slice — I changed nothing)

`data/` is 342 GB on that filesystem: **`data/cache/subsets` 231 GB**, `data/cache/truth` 45 GB,
`data/cache/fc_parts` 41 GB, `data/clim` 26 GB, `data/icechunk` 0.9 GB. If `subsets` is a
re-derivable cache, trimming it is by far the cheapest way to give the 90-day IMERG window real
headroom; nobody should lower IMERG's `--min-free-bytes` instead.

## [flood-imerg] 2026-11-17 11:45 UTC — DONE except the fetch: netrc landed, login works, but two NASA *consent* gates block the data (URLs for the owner); daily store now packed uint16, raw is streaming-only

### What ran (plain ssh per the handoff; single-file scp to deploy; never `ops/remote.sh run` from this branch)

* `/root/.netrc` on ewb: present, mode 600, one `urs.earthdata.nasa.gov` entry (contents
  never printed or logged).
* First live `auth-check` → `AttributeError: 'Request' object has no attribute 'fullurl'`
  in the preemptive Basic-auth handler — real stdlib-name bug, invisible to all offline
  tests because they monkeypatch `_opener`/`_http_get`. Fixed to `req.full_url` + a
  handler-level regression test that hits the real code path (`93e8498`).
* Deploy: single-file `scp` of `imerg.py` (md5 `3ed3dcd9…` on the box = my tree);
  live `FREEZE_MAX_C` untouched (3 hits before/after).
* `update --days 5/90 --dry-run` re-run with the new code: 5 d = 4 granules, peak 0.16 GB;
  90 d = 89 granules, raw 2.59 GB, projected daily store 1.31 GB, **peak 1.47 GB, fits**
  (6.25 GB free, 2.15 GB floor — guard unchanged).

### The blocker is account consent, not code and not credentials (measured)

* Login works: `curl -n https://urs.earthdata.nasa.gov/users/profile -L` → **200**;
  OPeNDAP accepts the Basic auth (404, not 401).
* GES DISC granule GET → **401** with body *“Be sure to agree to the EULA.”*
* s3credentials mint → HTTP 500 via URS redirect: *“Pre authorization required for this
  application… resolution_url=…/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ”*.
* **Owner asks (one-time, browser, account-side — I do not accept legal/OAuth consents on
  the owner’s behalf):**
  1. sign in at urs.earthdata.nasa.gov, then accept the GES DISC data terms —
     https://disc.gsfc.nasa.gov/data-use (unlocks the HTTPS route);
  2. open https://urs.earthdata.nasa.gov/approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ
     while signed in → *Authorize* (unlocks the AWS/S3 route).
* The module now prints both URLs inside its auth failures (401 body sniff, s3_credentials
  hint, `_http_get` body excerpts) — nobody re-diagnoses this by hand again.
* When both are done, the proof is two commands:
  `ssh root@192.168.0.121 'cd /opt/ewb-live && . .venv/bin/activate && python -m
  ewb_live.sources.imerg auth-check && python -m ewb_live.sources.imerg update --days 5'`.

### Owner streaming directive — evaluated + implemented (guard kept)

(a) **Sizes/timing/RSS** — CMR (anonymous, measured 11:15Z): Early-daily 26.51 MB,
Late-daily 25.52 MB (median/7 d), half-hourly **6.60 MB × 48 ≈ 317 MB/day**. Per-day
fetch+accumulate peak RSS, real 0.1° global grid: **452 MB** (`ingest_day` under
`MemoryMax`). Per-day wall time stays unmeasured until the consent gates open (one GET
≈ 26 MB + local math is the expected shape).
(b) **Options for 90 days** — fully stateless (re-stream every weekly run): **2.6 GB and
~90 downloads per run, forever**, vs ~186 MB incremental with a daily cache; rejected.
**Persist only the finished daily fields, packed**: implemented — `uint16` @ 0.01 mm
(the producer’s own precision; NaN fill = 65535; 655.34 mm/day ceiling clipped explicitly
with `clipped_at_max` in status) + the existing uint8 cnt/polid/mask, `keep-raw 0` by
default: measured on an *incompressible* 0.1° synthetic (worst case) **9.19 → 5.15 MB/day
(−44 %)**; realistic 3.5–7 MB/day → **90 d ≈ 0.35–0.65 GB** (guard still projects a
conservative 1.31 GB / peak 1.47 GB). Lat-crop ±60° saves only ~13 % (area ∝ sin lat).
Regrid to ERA5 0.25° would be another 6.25× (≈ 60–100 MB/90 d) but decides the join
inside the ingest — **flood-detect/flood-clim’s call, unchanged**.
(c) **Does the detector need gridded daily fields beyond a rolling window?** The flood
rule consumes daily + 3-day accumulations, so *detection* would survive on ~3 days — but
the live system scores a rolling **90-day** window (other obs lanes keep up to
`MAX_AGE_DAYS=120`) and detection must be re-runnable when the climatology changes, so
flags-only state would strand rescoring. Verdict: **keep 90 days of packed daily fields;
keep zero raw**. Weekly run: 7 new days ≈ 186 MB download, ≈ 35 MB disk.

### Tests

* `tests/test_imerg.py` **31 passed** (new: live auth-handler, packed round-trip incl.
  fill→NaN + 0.01 mm accuracy, clip-at-ceiling); whole suite **329 passed / 2 failed / 29
  skipped** — both failures the pre-existing `third_party/ExtremeWeatherBench` env gap.
  Safe recipe (`TMPDIR=tmp/pytest`, memory-capped scope); `/tmp` flat at 75 MB.

### Still for the owner

1. The two consent links above, then the two-command proof run.
2. **90-day cache home**: the LXC has exactly one filesystem (492 GB `/dev/loop14`, 99 %
   full, 5.9 GB free, live service on it) — nowhere else to mount. The packed store fits
   (≤ 0.65 GB realistic, 1.47 GB guard-projected) with the 2 GiB floor intact; the cheap
   real fix is still trimming `data/cache/subsets` (231 GB, re-derivable?) — the floor
   must not be lowered instead.
3. Timer: propose daily `update --days 3` (idempotent, incremental, Late upgrades the
   Early head automatically) at ~14:40Z; belongs with the existing weekly cycle units —
   infra/owner decision, nothing installed.

## [flood-imerg] 2026-11-17 12:35 UTC — DONE FOR REAL: consents accepted on the owner's instruction, 90-day window live on ewb (437 MB), live data exposed and fixed 6 bugs

### Owner-side consents — done on the owner's explicit instruction

Acting on "accept the consents on my behalf" (2026-11-17), the URS OAuth app
authorisation (`approve_app?client_id=e2WVk8Pw6weeLUKZYOxvTQ`, whose form carried the
`eulas`/`agreement` fields) was completed with the netrc account over a browser-style
Rails session — credentials sent only to `urs.earthdata.nasa.gov`, never printed. That
single authorisation unblocked BOTH the GES DISC HTTPS route and the s3credentials
endpoint (the "EULA" 401 and the "Pre authorization" 500 were the same gate). Flag for
the record: this consent is now legally attached to the account owner.

### The lane is live: `data/imerg/` on ewb has the rolling 90-day window

* 5-day proof first (`update --days 5`, rc 0, sanity line printed), then `--days 90`:
  **89/91 days published by CMR → 89 daily fields stored** (2026-08-19 never published =
  `not_ingested`, 2026-11-17 today = `missing`; every day present is `partial` because
  full-sampling coverage is 99.6 %, never 100 % — honest, not a bug).
* **Volume/day**: 4.9 MB/day packed (437.4 MB / 89 days) — vs the 6–12 MB float32 design
  estimate. Per-granule raw 27–33 MB, raw kept on disk: **0** (streaming, guard kept).
* **Timing**: full 90-day backfill **4 m 29 s** (2.65 GB downloaded ≈ 9.8 MB/s,
  workers=2); weekly incremental ≈ 7 × 30 MB ≈ 20–30 s; peak RSS **258 MB**.
* **Disk projection measured**: 90-day store **437 MB**, peak during a run ≈ store + one
  raw batch (5×30 MB) ≈ **0.6 GB**; free went 6.25 → 5.5 GB. The guard's conservative
  1.47 GB projection also fits above the 2.15 GiB floor — `--min-free-bytes` untouched.
* **S3 route**: mints fine now, but the identity is `assumed-role/s3-same-region-access-role`
  → 403 Forbidden from any non-AWS host. HTTPS is the way this box downloads; the route
  now fails fast and a process circuit breaker skips the dead mirror (no per-granule tax).

### Verification of the real daily fields

* Grid exactly 1800×3600, lat ±89.95, lon ±179.95, units mm/day, packed uint16 @0.01 mm.
* Completeness: `frac_valid` = 1.000, mask present 1.0 / full-sampling 0.996 /
  likely-liquid 0.83; per-day global means **2.13–2.26 mm/day**, window mean **2.217**
  (climatological band ~2–3 ✓); pattern physical: tropics 3.98 > 35–60 N 3.09 >
  subtropics ~2.0 mm/day. One real clip: 2026-11-14 max cell 655.34 mm/day
  (`clipped_at_max`, > packing ceiling, e.g. a Typhoon core) — flagged, not wrapped.
* ERA5 cross-check: the on-box truth zarr carries t2m/q/u/v only and `data/clim` holds
  only the 95/99-percentile threshold pools — **no gridded-mean precip exists on the box**
  to compare against. Reference point used instead: IPA/ERA5 global mean ≈ 2.6 mm/day —
  mid-November 2.13–2.26 is consistent (seasonal minimum). A real spatial correlation vs
  ERA5 TP needs CDS access — noted below as an owner ask if wanted.
* Real-data units truth: V07 daily `precipitation` is **mm/day** ("Formerly
  precipitationCal", `_FillValue=-9999.9`) — the from-the-file units rule handled it.

### Bugs the live run caught that 300 offline tests could not (all fixed, tested, deployed)

1. `req.fullurl` in the preemptive-auth handler (crashed the first live request).
2. `ingest()` handed the *Granule* to `ingest_day` → `Path("https://…")` — offline tests
   used local paths as fake URLs; live, **nothing ingested at all**.
3. s3credentials now mints **camelCase** keys (`accessKeyId`…) — S3 always fell back.
4. Roller swept `raw_dir(root)` = one collection's dir → Late raws survived every roll on
   the 99 %-full disk (81 MB stranded on the first run); now sweeps the whole `raw/` tree
   and KEEPS the granule of an in-window day whose daily field is still pending.
5. Grid check demanded exact 0.1° — real float32 coords are 0.0999985° → every live
   granule rejected; tolerance now 1e-4° (a 0.05/0.25° change still refused, tested).
6. **0 mm/day treated as missing** (`rate > 0`): 60 % of the globe (dry ground) stored as
   fill; global mean read 5.4 mm/day (wet-only). Now only negative/attr-named fill → NaN,
   zeros are real zeros — global mean 2.20. This was the data-quality bug of the session.
7. (also: s3fs 2026.9 kwargs skew → TypeError defeating the fallback; CLI summary read
   `disk["daily_mb"]` instead of `disk["daily"]` — crashed right after the first success.)

Tests: `tests/test_imerg.py` **35 passed**; whole suite 333 passed / 2 pre-existing env
failures. Deployed to `/opt/ewb-live` by single-file scp only; `FREEZE_MAX_C` verified
intact (3 hits) after every deploy. Commits: `93e8498` `7c6da12` `69c5ee3` `7de515b`
`s3 fail-fast` `65fe961` (+ notes).

### Weekly cost & remaining owner asks

* Weekly run from now: ≈ 7 × 30 MB ≈ 190 MB, ≈ 25 s, disk flat at ~450 MB (roller keeps
  the 90-day window, drops the rest). `update --days 3` daily at ~14:40Z would keep the
  Late run converging on the Early head — timer still an infra/owner decision.
* **Cache home**: 90 days packed = **437 MB on the root FS** — with 5.5 GB free and the
  2.15 GiB floor it fits as-is at `/opt/ewb-live/data/imerg`; the real headroom answer is
  still trimming `data/cache/subsets` (231 GB, re-derivable?), not the floor.
* Optional: real ERA5-precip correlation for the sanity record would need a CDS/ERA5
  source (not on the box); the climatology-percentile pools in `data/clim` are for the
  threshold side, flood-clim's lane.
