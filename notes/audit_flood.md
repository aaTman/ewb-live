# Flood-lane audit (agent ne-fix-flood) — 2026-11-19

Brief: owner said "floods need to be fixed" without specifics; audit the lane
against independent references before fixing. Audit performed 2026-11-19 on
the **dev build data as published** (`/opt/ewb-live-dev/data/results`, build
2026-11-18T22:50Z, window 2026-08-20…2026-11-16) plus direct re-statistics of
the stores (`data/imerg`, `data/clim/precip_era5_pooled_1990_2019.zarr`) with
independent numpy code (`tmp/audit_flood.py`, run on the LXC, log
`/var/tmp/audit_flood.log`).

## What the lane publishes today

- raw catalogue: **766** flood objects in 89 observed days (mean 8.5/day, peak
  32/day 2026-09-28); selection gate (`peak_3day >= 50 mm`, area >= 10k km2,
  1..21 d, box <= 45 deg) + top-40 cap → **40 published**.
- duration histogram of raw objects: 1d:159, 2d:161, 3d:308, 4d:87, 5d:38,
  6-8d:13.
- IMERG data reach 2026-11-16 (89/95 window days; missing 2026-08-16..19 =
  pre-store lookback, 2026-11-17..18 = beyond Late-Run lag). Run mix recorded
  in every event: 88 late, 1 early, **6 days labelled `auto`** (provenance
  bug: the label is not resolved to the concrete run).

## Findings (severity-ordered)

### F1 — ERA5→IMERG product bias inflates the lane, worst at high latitudes — HIGH
A p95 rule should flag ~5 % of *wet* days if the two distributions matched.
Measured independently (all land cell-days, 90-day window, 0.25 deg lattice):

| band | 1-day exceedance | 3-day exceedance | IMERG wet-day p95 | median ERA5 p95 thr | ratio |
| --- | --- | --- | --- | --- | --- |
| 0-23.5 | 3.24 % | 2.26 % | 28.8 mm | 17.3 mm | **1.66** |
| 23.5-45 | 1.58 % | 1.63 % | 29.5 mm | 18.8 mm | **1.57** |
| 45-60 | 1.31 % | 0.66 % | 22.7 mm | 14.4 mm | **1.58** |
| 60-90 | 0.44 % | 0.24 % | 20.1 mm | 7.7 mm | **2.62** |

IMERG (a GPM-era passive-microwave+IR retrieval) reports substantially more
extreme rain than ERA5's reanalysis precipitation for the same cell and day of
year — the known ERA5 convective dry bias plus IMERG's own wide-areal light
bias. Consequences: raw counts ~2x what a matched distribution would give;
the signal-to-noise degrades exactly where IMERG's sampling is weakest
(above 55 deg N: **66,963 exceedant cell-days = 14 % of all exceedance
north of 55 deg N**, 45,861 of them above 60 deg N, where GMI-level
microwave coverage ends and IMERG is IR-only). This is the main driver of the
"too many/fragmented" impression and of spurious Siberia/Canada/Scandinavia
hits (191 raw objects with midpoint above 45 deg latitude).

Fix direction (owner rule keeps ERA5 as the QPF climatology source): a
**bounded per-band bias rescale** of the ERA5 threshold against the IMERG
distribution (quantile-map at the band level from the in-window IMERG
sample, k = ratio of wet-day p95s, capped, e.g. |lat|>55 handled as IR-only),
+ restrict the 3-day lane's high-latitude trust. Documented in
`notes/flood_bias_note.md`; parameters in the lane, not hardcoded per event.

### F2 — event definition fragments spells and splits one flood into pieces — HIGH
- 1-day-lane-only objects (766 − those with `accum_3d>0` cells) flood the raw
  catalogue and are then *rejected* by the publish gate (`peak_3day >= 50`),
  i.e. the lane spends most of its event budget on objects it can never
  publish: the raw/published gap is mostly this, plus the top-40 cap.
- Duration histogram shape (308/766 exactly 3 days) and per-day counts show
  monsoon-burst fragmentation: an object qualifies, weakens below the
  seed area for one day, ends, and re-seeds next day 200 km away —
  one real flood, several raw objects.
- Tracker params that matter: `end_area` = `seed_area` (10k km2) with no
  hysteresis, `min_days=1`, `max_ramp_days=3`, region radius 1.5 deg.

Fix: require at least one lane-3 exceedant cell OR >= 2 active days to
*create* an event (a bare single-day daily-lane blip stays raw evidence, not a
candidate); end an object only after **2 consecutive** sub-seed days
(hysteresis); merge objects whose boxes overlap and whose intervals touch
after tracking. All inside `events/flood.py` (lane-owned file).

