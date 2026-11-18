# midlat_cyclone lane: root cause of the 0-event blocker, the fix, and the re-validation

Role `mlc-fix`, 2026-11-18. All compute on the ewb LXC (`/root/scratch/mlc-fix`,
a scratch copy of this worktree run with `/opt/ewb-live/.venv`; `/opt/ewb-live`
and `/opt/ewb-live-dev` untouched). Diagnostic harness:
`scripts/mlc/diag_arco_frame.py` (one real frame, both axis orders, side by side)
and `scripts/mlc/revalidate.py` (the production lane over a window, funnel +
top-N + known-storm check + timing/RSS). Raw diagnostic output committed at
`notes/mlc_diag_ciaran_20231102.txt`.

## 1. Symptom (the integrator's blocker, notes/channel_new_events.md 2026-11-17 22:55)

Over the 90-day dev window the lane scanned 361 frames, found 272 nodes, stitched
3 tracks and published **0 events**, while mlc-impl's proof window published 71
events / 13 bombs. On one real ARCO MSLP frame (2026-10-20 00Z)
`find_peaks_for_time_slice()` returned 0 nodes although 190 k cells were below the
1010 hPa ceiling in the 25-70° band, and relaxing *only* the closed-contour
parameters produced 5-27 nodes.

Reproduced on this branch before any change (`/root/scratch/mlc-before` = this
tree with the pre-fix reader):

```
midlat_cyclone 2026-08-19..2026-11-17: 385 frames, 273 nodes, 3 tracks -> 0 events
```

## 2. Root cause: the ARCO reader handed the tracker a ROTATED longitude axis

`ewb_live/sources/mlc_era5._to_repo_frame()` converted ARCO's 0-360 grid to the
repo frame with

```python
da = da.assign_coords(longitude=(lon + 180.0) % 360.0 - 180.0)
# ARCO longitude is ascending 0..359.75, so the shifted axis is ascending too   <-- the bug
```

That comment is false. Shifting `0, 0.25, …, 359.75` by +180 mod 360 **rotates**
the axis: it becomes `0, 0.25, …, 179.75, -180, -179.75, …, -0.25` — the data
columns stay in physical order (so no cell is mislabelled), but the axis is no
longer monotonic. Nothing sorted it back, and the tracker's index math assumes it
is:

* `mlc_track.grid_spacing(lon)` averages `|diff(lon)|`, and one of those diffs is
  **-359.75°**. On the 0.25° grid that returns **0.4998°** — exactly 2×.
* `mlc_track.contour_to_latlon()` rebuilds every contour point's longitude as
  `lons[0] + column * grid_spacing(lons)`, so each point is placed at roughly
  twice its column offset from the axis origin: both a scale error and a large
  additive error (the origin label is `0`, the first column of the rotated axis).
* The consequence is exactly the shape the integrator predicted: the great-circle
  radius of the `centre + 3 hPa` isobar — the
  `centre + 3 hPa closes within 6 deg` gate, the one thing standing between "a
  local minimum" and "a closed low" — is measured at 8-67° for rings that are
  0.5-3.4° across. **Every** candidate on a real frame fails the gate, so
  `find_peaks_for_time_slice()` returns no nodes, the stitcher has nothing to
  track, and the lane publishes nothing while every unit test stays green.

The same function had a second, latent instance of the same class of mistake: the
latitude flip test was inverted (`if lat[0] < lat[-1]: flip`), so ARCO's
90 N-first grid came out **descending**, the opposite of the module docstring and
of `mlc_track`'s documented convention. `find_peaks_for_time_slice` flips a
descending axis internally, so this one was not fatal — but it made the reader's
output lie about its own contract, which is how the longitude problem stayed
invisible.

Measured on the two frames that matter (identical field, only the axis order
differs — `diag_arco_frame.py`):

| frame | axis as the reader returned it | nodes, as-read | nodes, lon sorted |
|---|---|---|---|
| 2023-11-02 06Z (Ciaran) | lat desc; lon `0…179.75,-180…-0.25`, max step 359.75°, `grid_spacing=0.4998°` | **1** | **24** |
| 2026-10-20 00Z (dev window) | same | **0** | **27** |

and the radius the contour stage measures for Ciaran's own +3 hPa ring:
**1.11° true vs 2.91° as-read** — Ciaran passed the 6° gate by luck (its ring is
small); a low with a 3° ring measured 9.6° and died. Deepest candidates on the
2026-10-20 frame measured 40-68° against true radii of 1.9-2.7°.

