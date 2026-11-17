# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [drop-subsets] 2026-11-17 18:45 UTC — subsets cache DELETED (231 G); all MLWP eval now virtual-store-only

Owner ruling executed. Full inventory of every MLWP-touching path, the
OK/REMOVED/CHANGED/BUILDER classification, per-model store table, fc_parts
verdict and residual risks: **`notes/mlwp_virtual_store_audit.md`**. Code on
this worktree (`aaTman/ne-subsets`, branched from main); NOT deployed to
`/opt/ewb-live` (integrator `/dev/` + owner promotion decide that).

### Phase 1 — deletion (ewb LXC, done)

Pre-checked: no scoring/weekly/pipeline job running (`ps`, timers: next
weekly Mon 2026-11-23 07:00; the 6-hourly `ewb-refresh.timer` is the
separate `/opt/ewb-dashboard` ERA5 refresh — never touches the subset tree;
web server serves prebuilt JSON only). Deleted ONLY
`/opt/ewb-live/data/cache/subsets` (231 G, all 11 models). `cache/truth`,
`cache/fc_parts`, `data/clim`, `data/icechunk` untouched.

- **df before:** `/dev/loop14 492G 466G used / 5.4G avail — 99%`
- **df after:** `/dev/loop14 492G 235G used / 237G avail — 50%`
- **freed: ~231 GB** (disk back from the cliff edge).

### Phase 2 — the write path is gone; the store really serves scoring

Single choke point `ewb_live.sources.aiwp.open_mlwp_forecast` (via
`sources.open_forecast` / `fc_cache` / `viz`). MLWP reads are lazy Icechunk
refs, bbox+lead+level cut in memory, **nothing materialised**. Removed:
`aiwp._open_direct` (the direct-HDF5 download fallback), the api.py
per-init `ds.zarr` write-through (THE 231 G writer), `cache.py` subset
eviction (now inert shims), `_net.AIWP_BUCKET`, `ops/probe_fcn_subset.py`.
Bounded semaphore: 2 concurrent icechunk sessions/process
(`EWB_LIVE_ICECHUNK_MAX_SESSIONS`, per notes/ewb.md ≥3 hangs). Repeat calls
memoised in-process only (LRU, `EWB_LIVE_MEM_CACHE_MB`, default 512 MB).

Models → store now: 8 MLWP models (graphcast, pangu_weather, fourcastnet_v2,
aurora ± `_gfs`) read the Icechunk virtual store. `ifs_hres`/`aifs_single`
(ECMWF GRIB via CloudFront) and `aigfs` (NOAA GRIB) unchanged — they were
subsets *writers* but read their own sources; scoring still reuses
`fc_parts` for them. **fc_parts kept**: 3496 keys, 100% ECMWF, contents are
`fc_cache`'s own derived fetches, keys independent of the removed tree —
nothing to invalidate (see audit).

### Proof on the ewb LXC (real data, dev tree `/root/ne-subsets-dev`, scratch)

`ops/validate_virtual_store_scoring.py`: 2 events × {graphcast,
pangu_weather, ifs_hres}, `era5_arco` truth, compared row-by-row to the live
pre-change `score_parts` parquet.

| run | models | inits | wall | bench s (graph/pangu/ifs) | rows compared | identical |
|---|---|---|---|---|---|---|
| heat_wave `085497ecbe06` | g,p,ifs | 5 | 55.7 s | 36.0 / 7.2 / 7.8 | 21 | **21/21, max_rel 0.0** |
| AR `56b282629cf6` | g,p,ifs | full-live (~13) | 271.3 s | 39.6 / 47.1 / 52.8 | 360 | **360/360, max_rel 0.0** |

Values **bit-identical** to the pre-change scorecard rows. Subset tree did
**not** reappear (`live_subsets_exists: false`, `dev_subsets_exists: false`).

Timings vs the OLD path (notes/ewb.md): graphcast AR build_forecast used to
run **25+ min** per job (S3 request-rate throttling), pangu AR ~30 s/init
(~15 min/job), ifs_hres 20-35 min/job. Now the whole AR event (all 3 models,
13 inits) scores in **271 s (~90 s/model)**, heat event in **56 s**. Isolated
store reads: graphcast t2 41-lead 18×25° = **1.7 s**, pangu 3-var 4-lead 3-D
= **4.5 s**, memoised re-read = **0.0 s**. The bounded semaphore +
windowed-lead store reads removed the throttling collapse.

### Tests (local, synthetic)

`tests/test_mlwp_store_guard.py` (new, 6 tests): static scans over
`ewb_live/**` fail if the bucket token / `AIWP_BUCKET` / `cache/subsets` /
`_SUBSET_ROOT` reappear outside the two store builders, if `.to_zarr(`/
`.to_netcdf(` appears in `sources/`, or if `open_init(`/`open_model(` is
called outside `stores/`+the choke point; functional probes assert the
choke point serves from the store, writes NOTHING (no `subsets` dir),
store-miss→`FileNotFoundError`, LRU byte-eviction. `test_prune_cache` →
no-op assertions; MLWP cache test rewritten for the memo; network tests
skip MLWP when the store is absent. Non-network suite: **305 passed**
(2 pre-existing `test_events_unit` AR-floor failures unrelated to this
change, confirmed on a clean stash).

### Residual risks / behaviour changes (owner)

- **MLWP read freshness is now load-bearing on the store timer.** Inits
  published after the last store update (daily 05:30 + weekly catch-up)
  raise `FileNotFoundError`; `open_forecast_nearest_init` shifts to an
  earlier init (agreed lead-offset), so freshest inits can score <24 h
  older data transiently. Listing still reports them.
- **Dev laptops / store-absent hosts:** MLWP reads now fail loud (the
  direct fallback is gone BY DESIGN). Listing works without the store.
- LRU is per-process and 512 MB default; spawn-pool scoring workers each
  pay first-read cost (as with a cold subsets cache before).
- Store update itself still only appends virtual refs (no data bytes
  copied) — kept, per the builder carve-out in the ruling.

### ⚠ Owner mitigation if code can't land before Mon 2026-11-23 07:00

Live `/opt/ewb-live` still has the old api.py write-through, so the Nov 23
weekly WILL rebuild `data/cache/subsets` and refill the disk. Land the code
first (4 functional files), one line:

```
rsync -a --exclude __pycache__ \
  <this-worktree>/ewb_live/sources/{api,aiwp,cache,_net}.py \
  root@192.168.0.121:/opt/ewb-live/ewb_live/sources/ && \
  ssh root@192.168.0.121 systemctl restart ewb-live-web
```

Stopgap if you can't sync in time (defers scoring, does not fix it):
`ssh root@192.168.0.121 systemctl mask ewb-live-weekly.timer` (unmask after
sync). Do not just delete the folder again without the code — weekly refills
it.
