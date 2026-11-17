# DONE — mlc-impl (mid-latitude cyclone lane, entirely in Python)

**Branch:** `aaTman/ne-mlc-impl` (branched from `aaTman/ne-mlc-research`, on top of `main` `e492a52`)
**Not pushed, not promoted.** No shared file touched beyond the additive hooks listed below.

## What landed

| file | role |
|---|---|
| `ewb_live/events/mlc_track.py` | the tracker: pure numpy/scipy/skimage port of DetectNodes + StitchNodes, structured function-for-function against upstream ExtremeWeatherBench `events/tropical_cyclone.py` (v1.1.1) — see the module docstring for the mapping |
| `ewb_live/events/mlc.py` | `midlat_cyclone` Events: qualification rule, `details["track"]` 5-slot rows, severity, floor, dedupe, core window, wrap-aware bbox, IBTrACS tropical-origin screen (lazy import of the `tc` lane's reader, fail-soft) |
| `ewb_live/sources/mlc_era5.py` | ERA5 MSLP from ARCO (0.25°, 6-hourly, streamed in 24 h chunks — the ewb root disk is 99 % full so nothing is mirrored), orography cached once as a ~4 MB npz, fails soft when offline |
| `tests/test_mlc_track.py` | 15 tests on synthetic global fields |
| `scripts/mlc/validate_python_vs_te.py` | the validation harness: extracts the same MSLP netCDFs, runs TempestExtremes with the PROOF recipe *and* the Python tracker on them, reports counts / position error / Ciaran / timing+RSS |
| shared files | `events/types.py` (+`"midlat_cyclone"`), `events/__init__.py` (id tag + scan dispatch), `events/validate.py` (label, 900–1015 hPa peak bounds, mlc cross-checks), `events/select.py` (severity, floor, track lane outside the no-overlap rule, own quota), `events/qualification.py` (track lanes qualify at their first fix), `pipeline.py` (published-unscored lane with documented `pair_status`), `dashboard/{app.js,styles.css,index.html}` (label, `--series-mlc` 8.03:1 / 5.46:1, criteria + About text, story line), `pyproject.toml`+`uv.lock` (declare `scikit-image`, already present transitively → 2-line lock diff) |

## Design decisions (and where they differ from the brief)

* **Upstream-shaped, not TempestExtremes-shaped.** Per the owner's clarification the tracker
  mirrors `tropical_cyclone.py` (peak_local_max → closed-contour validation → candidate
  association → gap filling). The physics that does not transfer to extratropical lows was
  replaced, not copied: no warm-core DZ contour, no 10 m wind gate, no IBTrACS genesis prior,
  great-circle (not index-space) distances, MSLP in hPa so the contour delta reads `3.0` like
  the PROOF recipe. `notes/channel_new_events.md` records the conflict.
* **Locality everywhere.** Contours and gap-fill searches are restricted to a window around the
  low (a contour is only accepted if its furthest point is within the acceptance radius). The
  same insight, applied upstream, is the PR below.
* **Antimeridian**: peak detection runs on a padded copy of the field, stitching is
  wrap-aware, and bboxes wrap — a low crossing 180° stays one event.
* **Merge keeps the deepest low** (TE `--mergedist` semantics), not the shallowest.
* **Bomb** = 24 h MSLP fall ≤ −24 hPa·sin|lat|/sin60°, and only where a full 24 h of history
  exists (tag, never a gate).

## Validation vs TempestExtremes

`notes/mlc_python_validation.md` (generated on the ewb LXC by
`scripts/mlc/validate_python_vs_te.py`, which is committed). Both trackers on the
**identical** extracted netCDFs, 144 frames of ERA5 0.25° MSLP, Oct 26 – Nov 30 2023:

| | python | TempestExtremes |
|---|---|---|
| nodes | 3199 | 4006 |
| tracks ≥ 48 h | **138** | **145** |
| median lifetime / min MSLP / displacement | 66 h / 975.1 hPa / 1693 km | 72 h / 977.3 hPa / 1700 km |
| deepest | 926.7 hPa | 926.7 hPa |
| bombs | 17 | 18 |
| TE tracks matched by a python track | **133 / 145** | — |
| position error, 1771 matched fixes | median **0.00°**, p90 0.00°, max 17.6° | — |
| Storm Ciaran | 954.0 hPa @ 2023-11-02 06Z, 156 h, 3456 km, −32.2 hPa/24 h, bomb ×1.52 | **identical, field by field** |
| wall time | 26.9 s detect+stitch (0.19 s/frame) | 17.9 s + 0.01 s |
| python peak RSS (144 frames resident) | 2.0 GB | — |

The median position error is *exactly* zero because both trackers snap the centre to
the grid-cell minimum; the 17.6° maximum is a handful of high-latitude tracks where
the two attach a different low across a merge/split — a tail, not a drift. 138 tracks
→ 77 published events after the §4 screens, 14 tagged bomb.

Re-run it with (scratch on tmpfs, nothing written to the 99 %-full root disk; the
scratch and the code copy were deleted after the run of 2026-11-17):

```bash
rsync -a --exclude .git --exclude data --exclude .venv --exclude tmp ./ \
  root@192.168.0.121:/root/mlc-val/
ssh root@192.168.0.121 'cd /root/mlc-val && EWB_LIVE_CACHE=/dev/shm/ewb-mlc/cache \
  python scripts/mlc/validate_python_vs_te.py --out /dev/shm/ewb-mlc/report.md'
```

It ran from `/root/mlc-val`, **never** from `/opt/ewb-live` — the live deploy was not
touched (only `ewb_live.serve` is running there; `ewb-live-pipeline` was inactive and
still is).

## Upstream PR work (kept out of this lane's commits)

`notes/upstream_pr_1.md` — one behaviour-preserving performance PR, branch
`perf/tc-locality-search` in a clone at `~/scratch/ExtremeWeatherBench` (v1.1.1 @ `c6467f4`),
**not pushed, no PR opened**. 504 ms → 3 ms spatial mask, 760 ms → 24 ms per timestep peak
search on a global 0.25° field (20-init × 120-lead eval: ~30 min → ~1 min), 69 new equivalence
tests, existing 43 TC tests pass, ruff clean. Push commands are in the note. Flagging for the
orchestrator: the owner must create/authorize the `RLK5900` fork push before this can be opened.

## Open issues

1. **`midlat_cyclone` is published, never scored** — EWB has no metric set for a pressure-minimum
   track. The pipeline writes documented `pair_status = unsupported` rows so
   `full_run_complete`/`pair_coverage` stay decidable, exactly like the `tc` lane does for
   `tropical_cyclone`. If both lanes land, resolve to one lane (or one shared `TRACK_TYPES`).
2. **Merge conflicts with the `tc` lane are expected** in `select.py`, `pipeline.py`,
   `__init__.py`, `validate.py`, `qualification.py`, `dashboard/app.js` (`storyLabel`,
   `TYPE_META`). Every one of my edits is additive and commented `mlc lane`; the
   `MLC_TRACK_TYPE`/`MLC_TOP_N` names are chosen so a post-merge rename to `TRACK_TYPES` is a
   one-line change.
3. **Runtime dependency is only numpy/scipy/skimage/xarray** — no TempestExtremes, no conda.
   `scikit-image` is now a declared dependency (it was already installed transitively via
   `extremeweatherbench`).
4. **ERA5 ARCO must be reachable** on the box for the scan (the lane streams; it does not use
   the mirror). `mean_sea_level_pressure` is still not in `truth_mirror.MIRROR_VARS`; adding it
   costs ~1 GB per 90-day window on a 99 %-full disk — owner call.
5. **Intensity axis is pressure only** (no wind series in `details["track"]`), matching the
   5-slot schema. If the owner wants wind, it is an extra column in the source read.
6. **Depth floor is a knob**: 990 hPa (the PROOF's Nov-2023 median is 971 hPa). It is
   `QualifyParams.max_min_mslp_hPa`, not a constant, so a busy/slow season can be retuned
   without touching the detector.