### Hypotheses from the brief, tested rather than assumed

| hypothesis | verdict |
|---|---|
| cell-vs-degree scaling of the contour distance | **this is the bug** (a 2× grid step derived from a rotated axis, feeding an index→degree map) |
| missing `1/cos(lat)` longitude widening of the contour window | not it — `find_peaks_for_time_slice` already widens `lhw = ceil(halfwin/cos lat)` |
| units, Pa vs hPa (ARCO msl is Pa; thresholds are 3.0 hPa) | not it — `_to_repo_frame` divides by 100 and the window reads 926.7-1060.6 hPa. Kept as a **test**: the same synthetic low left in Pa yields 0 nodes, because the 1013 hPa ceiling then masks the whole globe |
| latitude ordering (ARCO is 90 N-first) | real contract violation (inverted flip test), not the cause of the zero; fixed |
| longitude convention 0..360 vs -180..180 | **this is the bug**, see above |
| windowed-contour path vs the netCDF-extract path mlc-impl validated on | explains the *survival* of the bug: `scripts/mlc/extract_month.py` shifts **and `sortby("longitude")`**, so the validated path was well ordered and the ARCO path was simply untested |
| float32 vs float64 | not it — same nodes, same positions in both dtypes (float32 spacing at 1000 hPa is 6e-5 hPa) |
| dask/xarray indexing | not it — the reader materialises `np.ascontiguousarray(float32)` before the tracker sees it |

## 3. The fix (3 commits on `aaTman/ne-mlc-fix`)

1. `6ffad95` **sort the axis, and make the tracker refuse a bad one.**
   `_to_repo_frame` now flips latitude to ascending, clips `|lat| ≤ 88` with an
   ascending slice, shifts longitude **and re-orders the columns to match**, and
   validates the lattice (`check_axes`) before returning it.
   `mlc_track.check_grid()` enforces the same contract where the index math lives
   (strictly ascending uniform longitude; monotonic uniform latitude) and raises
   `ValueError` — a future reader regression becomes a loud lane failure that the
   UI reports, never an honest-looking zero. `load_orography`'s inverted latitude
   flip is fixed the same way.
2. `8ce13b9` **only frames ARCO has actually filled.** ARCO's time index runs
   ahead of its data: every cell of 2026-11-12 00Z .. 2026-11-17 00Z is NaN, so a
   90-day window asked for 385 frames and got 29 empty ones. Peak detection
   ignores non-finite cells, but the tracker's gap filling takes the *lowest* cell
   in a hole, so an empty frame could become a NaN central pressure in a published
   track (`min_mslp` NaN passes `min MSLP > 990`, `peak_value` lands as `null`).
   `read_mslp_window` now drops all-NaN frames per chunk, logs the ranges, and
   returns the no-frames sentinel when that empties the window (→ `no-data`, not
   "no lows met the criteria"). This is also why the lane's effective window today
   ends at ARCO's last filled frame, 2026-11-11 18Z.
3. `95b5044` **the lane says how it ended** (see §6). `RULE_REV → mlc-v2`, so a
   cached zero scan cannot resurrect under the fix.

Tests added: `tests/test_mlc_arco_frame.py` (real ERA5 cutout around Storm Ciaran,
9 frames, `tests/fixtures/mlc_ciaran_cutout.npz`, 377 KB; regenerate with
`diag_arco_frame.py --save-cutout`) — nodes/track/Event on real data under the
production screens, the same bytes re-expressed in ARCO's units and axis order
(Pa, 90 N-first, lon 0-360) must still find the low, the Pa-only field must find
nothing, the rotated axis is measured crossing the 6° gate, and the reader drops
frames ARCO has not filled; `tests/test_mlc_lane_status.py` (§6). Three of the
new tests fail on the pre-fix reader (verified by reverting `mlc_era5.py` only).
Suite: **486 passed / 4 failed / 29 skipped**, all 4 failures pre-existing on the
branch base (2 need `third_party/ExtremeWeatherBench/data_prep`, 1 is the local
ARCO cutoff probe, 1 `test_flood.py::test_detect_events_routes_flood…` — each
also fails at the base commit). Incremental coverage of this branch's diff in
`ewb_live/**`, measured with `coverage` over the four mlc-relevant test files:
**108/127 statements = 85.0 %** (the 60 % gate) — `mlc.py` 10/10, `mlc_track.py`
15/17, `mlc_era5.py` 40/47, `events/__init__.py` 36/45, `pipeline.py` 7/8. The
uncovered lines are the defensive raises and fail-soft `except` arms
(`_to_repo_frame`'s duplicate-axis guards, `check_axes`' uniform-lattice raises, the
IBTrACS/orography `except` in the PROOF check), which is what they are for.