### F3 — no flood detail view in the UI — HIGH (visible defect)
Flood events have no viz.json; the detail panel shows the honest empty state,
and `test_unrecoverable_full_band_bbox_draws_no_band` (an e2e test that opens
a flood and waits for `#detail-viz .ev3-panel`) times out on dev *because of
this*. Fix: a proper flood EventViz (`kind: "flood_accum"`): per-day
exceedance footprint map on the event box + time series of the box's max
1-day/3-day accumulation, from the same IMERG/threshold join the detector
used; wire into `ewb_live/viz` + `eventviz.js`; update the e2e test's
premise. (Scoring rows are still absent → the panel keeps the
"Not scored" note; F5.)

### F4 — provenance: `auto` appears as a run name — LOW
`status.json`/`details.data_source.runs` mix `late/early` with 6 days labelled
`auto`; `load_observations` copies the label without resolving it through
`imerg.product_for`. Cosmetic but it is a data-provenance claim in the UI.

### F5 — "Not scored" — verify/keep or fix per the brief
Check whether CIRA MLWP QPF in the virtual store (icechunk, inits only
~40 d deep at Nov 17-18, leads per `store_metrics`) can score the events, and
either implement the smallest honest version or document why not.
(As-is the UI says "no EWB benchmark for this lane yet", which is currently
true — the pipeline ships an `unsupported` pair status.)

### Checks that came back clean
- **Units / accumulation**: store daily `precipitation` is mm/day (product
  "mean half-hourly rate x 24" convention handled in `imerg.py` with
  unit-derived conversion and a global-mean plausibility guard); raw granules
  are not retained after ingest (`data/imerg/raw` empty, 40K), so the check is
  indirect: status.json global means 2.0-2.3 mm/day (plausible), no 24x
  outliers (max day 372 mm/day Late 08-20, plausible for a monsoon cell),
  peak event values (<=484 mm/day) physically sane. No raw-vs-store
  recompute possible offline; flagged as residual uncertainty.
- **Land/ocean gating**: only 802 (0.17 %) exceedant cell-days fall in cells
  <1/3 land (coastal Natural Earth 110 m generosity, known); the land gate
  works. Sahara box exceedances (903 cell-days) are real Sahel convection
  above the 20 mm floor, not NaN-threshold leaks.
- **Latency/reach handling**: provisional flags, run mix, missing-day
  poisoning and the "never count a partial day" screen behave as documented
  (89/95 days present matches the store exactly).
- **Dateline**: tracker/Event bbox use the repo's `lon_min > lon_max`
  eastward-arc convention correctly; map rendering of wrapped boxes is the
  ne-fixes2 lane (not touched here per brief).

## F0 — floods are invisible on the merged map (OWNER 2026-11-19, above all of the above)
Verified by the owner on :8051 (flood tab, map=2026102418): the only flood
marks are the 2 events whose interval covers that day (dashed boxes, ~20 px);
the other 38 flood events live on other days, the raw-shading layer that makes
heat/AR visible per day has no flood counterpart (`mergedmap.js` LAYERS says
"no raw exceedance layer exists for the flood lane"), there is no flood legend
entry, and selecting the flood chip leaves the heat/AR raw layers painted —
`layersForTab()` deliberately no-ops for lanes without a raw layer, so red
Africa and green AR bands read as flood evidence. Defect 0 = give the flood
lane its own daily exceedance raster (union rule, shaded by accumulation,
lanes-only shading when one chip is selected), a legend entry with the mm ramp
(AA light+dark), honest provisional/reach treatment, keep the event boxes,
and e2e coverage that the flood tab paints non-empty marks + legend.

**Status: fixed on this branch (2026-11-19).** New `ewb_live/viz/flood_frames.py`
publishes one class grid per day (`raw_exceedance/flood_YYYYMMDD.cls`, zlib u8,
display orientation) coding `max(daily_mm, accum_72h/3)` into 5 classes at
20/50/100/200/350 mm/day-equiv — the same exceedance rule the detector uses,
via its own `build_scan`, so the map and the event evidence cannot disagree.
Frames are served past the ERA5 score cutoff (observation truth, not model
availability); days the IMERG store cannot supply (Late-Run lag tail) are
recorded `absent` and draw nothing. `mergedmap.js` paints it as the flood
raster (key `l`), the legend shows the mm ramp under a `mm/day-equiv` unit
line, and `layersForTab()` now isolates a single selected chip — flood chip
selected ⇒ only the flood layer, no heat/AR bleed. Event boxes are kept.
`serve.py` allowlists `flood_\d{8}\.cls`; `weekly.py` step 6b2 and
`dev_site.sh lanes` build the frames with the raw layers. Verified in browser
on :8051 (1440+390, light+dark): ~11k painted flood px on 2026-10-24, legend
stops render, `on` state = flood-only with the chip selected; e2e coverage in
`tests/e2e/test_dashboard_fe.py` (`test_flood_layer_paints_marks_and_legend`,
`test_flood_chip_isolates_raw_layers`) on a pinned synthetic frame.
