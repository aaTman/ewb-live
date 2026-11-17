# midlat_cyclone: algorithm decision + build plan

Role `mlc-research`, 2026-11-17. Evidence: `notes/mlc_proof_report.txt` (one-month PROOF on the
ewb LXC) and the harness `scripts/mlc/` (both committed on this branch). Compute-only-on-ewb,
no shared production file touched by this lane.

## 1. Decision

**TempestExtremes** `DetectNodes` + `StitchNodes` (Ullrich et al. 2021, *GMD* 14, 5023–5048,
<https://doi.org/10.5194/gmd-14-5023-2021>; framework paper 2017, *GMD* 10, 1069–1090,
<https://doi.org/10.5194/gmd-10-1069-2017>), run on **`mean_sea_level_pressure` alone** —
0.25°, 6-hourly ERA5 (ARCO / the ewb mirror). TempestExtremes is a *feature finder only*; the
event qualification, screens, severity tags and the `details["track"]` schema are our code.

Validated recipe (Oct 26–Nov 30 2023, global, ERA5 0.25°):

```
DetectNodes  --in_data_list <files> --searchbymin msl \
             --closedcontourcmd "msl,3.0,6.0,0" --mergedist 6.0 --minabslat 5.0 \
             --outputcmd "msl,min,2" --out_header --out nodes_XXXXXX.dat
StitchNodes  --in_list <node file list> --in_fmt lon,lat,msl \
             --range 6 --mintime 48h --maxgap 12h \
             --out tracks.csv --out_file_format csv --out_seconds
```

Install (already on the box): micromamba env `/opt/ewb-live/opt/mm/envs/te` (281 MB),
conda-forge `tempest-extremes` **2.4.2**, **BSD-2-Clause** (verified from the conda-meta record).
It is a compiled binary called with `subprocess` — never imported into `.venv`.

## 2. Why this one (survey)

| method | input fields | packaging / licence | verdict |
|---|---|---|---|
| **TempestExtremes** (Ullrich et al. 2021, 2017) | MSLP only, optionally θ/vorticity/wind; spherical, unstructured grids | conda-forge, **BSD-2-Clause**, pip-buildable, active | **chosen** |
| TRACK (Hodges; scheme/statistics e.g. 2008, *MWR* 136, 1758–1777, <https://doi.org/10.1175/2007mwr2299.1>) | PV / 850 hPa vorticity blobs + background field | Fortran, source from U. Reading, no pip/conda, own NetCDF build, registration-style academic terms | rejected: packaging/licence friction, heavier inputs |
| Melbourne scheme (Murray & Simmonds 1991; intercompared in Raible et al. 2008, *MWR* 136, 880–897, <https://doi.org/10.1175/2007mwr2143.1>) | needs **SLP reduction** from surface pressure + 10 m winds | no maintained package | rejected: SLP reduction over orography is the classic spurious-low source (see §5) |
| Wernli & Schwierz 2006, *JAS* 63, 2486–2507, <https://doi.org/10.1175/JAS3766.1> (the ETH European database; Part I is the method paper) | MSLP + θ contour geometry + relative vorticity maxima | code not packaged | rejected: not distributable, Europe-oriented |
| **IMILAST** (Neu et al. 2013, *BAMS* 94, 529–547, <https://doi.org/10.1175/BAMS-D-11-00154.1>) | — (intercomparison of the above) | — | adopted as **evidence**, not as a method |

IMILAST (and the earlier COST-726 comparison) is the reason not to claim "most accurate": schemes
disagree by roughly a factor of two in **how many** cyclones they count and in the tails, while
agreeing closely on the position and intensity of the deep, impactful lows. So a tracker choice is
defended by a **known-storm check + explainable screens**, not by a benchmark score. That is what
the PROOF did: Storm Ciaran, 2 Nov 2023.

PROOF result. **Ciaran is found by every variant tested.** The detailed track below is the one the
report prints in full (the vorticity-gated `vor3v` run): one continuous track 2023-10-31 06Z →
2023-11-05 18Z (132 h), deepest **954.0 hPa** at 2023-11-02 06Z 50.5°N 1.5°E — landfall in
Finistère at 00Z 2 Nov within one 6-hourly step of the documented position — displacement 3138 km,
24 h fall 32.2 hPa = **1.52× the bomb threshold**. That 954.0 hPa equals the ERA5 field minimum in
a 45–58°N / 25°W–5°E box over those days, i.e. the tracker's central pressure is the raw field
minimum, not an artefact. The recommended MSLP-only variant (`vor3`) finds the same storm as a
156 h track (earlier genesis, same 3 hPa recipe). For the recommended variant, Nov-2023 window:
**99 tracks, median lifetime 66 h, 70 tracks with ≥1000 km displacement, 13 bombs, mean |lat|
58.3°**; the deeper lifetime/pressure stat block in the report belongs to `vor3v` (65 tracks,
median 66 h, median min MSL 971 hPa, deepest 927.5 hPa, 12 bombs).

## 3. Do not copy the tutorial recipe

The vorticity threshold `vor850 >= 2e-10 s⁻¹` circulating in TempestExtremes "extratropical
cyclone" tutorials is a **no-op** at 0.25° (identical node counts: 7792 with and without). Scan:
≥2e-6/1e-5 → 7792, ≥2e-5 → 7780, **≥1e-4 → 2720**, ≥5e-4 → 222 nodes. `--thresholdcmd` is an
*exists-within-radius* test, not an average, so it is a weak filter by construction. Use
`--closedcontourcmd msl,3.0,6.0,0` for strength selection instead of a vorticity gate.

Keeping the 1e-4 vorticity gate (variant `vor3v`, the *headline variant in the PROOF report*) costs
5× the I/O (the ARCO u/v store is chunked `(1,37,721,1440)`, so one level reads the whole
37-level block: 8.5 s/frame vs 1.3 s/frame for MSLP) and removes 34 of 99 tracks — weak and
early-stage lows along with junk. **Refinement of the report's headline: run MSLP-only (`vor3`) +
our own deterministic screens in production**; the vorticity gate stays a documented tuning knob to
revisit after the first live month, not the first lever. The orography percentages in §4 are
measured on the `vor3v` node set; the MSLP-only node set is a superset with the same geography.

## 4. Event qualification rule (mlc-impl implements this, all in our code)

Raw tracks from TempestExtremes are *systems*; an EWB `midlat_cyclone` **event** is the qualifying
sub-window. Gates (all on the track, all explainable in the About dialog):

1. **lat band**: first fix (genesis) in |lat| ∈ [25°, 70°] — extratropical band, polar side of the
   trade-wind belt. (Nov 2023: 53/65 tighter-variant tracks inside 25–70°.)
2. **orography gate at genesis**: genesis fix must sit over ERA5 orography < 500 m
   (`geopotential_at_surface / 9.80665`, static field from the ARCO store; `scripts/mlc/
   fetch_topo.py` + `check_orography.py`). This is the Murray–Simmonds failure mode we are
   avoiding: 16.4 % of nodes sit above 500 m, 32 % in the 20–40° orogeny belt (Tibet, Andes,
   Greenland) vs 8.6 % in 40–65°.
3. **lifetime ≥ 48 h** (already the tracker `--mintime 48h`) **and displacement ≥ 1000 km**
   (great-circle first→last fix; Nov 2023 median 1574 km). The displacement floor also removes
   stationary cold-core cut-off lows, the other known MSLP-scheme artefact.
4. **depth floor**: min MSL ≤ 990 hPa anywhere in the qualifying window (Nov 2023 median 971 hPa).
5. **no tropical origin**: reject a track whose genesis fix is within 500 km / ±24 h of an IBTrACS
   fix with `nature` tropical, looked back 7 days from genesis — reuse the `tc` lane's
   `ewb_live/sources/ibtracs.py::load_tracks`. One system, one event: when it fires, **tc owns the
   track** (this is exactly the post-tropical-Philippe → Ciaran case, so decide and document it).

Severity **tags**, never gates: `bomb` = max over any 24 h of ΔMSL ≤ −24 hPa · sin|lat|/sin60°
(Sanders & Gyakum 1980, *MWR* 108, 1589–1606,
<https://doi.org/10.1175/1520-0493(1980)108<1589:sdcot>2.0.co;2>) → `details["bomb"]`,
`details["bomb_ratio"]`, `details["max_24h_fall_hPa"]`; Nov 2023: 12 bombs (Ciaran 1.52×). Also
`min_msl_hPa`, `displacement_km`, `lifetime_h` for `select.py` ranking.

## 5. Data plumbing (answers "does it run on SLP alone?") — yes

* Needed: **one variable**, ARCO `mean_sea_level_pressure` (= `air_pressure_at_mean_sea_level`,
  the name already in `ewb_live/sources/registry.py`). It is **not** in
  `ewb_live/scoring/truth_mirror.py::MIRROR_VARS` (t2m, q, u, v ≥ 300 hPa).
* **Do not add it to the permanent mirror**: the ewb root disk is at **99 % (6.0 G free**,
  `data/cache` 316 G) as of 2026-11-17. Stream the scan window (+ 6-day warm start for stitching)
  from ARCO into **`/dev/shm/ewb-mlc`** (252 G tmpfs, 256 G RAM), run TempestExtremes, delete.
  Measured: 1.3 s per global 6-hourly MSLP frame → a 90-day window ≈ 380 frames ≈ **8–9 min
  single-core**; `DetectNodes` on 141 global frames = **18 s on ONE core**, `StitchNodes` 0.03 s;
  scratch ≤ 1.5 GB; node+track files 4 MB. The weekly cycle gains ~10 min (16 cores on the box).
* `scripts/mlc/extract_month.py` already does the ARCO → TempestExtremes conversion (lon shifted
  to [-180,180] per the repo bbox convention, TE's default lat/lon names, zlib-4). 141 frames ×
  4 variables = 1.3 GB; MSLP-only is the cheapest of the four.
* **Do not mirror u/v at 850 hPa** (§3).

## 6. Output mapping (tc's `details["track"]` contract)

Rows `[ISO-8601 UTC "Z", lat, lon∈[-180,180], intensity, pressure]`, 5 slots, ascending, raw
6-hourly cadence (no resampling), `null` never `NaN`, plus
`details["intensity_units"]`, `details["track_crosses_antimeridian"]` (split where |Δlon| > 180),
bbox = padded track extent with the `lon_min > lon_max` wrap convention. For MLC slot 3 = slot 4 =
**central MSL in hPa** (`intensity_units: "hPa (MSL, lower = stronger)"`) — pressure *is* the
headline intensity here; flag for the design pass if it wants a second series. `Event.peak_value`
= min MSL (hPa). `Event.start/end` = the qualifying window (§4), the track may extend beyond it.

## 7. Build plan for `mlc-impl`

New modules (all logic here):

* `ewb_live/sources/mlc_era5.py` — ARCO → TE netCDF in `/dev/shm` (copy of
  `scripts/mlc/extract_month.py`, keep `--nowinds`, add `--shrink-window`/cleanup).
* `ewb_live/events/mlc_track.py` — pure functions: TE CSV → rows, §4 gates, §4 tags, bbox,
  antimeridian flag. Unit-testable on a fixture CSV.
* `ewb_live/events/mlc.py` — `scan_mlc_events(start, end, **kw)`: window = [start−6 d, min(end,
  truth_cutoff)], extract → `subprocess` DetectNodes → StitchNodes → qualify → `Event` list;
  cache the tracks CSV per window under `data/cache/mlc/`; **fail soft** (log + `[]`) when the TE
  env is missing, like the raw-exceedance-lane fallbacks.
* Shared files — additive, one line/block each: `events/types.py` (`EVENT_TYPES += "midlat_cyclone"`),
  `events/__init__.py` (dispatch branch + cache tag), `events/validate.py` (title label, peak
  range 900–1015 hPa, 5-slot track check), `events/select.py` (quota + "MLC never merges with a TC
  track"), `dashboard/detection.py` (About text in the existing style: "closed 3 hPa MSLP low,
  ≥48 h, ≥1000 km, ≤990 hPa, 25–70°, no tropical origin, bomb tag"), `dashboard/{app.js,
  index.html,styles.css}` (label + colour only — design pass restyles later).
* Tests: `tests/test_mlc_track.py` (fixture track CSV: bomb rule, lat band, orography gate,
  displacement floor, antimeridian bbox, IBTrACS-overlap rejection) and a
  `tests/test_mlc_events.py` that monkeypatches `subprocess` with a canned TE CSV — no network, no
  TE binary in CI. Reuse `scripts/mlc/` as the research harness; it is committed.
* Ops: TE install = `scripts/mlc/setup_te.sh` (idempotent, isolated under `/opt/ewb-live/opt`,
  does not touch `.venv`). Record it as a runtime dependency of `midlat_cyclone`; wire the scan
  into `ops/events_rebuild.sh` (on main) rather than a new timer.

## 8. Open issues / owner

1. **Disk** — ewb root at 99 % (6.0 G free). My PROOF scratch is deleted (freed 1.6 G); scratch
   must stay on `/dev/shm` for every future run. `data/cache/subsets` is still the big lever.
2. **Orogeny crossing tracks** — the genesis gate does not stop a real low that later crosses the
   Plateau/Andes, nor a false low that *starts* below 500 m and is then carried over land. If
   false events appear, add "≥2 consecutive 6-hourly fixes inside the gate" before touching the
   recipe.
3. **vor3 vs vor3v** (99 vs 65 Nov tracks) is the one tuning decision with real event-count
   impact; recommendation is 99 + screens, revisit after one live month.
4. **Intensity display** — MSLP-only means no wind column in the track view. If the owner wants a
   wind axis, it costs the 5× u/v read (or 10 m wind, which is a *separate* single-level variable
   and cheap: worth a decision before impl, since `u10/v10` are single-level and do not pay the
   37-level-block penalty).
5. **Ciaran/tc ownership** — the overlap rule (§4.5) decides which of the two lanes publishes the
   post-tropical system; needs the orchestrator to confirm both lanes implemented the same
   direction.