## 4. Re-validation on the ewb LXC (all through the ARCO reader path)

### PROOF window, Storm Ciaran — the netCDF-extract validation reproduces exactly

`revalidate.py --start 2023-10-26 --end 2023-11-30`, through the ARCO reader:

| run | frames | nodes | tracks ≥ 48 h | events | bombs |
|---|---|---|---|---|---|
| **mlc-impl's exact field** (`--lookback-days 0 --read-end 2023-11-30T18`) | 144 | **3199** | **138** | **71** | **13** |
| mlc-impl's netCDF-extract validation (the reference, `notes/mlc_python_validation.md`) | 144 | 3199 | 138 | 71 | 13 |
| same event window, field ending 30 Nov 00Z (`--lookback-days 0`) | 141 | 3142 | 136 | 70 | 12 |
| the lane's production setting (6 d stitching look-back) | 165 | 3833 | 161 | 69 | 12 |

The ARCO read path now agrees with the validated netCDF path **node for node**
(3199 / 138 / 71 / 13). The two lower rows are the same tracker on a shorter
field, not a different result: the lane reads `[start − 6 d, end]`, and a low born
on 29-30 Nov cannot reach the ≥48 h / ≥1000 km floors without frames after `end`
(the look-back helps genesis, never the tail) — so the production count for a
window is legitimately a little below the count of the same window read to its
last frame. Deepest 926.7 hPa, median min MSLP 966.4 hPa, median lifetime 78 h,
median displacement 1999 km, mean |genesis lat| 52.2° — all as the PROOF reported.
And the identity check is exact:

```
CIARAN (2023-10-26..2023-11-30, ARCO reader):
  nearest fix to 2023-11-02 06Z / 48.4N 4.6W: 2023-11-02 00Z, 49.8/-5.8, 954.6 hPa,
  172 km to the Finistere landfall point
  event min MSLP 954.0 hPa, min at 2023-11-02 06Z, lifetime 156 h, displacement
  3456 km, max 24 h fall -32.2 hPa = 1.52x the bomb threshold, 27 fixes
```

field by field the same as `notes/mlc_python_validation.md`'s python column, which
was itself field-by-field identical to TempestExtremes (954.0 hPa is the PROOF's
number, and it is the ERA5 field minimum in a 45-58°N / 25°W-5°E box those days,
which `revalidate.py` re-checks independently for every event it lists).

### 90-day dev window, 2026-08-19 .. 2026-11-17

| | before (pre-fix reader) | after |
|---|---|---|
| frames handed to the tracker | 385 (29 of them empty) | 356 (empty ones dropped: 11-04 00-18Z, 11-09 00-18Z, 11-12 00Z-11-17 00Z) |
| nodes | **273** (0.71/frame) | **9004** (25.3/frame) |
| tracks ≥ 48 h | 3 | 325 |
| same-low dupes | 0 | 2 |
| **events** | **0** | **143** |
| bombs | 0 | 30 |
| invalid events (shared validator) | — | **0** |
| read / scan wall | 295 s / — | 252 s / 153 s (0.43 s/frame) |
| peak RSS (whole process, includes the one-time 17 GB orography decode) | 21.8 GB | 21.5 GB |
| published after curation | 0 | 80 (`TOP_N_BY_TYPE["midlat_cyclone"] = 80`, severity-ranked; see §7.2) |

