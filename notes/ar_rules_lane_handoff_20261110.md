# AR rules lane — handoff + deploy-collision postmortem (2026-11-10)

Lane: the prime-meridian/seam thread (this worktree, `bug-catcher`). Peer lane:
PI-BUG-CATCHER-2, whose write-up is `notes/ar_outage_20261110.md`. Read that one
first; this file only records my state, the collision, and the review points.

## 1. My work is a commit now, not a working tree

`wip/ar-rules-20261110` = `d0c3f3f` (parent `424268e`). Files in this worktree are
**frozen by request** — I am not editing or scp-ing
`ewb_live/events/{ar,ar_filter,seam,ar_mask}.py`, `ewb_live/viz/{build,exceedance}.py`,
`dashboard/mergedmap.js`. Take the hunks with

    git show wip/ar-rules-20261110 -- ewb_live/events/ar_filter.py

Contents (contracts, not line numbers):

- `events/ar_mask.py` — EWB's AR detector with longitude-periodic Laplacian,
  dilation and component labeling; `is_periodic_lon` refuses to wrap a regional
  crop; `build_atmospheric_river_mask_and_land_intersection` is a drop-in.
- `events/ar_filter.py` — linking needs overlap ≥ 0.25 **and** parent→child
  centroid ≤ `LINK_MAX_KM` (900 km/6 h) **and** parent age < `LINK_MAX_STEPS`
  (40 steps). Tropical screen keys on the area-weighted **centroid** staying
  inside ±15° for `TROPICAL_PERSIST_STEPS` (4) consecutive steps (owner
  2026-11-10: "a touch isn't important… if it stays for a day, remove the ID'd
  AR entirely"). Also fixes a live crash: the ghost-bridge drift test indexed
  per-step centroid tables with *track ids* (real failure id 36 vs 22 labels).
  Imports the box hotfix (`_storm_pass` gated on the physical mask).
- `viz/exceedance.py` — 4th `.bin` plane `bridged` (always present, planes 0-2
  never move), `LAYER_REV="v8"`, detector via `ar_mask`.
- `viz/build.py` — AR compute window padded 25° lon / 10° lat
  (`AR_COMPUTE_PAD_*`) because the mask is computed **on the crop**, so a tight
  crop cuts the plume it is supposed to show; `_ar_compute_bbox` computes
  globally instead of cropping when the padded arc crosses the grid's lon origin
  (that crop would come back with a non-monotonic longitude axis). Per-day AR
  masks in `obs.daily_bits` so the home map stops repeating the anchor frame.
- `AR_RULE_REV = "v7"`, SPEC.md documents both rules and the measured failure.
- Measured cost of the caps on 80 consecutive real frames: tracks 791 → 879,
  longest 31 → 24 steps, cells in ≥8-step tracks 69% → 55%, cells in 1-step
  tracks 7.6% → 15.4%. Identity churns; pixels do not.
- Tests: `tests/{test_ar_filter,test_seam,test_raw_exceedance,test_viz_unit}.py`
  in the same commit. Offline suite was green (237 passed / 29 skipped); 61 of
  them also ran green on the box against the deployed copy at ~18:58Z.

## 2. Why the 18:59Z rebuild ran the old code (mechanism, not theory)

The deploy landed and was verified, then was replaced while the run was live:

| time (Z) | event |
|---|---|
| 18:58:06 | box backup `backups/pre-arrules-20261110T185806Z/files.tgz`; 7 files copied; `compileall` + `node --check` OK; md5s read back from the box (`ar_filter.py` = `ac75321c…`, `exceedance.py` = `6ecedba8…`, `mergedmap.js` = `94d442d5…`) |
| ~18:58 | box venv ran the AR/viz/raw tests against that tree: 61 passed |
| 18:59:16 | `ops/manual_arrules_rebuild.sh` launched (weekly → viz → exceedance) |
| later | box tree reverted: `ar_filter.py` = `eab8d4bf…`, `exceedance.py` = `44ef8ceb…`, `build.py` = `f6c4d5be…`, `ar.py` = `4c49e7e4…` — all == `424268e`/dogfish HEAD; `mergedmap.js` = `9d34557f…` (live JS, box mtime 19:37:44) |

The reverted files are owned by uid 1000 with mtimes that predate the copy
(`ar_filter.py` 12:01:41, `exceedance.py` 12:57:26, `build.py` 02:40:44) — the
signature of `rsync -a`, i.e. `ops/remote.sh sync|run|bg` from the peer lane's
worktree, which pushes a whole tree and is last-write-wins. `ar_mask.py`
survived only because the peer tree has no such file (rsync without
`--delete` leaves extras). My 18:59 launch therefore inherited old code for
most of the lane; artifacts published 19:29-19:41 (`events.json`,
`global_timeline.json`, 4 incremental frames) are old-rule but coherent, as the
peer lane says. Script is now `ops/manual_arrules_rebuild.sh.disabled`.

**Rule both lanes should follow:** the box has one code tree and `remote.sh sync`
overwrites it wholesale. Only the lane that owns the rebuild syncs, and it
syncs from one merged tree (dogfish per the lane rule). Cheapest enforcement:
`flock /opt/ewb-live/.deploy.lock` around sync+run, and print `md5sum` of the
changed files on the box *after* sync and *before* the long step.

## 3. Review points on the peer lane's three additions

1. **`.ivt` from the IVT field (schema 2: classes in low 3 bits, bit 7 = IVT≥400).**
   Agreed, and it supersedes one of my hunks: I made the class plane *exclude*
   ghost cells (`ivt_class_plane(ivt, ar & ~bridged)`), which deepens the
   paint-coupling he is removing. Drop mine when his lands. Must move together:
   `ivt_class_plane`, `mergedmap.js` AR ramp + hover (`ivtAt`/`curIvt`),
   `index["ivt"].schema`, `tests/test_raw_exceedance.py`, and the AR-ramp e2e
   tests in `tests/e2e/test_dashboard_fe.py`.
2. **TC screen at step level.** Right diagnosis (a `tc` mega-track blanked whole
   ARs), but it contradicts SPEC §"AR tropical exclusion": keep/drop is
   *object*-level so an AR never blinks. If we go step-level, that sentence and
   the owner contract need re-signing. Softer version that keeps the contract:
   drop only the steps within ±`TC_SCREEN_HOURS` of the touch and their
   descendants, keep the track id, and say so in `dropped[]`.
3. **`bbox` + centroid in `ar_tracks.json` `dropped[]`.** Yes; also mean IVT and
   per-step cell counts — his §2 diagnostic gap is real and this is what closes
   it. Cheap, and it makes a report like this answerable from published data.

Open question for him: which tree is authoritative for `ar_filter.py`. My caps
are already merged-compatible only if his step-level TC keeps
`track_ar_components(mask, valid_times, latitude, longitude)` (the coords are
what the drift cap and the centroid test need) — a signature change there will
break `tests/test_ar_filter.py` and `tests/test_seam.py`.