Population: median min MSLP **957.6 hPa**, deepest **912.2 hPa** (which is the
global minimum of the whole 1.45 GB window — the tracker's headline number is the
field's number), median lifetime **72 h**, median displacement **2230 km**, mean
|genesis lat| **53.3°**. By genesis month (events / bombs / deepest):
Aug 17/1/941, Sep 57/10/913, Oct 61/14/912, Nov 1-11 8/5/944 — a spring-wmax
Southern Ocean cycle, in the same regime the PROOF measured for Nov 2023; the low
Nov figure is partly the missing tail (the last filled ARCO frame is 11-11 18Z).

Top 10 by severity (`revalidate.py` prints the same table; "field min" is the
ERA5 minimum within 2° and ±6 h of the deepest fix — an independent check that the
published central pressure is what the archive says, and it matches for every row):

| # | born → last core day | min MSLP | life | displacement | bomb | genesis → deepest (basin) | field min |
|---|---|---|---|---|---|---|---|
| 1 | 09-30 00Z → 10-01 | 952.1 | 288 h | 4242 km | yes | 52.0/-22.2 → 55.8/-19.5 (NE Atlantic) | 952.1 |
| 2 | 09-14 18Z → 09-15 | 913.3 | 102 h | 4907 km | yes | -35.0/-30.0 → -65.2/30.8 (S. Atlantic → Kerguelen) | 913.3 |
| 3 | 10-01 00Z → 10-02 | 912.2 | 96 h | 1354 km | yes | -56.8/85.0 → -61.8/98.0 (Indian, S of Australia) | 912.2 |
| 4 | 10-17 18Z → 10-18 | 946.3 | 162 h | 8142 km | yes | -38.0/-35.0 → -55.0/73.2 (S. Atlantic → Kerguelen) | 946.3 |
| 5 | 09-12 00Z → 09-13 | 937.5 | 192 h | 4479 km | – | -50.5/93.0 → -59.8/110.2 (Great Southern Bight) | 937.5 |
| 6 | 09-30 18Z → 10-01 | 935.7 | 168 h | 2005 km | – | -63.8/156.8 → -65.2/-171.2 (Southern Pacific → Drake) | 935.7 |
| 7 | 09-06 06Z → 09-07 | 926.8 | 90 h | 4919 km | yes | -43.8/58.8 → -57.5/105.2 (Indian) | 926.8 |
| 8 | 09-25 06Z → 09-26 | 924.5 | 84 h | 1641 km | yes | -62.0/-45.0 → -68.5/-12.2 (Weddell Sea) | 924.5 |
| 9 | 10-26 18Z → 10-28 | 946.9 | 126 h | 6360 km | yes | -38.2/-52.5 → -55.8/4.2 (S. Atlantic) | 946.9 |
| 10 | 10-22 00Z → 10-24 | 956.8 | 144 h | 4440 km | yes | 42.5/141.5 → 52.0/155.2 (Kuroshio → N Pacific) | 956.8 |

Sanity, stated honestly. Nine of ten are deep Southern Ocean / Kuroshio cyclones
in Aug-Oct, which is where and when the reanalysis record puts the most intense
mid-latitude cyclogenesis; the one North Atlantic case (#1, 952 hPa, 52-56°N,
2026-09-30) is a plausible late-September Atlantic low. The 912-913 hPa values are
extreme but they are ERA5's own values (column "field min"), reached in the
Southern Ocean where the reanalysis record lows lie; no station/ship report was
consulted, so treat the top rows as "the archive says so, the tracker follows"
rather than as an independent verification. Two things an owner may want to look
at, both pre-existing knobs rather than defects: the depth floor (990 hPa) is
trivially met in the Southern Ocean, so the lane's severity ranking is doing most
of the selection there, and one 288 h track shows that stitching can chain
successive lows across a 6°/step budget over 12 days.

## 5. What is NOT changed

The science is untouched: no screen, threshold or distance was retuned (the
integrator's note that a silent retune would be worse than an empty tab stands —
this was never a tuning problem). `Params`, `QualifyParams`, the §4 screens, the
severity ranking and the track schema are exactly as `mlc-impl` shipped them.

## 6. Second fix: a broken lane can no longer read as an empty sky

`scan_mlc_events` failing soft to `[]` is what made this invisible for a whole
build: the tab said "No midlatitude cyclones met the criteria this window". Now

* `mlc.LANE_STATUS` records the outcome of the scan —
  `{"status": "ok" | "failed" | "no-data", "reason", "window", "rule_rev",
  "n_frames", "n_nodes", "n_tracks", "n_dupes", "n_screened", "screened_by",
  "n_events", "source"}`;
* `detect_events` persists it as `lane_status_midlat_cyclone.json` next to the
  scan cache and restores it on a cache hit, so the weekly run knows what produced
  a cached zero; lanes that report nothing get **no** entry, so every other lane's
  copy is unchanged and `meta.ibtracs` still works as the TC lane's own mechanism;
* `pipeline` publishes it as `meta.lane_status` (fail-soft, like `meta.ibtracs`);
* `dashboard/app.js` has one function that writes a lane's "nothing here"
  sentence — `emptyLaneLine()`, which now asks `meta.lane_status` first, so the
  midlat_cyclone tab reads
  `Midlatitude cyclones: lane failed, so this window has no numbers — <reason>.`
  or `… not computed, the source had nothing for this window — <reason>.` instead
  of the criteria sentence. A failed lane outranks the region-scoped wording in
  both the event list and the scorecard lane block.

**For the chips lane (`ne-chips`):** the copy lives in `emptyLaneLine(t)` in
`dashboard/app.js`; the tab/tooltip should call `emptyLaneLine(id)` rather than a
lane-specific note, so the chip's tooltip and the list agree. `laneStatusNote(t)`
is exported-by-name for the case where the tab needs the sentence on its own.

## 7. Handoff to the integrator — what to rerun

1. Merge this branch's 3 commits, then in the dev tree: code-only
   `ops/remote.sh sync`-equivalent (the dev tree is `/opt/ewb-live-dev`; nothing
   in `data/` changes), then re-run the **midlat_cyclone lane only** and republish:
   `EWB_LIVE_CACHE` must point at the tree's own `data/cache` so the cached
   `midlat_cyclone_mlc-v1_*.json` is not reused (the tag changed to `mlc-v2`, so
   the old zero-scan cache cannot be hit), then
   `.venv/bin/python -m ewb_live.pipeline --publish-only --days 90` to rewrite
   `meta.json` (it now carries `meta.lane_status`) and
   `.venv/bin/python -m ewb_live.viz.timeline` (MLC entries join the Gantt).
2. Expect **143 raw events** for the 90-day window, of which curation publishes
   **80** (`TOP_N_BY_TYPE["midlat_cyclone"] = 80`, env `EWB_LIVE_TOP_N_MLC`);
   severity-ranked, so the tab shows the 80 deepest/longest/bombiest lows. Note
   the contrast with `mlc.py`'s `TOP_N = 30` comment ("a busy 90-day window holds
   ~20-40 qualifying lows"): that estimate came from the North-Atlantic-centred
   proof window. In a Southern Ocean spring the lane finds 143. If the owner wants
   the tab quieter, the knob is `TOP_N_BY_TYPE` / `EWB_LIVE_TOP_N_MLC`, not the
   screens.
3. Pre-warm the orography cache once on a fresh box (a one-time ~17 GB decode) so
   it never lands next to a scoring run:
   `.venv/bin/python -c 'from ewb_live.sources import mlc_era5; print(mlc_era5.load_orography()[0].max())'`
   → `5859.1`.
4. `meta.lane_status` is new: nothing else reads it yet except `emptyLaneLine()`.
   If another lane should report, it only has to expose `lane_status()` and be
   added to `_lane_scan_status()` in `ewb_live/events/__init__.py`.

## 8. Open items / needs the owner

1. **Disk**: the ARCO read for a 90-day window is 1.45 GB in RAM and the scan's
   peak RSS is ~21 GB (dominated by the one-time orography decode, which is
   cached afterwards). Disk free on ewb was 234 GB (51 %) during these runs; the
   lane writes only `data/cache/mlc/topo_0p25.npz` (1.9 MB) and the per-window
   event cache.
2. **ARCO's tail is emptier than its index claims** — as of 2026-11-18 the last
   filled MSLP frame is 2026-11-11 18Z (the lane lags ~6.5 days), and there are
   interior holes too: in this window 2026-11-04 00-18Z and 2026-11-09 00-18Z are
   all-NaN as well. Peak detection ignores non-finite cells and stitching tolerates
   a 12 h gap, so a 2-frame hole is bridged and a 6-day one is not; the reader now
   drops the empty frames and logs the ranges instead of letting gap fill invent a
   NaN central pressure. The UI still has no "lane data ends <date>" line — if the
   owner wants one, `lane_status.n_frames` + `window` is the data for it.
3. **Severity in the Southern Ocean**: with the 990 hPa floor, most Southern
   Ocean lows qualify every year; the quota (80/window) is what keeps the tab
   finite. If the owner wants a hemisphere-balanced view, that is a ranking
   decision, not a detection one — flagging it, not changing it.
4. **`scan_mlc_events` still returns `[]` on failure** (it must not take the
   weekly run down); the honesty now lives in `lane_status`. If the owner would
   rather hard-fail the build when the lane breaks, that is a one-line change in
   `events/__init__.py`.
