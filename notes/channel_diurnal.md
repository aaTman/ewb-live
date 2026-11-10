1|# Channel: heatwave threshold diurnal cycle investigation
2|Append-only. Prefix each entry with `[agent-name HH:MM UTC]`.
3|
4|Problem (from user, 2026-11-09): the heatwave threshold map on https://ewb.espr.ai shows a diurnal cycle. A heatwave threshold should not vary with hour of day.
5|
6|Code: /home/orca/orca/workspaces/ewb-live/dogfish (branch aaTman/live-extreme-weather-dashboard). READ ONLY: other agents work there; do not edit, commit or stash in it.
7|Deployed copy + data: root@192.168.0.121 (hostname ewb), /opt/ewb-live. All compute runs there, never on orca. Leave other projects on that box alone.
8|
9|[diurnal-code 12:4x UTC] Code trace complete (dogfish worktree == deployed copy on ewb for the two key files, md5-verified: owb_clim.py e2cf83.., exceedance.py 81b07b..).
10|1) The threshold IS per hour-of-day, by design: ewb_live/climatology/owb_clim.py:89-98 (_window_index: for each (dayofyear, hour) pool raw 6-hourly ERA5 t2m within ±10 days), :117-121 (store dims quantile×dayofyear×hour×lat×lon, hour anchors {0,6,12,18}). This mirrors both EWB's shipped climatology (notes/ewb.md:65: dims dayofyear, hour [0,6,12,18]) and the legacy OWB clim.py (SPEC.md:103-106). No daily-max/daily-mean aggregation exists anywhere in the threshold build.
11|2) Matched by valid (day, hour) everywhere — no init/valid mixups, no UTC/local mixup:
12|   - detection: third_party/ExtremeWeatherBench/data_prep/temperature_events.py:383-406 — sel(dayofyear, hour) of each 6-hourly step's own valid time, exact match, then daily = resample('1D').min() over the 4 steps (AND rule suppresses the hourly variation at event level).
13|   - raw map: ewb_live/viz/exceedance.py:241-257 (_step_heat_freeze: hi = clim_hi.sel(dayofyear=doy, hour=tt.hour) per frame) and :500-502 (frames only at 00/06/12/18 UTC).
14|   - viz detail: ewb_live/viz/build.py:234-297 (thresholds_for indexes by valid-time doy+hour) ; daily pass :375 min over 4 steps ; model side :351-353 (steps at lead+24k+h+off == the same valid times as thr4) — consistent.
15|3) So the map's diurnal cycle is a property of WHAT the raw map shows, not a coding error: it publishes the per-step mask (v > p85(doy,hour)) & land WITHOUT the daily AND-rule (exceedance.py docstring line 5-7: 'BEFORE the AND/3-day/area filters'). Both t2m and the threshold carry the solar diurnal cycle; exceedance probability peaks near local afternoon.
16|   Measured on ewb (/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr): p85 threshold itself swings over the 4 anchors, e.g. Sahara 12Z 30.6°C vs 06Z 17.0°C (13.6 K range), C-Asia(-23,133) 12.9 K, Central Europe 3.5 K.
17|   Measured on the published frames (data/results/raw_exceedance, last 7 days): W-Europe box (40-55N,-10-20E) heat cells ≈199/205/574/211 at 00/06/12/18Z → 2.9x pulse at 12Z (local afternoon); global total is flat (~106k cells all hours) because continents' afternoons cancel.
18|4) SPEC/notes wording: SPEC.md:78-79 requires mirroring EWB's percentile approach (hourly-native); dashboard text already states it (dashboard/index.html:182 'per day of year and hour) at every 6-hourly step'; app.js:29 criteria says 'Daily 2 m temperature above the 85th percentile'). The user's mental model ('threshold shouldn't vary with hour') matches the DAILY definition, which the pipeline does implement via the all-4-steps rule — but the headline global map deliberately skips it.
19|Proposed fix direction (not applied): render the daily layer (.run / daily rule already computed in exceedance.py) on the global map instead of per-step exceedance, or add a 'daily' toggle; changing the threshold itself to daily-max-based would break the SPEC 'mirror EWB' requirement and the detector/viz consistency.
[diurnal-data 12:31 UTC] Data findings (all compute on ewb LXC; probes in /tmp, removed after).

1) The threshold field IS per-hour, by construction.
   Field: /opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr, var "2m_temperature",
   dims (quantile=6 [0.01,0.05,0.15,0.85,0.95,0.99], dayofyear=366, hour=4 [0,6,12,18], lat=721 desc, lon=1440 0..360).
   Builder ewb_live/climatology/owb_clim.py: HOURS=(0,6,12,18); _window_index pools raw 6-hourly
   ERA5 samples per EXACT hour (hmask = hour == h) over +/-10 days x 1990-2019, quantile per
   (dayofyear, hour). So "the heatwave threshold" is the p85 of temperature AT THAT UTC HOUR -
   a per-hour-of-day climatology. Not an indexing bug: hour coord labels are [0,6,12,18] and every
   consumer selects by label (exceedance.py:250 `hour=tt.hour`; viz/build.py:297 `hi[t.hour]`).

2) Measured swing, p85, doy=313 (K): Nairobi 8.7, Brasilia 10.5, Madrid 6.0, Sydney 7.1, Moscow 2.5.
   Global land median hour-of-day swing 3.7 K.

3) The swing TRACKS LOCAL SOLAR TIME, not UTC. argmax-hour of the threshold = solar noon + 2..4 h:
   Nairobi noon 9.5Z -> argmax 12Z; Brasilia 15.1Z -> 18Z; Madrid 12.3Z -> 12Z; Sydney 1.9Z -> 6Z.
   Grid-wide (doy 313, all valid cells): 67% of cells have argmax within +/-3 h of (solar_noon+2 h);
   tropics-only 65%. The "UTC-pinned 14Z" hypothesis explains only 28% - and those are the lon~0
   cells where the two hypotheses coincide. => per-hour climatology confirmed; time-indexing-bug
   hypothesis REJECTED on the data.

4) What the map renders: dashboard/mergedmap.js - 6-hourly frames 00/06/12/18Z, time slider steps
   through them (URL state #map=YYYYMMDDHH). The heat layer paints the .run duration sidecar
   (consecutive UTC days that exceeded p85 at ALL FOUR steps, exceedance.py run_recurrence) so the
   painted field changes only with the provisional in-day AND, but it IS built entirely from
   per-hour threshold comparisons.

5) The visible diurnal flicker quantified (2026-11-01 frames, /opt/ewb-live/data/results/raw_exceedance):
   167,696 cells exceed p85 at >=1 of the 4 steps; only 36.3% exceed at ALL FOUR (the daily rule);
   26.1% exceed at exactly ONE step. So ~2/3 of the day's exceedance area blinks on/off across the
   4 frames -> a strict day/night rhythm on the slider. Global heat-cell count per step is flat
   (112k/111k/113k/106k) and the heat-layer centroid sits near 10-25E at every frame (not subsolar),
   i.e. the EVENT pattern tracks warm anomalies, but individual cells gate on/off with local time.

6) Caveats for the code tracer: two thresholds coexist - raw map uses p85 (exceedance.py:497),
   finders use p95 (owb_clim.py FINDER_PERCENTILES). app.js:29 promises "Daily 2 m temperature above
   the 85th percentile" - a DAILY threshold definition - but nothing in the pipeline builds a
   time-of-day-independent threshold; the daily rule is an AND of four per-hour tests, which is
   night-anchored (nighttime anomalies are the binding constraint; 30-day check: Sydney p85 exceedance
   actually concentrates at local NIGHT 12Z/18Z = 23:00/05:00 local).

[orchestrator follow-up, 2026-11-09] USER: "It doesn't make sense that the 00z timesteps always have an abundance of points, consistently more than 15 percent." With a p85 threshold, ~15% of cells should exceed at EVERY hour (more only with a uniform warming offset vs the 1990-2019 baseline, which should hit all hours alike). A frame that is always elevated means something specific to 00Z is wrong. Round 2: find out what. diurnal-data = measure; diurnal-code = 00Z-specific code paths. Same rules as before. Append results here and write <name>_report2.md.

[diurnal-code round 2, 12:42 UTC] 00Z-specific code paths: ALL FOUR suspects (a)-(d) come up clean — the 00Z excess is a PAINT artifact, root cause found:
1) The heat layer on the global map is painted from the PROVISIONAL consecutive-day run, not the raw mask: dashboard/mergedmap.js:309-311 paints run>0 (ramp bins at :35). run_recurrence (ewb_live/viz/exceedance.py:110-130, contract §2 'current day provisional'): R(step j) = C(D-1)+1 iff steps 0..j of the UTC day ALL exceeded, else 0. So the painted set is monotone-decreasing through the day — 00Z gates on ONE step, 06Z on two, 18Z on four — and RESETS every midnight. 00Z is structurally the maximal frame.
2) Measured on the published sidecars (ewb, last 4 days — every day, every hour): run>0 = 32.1 -> 24.9 -> 19.8 -> 16.4 % of land cells (00/06/12/18Z), sawtooth with 00Z top, 18Z bottom. Identity proof the .run labelled 00Z is the 00Z data: run>0 count == bin-heat count EXACTLY at every 00Z (119298=119298, 116935=116935, 112075=112075, 110517=110517) and is a strict subset at all other hours. Frame labels are honest ((d) rejected).
3) THE 15% QUESTION -> diurnal-data: raw per-step exceedance (.bin heat layer, fraction of 344,688 land cells) is FLAT across hours: 32.1/32.6/31.0/29.7 % at 00/06/12/18Z on 2026-11-02 (00Z is NOT the highest; 06Z is). So 00Z data has no anomaly — but ~30-32% single-step exceedance against a p85 threshold at EVERY hour is the uniform warming-vs-1990-2019-baseline offset, and only 00Z gets to show it undiluted. Suggest quantifying the whole-window per-hour exceedance frac + a 1990-2019 rerun of a sample week as the baseline control.
4) (a) rejected: 00Z takes its own date's doy at build and at apply (owb_clim.py:112-115; exceedance.py:248-251) — symmetric; leap-year pooling shifts doy>59 slots by 1 d every 4th year, uniform across hours (minor, pre-existing). (b) rejected: _window_index hmask is hour==h exact — no cross-hour pooling at hour 0; circular ±10-day wrap (:95-97) is correct at doy 366/1. (c) rejected: raw map has one source (ERA5 mirror, truth_mirror.py) for all hours; no model/lead/analysis switch, frames stop at the cutoff.
FIX (described, not applied): paint the daily quantity — C grid (end-of-day AND run) for the previous day, or AND-of-steps as of the frame — at every hour, e.g. emit a per-frame dailyMask or have mergedmap.js paint min(steps<=j) so area does not reset at midnight; or make .bin (raw per-step) the painted layer with .run as intensity. Zero change to climatology/detection.

[orchestrator, round 3] USER APPROVED a map change, assigned to diurnal-code: paint the number of CONSECUTIVE COMPLETED DAYS meeting the daily criterion (all 4 steps exceed) instead of the provisional per-step run. Heat bins: 1,2,3,4,5,6,7+. Freeze bins: 1,2,3+ (freezes run shorter). Today counts only once all 4 steps are in, or is shown visibly as provisional. Climatology/detection untouched. Branch aaTman/diurnal-cycle-check in this worktree (now fast-forwarded to the dashboard branch 15d1484). No deploy, no push.

[diurnal-data round2, 12:58 UTC] 00Z-specific measurements (ewb LXC). Headline: the raw exceedance
layer does NOT single out 00Z; the PAINTED layer (.run duration overlay, what the map actually shows)
does — structurally, every day, because of how the provisional daily rule is evaluated per step.

(a) Exceedance fraction per hour, ALL published frames (84 per hour, 2026-08-11..11-02), land mask
    built exactly like the detector (build_land_mask, DISPLAY orientation; frames are pixel-exact
    (t2m>p85)&land — I reproduced 2026110100 and 2026110218 with 0 mismatches both directions):
      00Z mean 26.4% of land (std 4.6, min 18.3, max 34.6)
      06Z mean 26.8%  |  12Z mean 25.8%  |  18Z mean 25.0%
    => EVERY hour is consistently far above 15%, not just 00Z (uniform offset vs the 1990-2019 clim
       — 2026 land warming; mean t2m-p85thr over land = -3.0K but exceedance is right-tail-driven).
    00Z is NOT the max raw hour (06Z is); 00Z/mean(others) = 1.025, >1 on 59/84 days only.
    BUT the PAINTED field (dashboard heat layer = .run consecutive-day grid, run>=1 painted):
      00Z mean 91,097 cells | 06Z 66,325 | 12Z 50,277 | 18Z 40,361
      00Z is the max hour on 84/84 DAYS (mean ratio 1.78x the other-hours mean).
    Mechanism: run_recurrence (exceedance.py:110-130) paints R(step j of day D)=C(D-1)+1 iff steps
    0..j of D ALL exceeded. The 00Z frame therefore applies the daily rule with ONE step (00Z),
    while the 18Z frame needs all four. A frame that only checks one of four steps always paints the
    most cells. This matches the user's "00z always has an abundance of points, >15%" exactly:
    00Z painted = 18-35% of land, 18Z painted drops to 5.6-20%.
    NOTE for diurnal-code: mergedmap.js paints run[li*cells+cellIdx] (mergedmap.js:277-312), so the
    site shows the .run layer whenever index.dur exists — the raw .bin heat plane only backdrops the
    binary fallback. The fix is in the provisional-rule semantics (R at step j should not be painted
    as a qualified day until the day's steps are complete, or should be marked provisional), not in
    the threshold.

(c) Source check: both truth mirror and climatology read the SAME ARCO store
    (gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3; truth_mirror.py,
    /opt/ewb-owb/clim.py:20 ARCO_URI) — one variable, point-copied per (var,time), no 00Z-specific
    source/lead. Bias maps on the mirror (mean t2m - p85thr over land): 00Z -3.04K vs -3.04/-3.09/
    -3.18K at 06/12/18Z; 00Z-minus-others land mean +0.06K, regional means within +/-0.6K. No 00Z
    accumulation/mean-field or ERA5T-vs-ERA5 signature.

(d) doy off-by-one at 00Z: rejected. Recomputing heat masks for the last 7 days with doy-1 / doy /
    doy+1 thresholds (correct hour): d0 totals 2,867,146 vs doy-1 2,842,286 and doy+1 2,900,406
    (7-day, land); d0 sits strictly between its neighbours (threshold trend, +/-0.5%), no neighbour
    matches the published masks — the published masks match d0 pixel-exactly (see (a)).

(b) PENDING: calibration of the stored p85 clim against in-baseline ERA5 (1998/2010/2019, exact
    (doy,hour) comparison, land) — running on the box (/tmp/d2b2.log), result appended next.

[diurnal-code round3 — IMPLEMENTED on branch aaTman/diurnal-cycle-check, 13:1x UTC]
Dur rev v2, six files (+139/-50), agrees with diurnal-data round2 (same mechanism, same fix site).
Painted .run sidecar re-encoded: low 7 bits = CONSECUTIVE COMPLETED UTC days meeting the daily rule
(all four 00/06/12/18Z steps exceeded) — CONSTANT across all four frames of a day (no provisional
+1 at 00Z, no midnight reset); bit 7 = today-so-far, painted as a 2px checkerboard against the
completed bin (visible provisional style). C(D)=min(C+1,127) iff all 4 steps else 0; saturates 127.
- exceedance.py: _run_day + run_recurrence v2, DUR_REV="v2"; index dur.rev gates the JS decode.
- mergedmap.js: runCount/runBin v2 decode (falls back to v1 semantics if dur.rev != "v2"), heat
  ramp 1..7+ / freeze 1..3+ (RUN_ALPHA per layer), provisional dither, chip legend auto-bins.
- styles.css: --dur-heat-1..7 / --dur-freeze-1..3 (freeze -4/-5 deleted; mergedmap.js was their
  only consumer). app.js TYPE_CRITERIA + index.html map/criteria wording now state the daily rule.
- tests/test_raw_exceedance.py: +3 v2 tests; fixed pre-existing baseline failure (layer_fn fixture
  missing the 4th labels return the .trk sidecar requires — incremental test failed on c395798 too).
VERIFY (ewb LXC scratch /root/scratch/diurnal-r3, /opt/ewb-live untouched): 21/21 tests pass.
Real-data repro (notes/repro_sawtooth_v2.py, derived from published 2026-11-01 frames):
  v1 painted cells 00/06/12/18Z: 118,191 -> 98,925 -> 87,746 -> 82,404 (the sawtooth)
  v2 solid cells:                    65,865 at ALL FOUR FRAMES (byte-identical solid planes)
  today-so-far prov bit decays within the day by design (112,075 -> 60,826), painted dithered.
PNGs: notes/heat_v2_2026110100.png / notes/heat_v2_2026110118.png.
DEPLOY NOTE (see notes/diurnal_port_notes.md): py+JS must ship together and ALL .run sidecars
regenerate (semantics change retroactively for history); .bin frames and schema unchanged.
NOT touched: climatology, event detection, .bin codec. NO deploy, NO push. Committed on branch
with Co-Authored-By trailer.

[diurnal-code, 13:2x UTC — orchestrator follow-up checks]
(1) COMMIT STATE — already done, no action taken: HEAD of aaTman/diurnal-cycle-check is
    29609e9 "viz: paint consecutive COMPLETED days ... (dur rev v2)" ending with the
    Co-Authored-By trailer; `git show --stat HEAD` = exactly the six fix files
    (mergedmap/app/index/styles + exceedance.py + test_raw_exceedance.py). Working tree has
    only the untracked notes/ artifacts (reports, PNGs, repro script) — intentionally NOT
    committed (no notes/ files are tracked on this branch; 15d1484 committed none). If you saw
    c395798 with six dirty files, you were looking at a different checkout — e.g. dogfish, whose
    copies of those six files match my c395798 baseline byte-for-byte (that was the baseline's
    purpose).
(2) PNG FLIP — render-only, JS is clean. The repro script saved the painted array with
    row0=south straight into PNG, and PNG row0 is the TOP of the image => south-up.
    mergedmap.js does not flip: its pixel->frame-row LUT comes from the projection inverse
    (mergedmap.js:245-251, j = (lat - g.lat0)/step_lat with the deployed grid lat0=-90.0,
    step_lat=+0.25 per index.json), so canvas top maps to j~720 = 90N. The v2 change touched
    only which colour is picked from the existing byteIdx/cellIdx LUT, never the LUT itself —
    same orientation code as the live v1 site. Re-rendered north-up (np.flipud on save in
    notes/repro_sawtooth_v2.py), overwrote notes/heat_v2_2026110100.png / _2026110118.png,
    and verified by vision: Antarctica along the bottom, Greenland/N-America top, continents
    in place (polar stretch = equirectangular projection, present on the real site too).

[diurnal-data round2 close, 13:34 UTC] (b) calibration RESULT — stored p85 clim vs in-baseline ERA5
(exact doy,hour; land): 1998 = 15.4/15.7/15.5/15.4% at 00/06/12/18Z (correct, +pooling hair);
2010 = 17.6-18.2%; 2019 = 18.6-19.3%. Flat across hours within each year (<=0.3pp). => clim build
is NOT hour-broken and NOT 00Z-off; exceedance drifts up ~+1.5pp/decade with warming against the
fixed 1990-2019 baseline, so 2026's ~26%-every-hour is trend, not a bug.
FINAL: (1) "00Z always more points" = painted .run layer's provisional rule (00Z frame needs 1 step,
18Z needs 4) — 00Z max on 84/84 days, 1.78x mean, mechanism exceedance.py:110-130 + mergedmap.js:277-312.
(2) ">15%" = uniform warming offset, all hours. doy-shift and 00Z-source hypotheses rejected on data.
Full write-up: diurnal-data_report2.md.

[orchestrator, round 4] USER: repeat the calibration check for FREEZES. Hypothesis: below-threshold exceedance drifts DOWN with warming against the fixed 1990-2019 baseline, which would explain why fewer freezes appear than expected. Assigned to diurnal-data.

[orchestrator (Hermes), round 4 close — FREEZE calibration RESULT, 19:55 UTC]
Root cause of both agents' bad part-1 runs (r4b/r4c1): the per-year zarr time coord is raw
int "hours since <year>-01-01" (CF units attr). np.asarray(time,'datetime64[ns]') -> all steps
decode to 1970-01-01T00:00, so every day was compared against the doy=1,hour=0 clim slice.
Fix + race-free rerun: /opt/ewb-live scratch /tmp/r4d.py (reused r4_clim.npy cache; sanity-checked
cache vs zarr), per-timestep /tmp/r4d_cal.csv. Heat columns reproduce round-2 numbers exactly
(cross-validation).
(1) In-baseline, frac of land per (year,hour), 00/06/12/18Z range:
  1998: below-p15 14.7-15.1% | below-p5 4.7-4.9% | above-p85 15.4-15.7%  => thresholds calibrated, flat across hours
  2010: below-p15 12.5-12.9% | below-p5 4.0-4.2% | above-p85 17.6-18.2%
  2019: below-p15  9.7-10.2% | below-p5 2.7-3.0% | above-p85 18.6-19.3%
  Drift vs fixed 1990-2019 baseline: freeze-p15 ~-2.5pp/dec, freeze-p5 ~-1pp/dec (-40%/21yr);
  heat-p85 +1.5pp/dec. Asymmetric tail loss. NH/SH drift similar (~14.4->9.9% / 15.5->10.1% p15).
  Monthly below-p15 pooled 11-14%, no seasonal artifact.
(2)/(3) From diurnal-data's r4c2 (race-free, stands): 2026 published freeze layer 7.2-8.1% land
  per hour (NH 5.3-6.6, SH 9.5-10.2); daily AND-of-4: freeze 2.44% vs heat 11.71% land.
  Painted 18Z runs mean land points: freeze ge1/2/3/4 = 8,422/3,401/1,482/672 vs heat 40,361/21,624/12,767/8,142.
VERDICT: user hypothesis CONFIRMED — fewer freezes is the same fixed-baseline warming effect as
excess heat, in the opposite direction; p5 (freeze) tail shrinks proportionally faster than the
p85 tail grows. 2026's ~7-8% map fraction sits exactly on the extrapolated trend (~8.5-9%).
Cleanup available: /tmp/r4_clim.npy (18GB, /tmp on ewb box) no longer needed.

[diurnal-data round4 write-up] (data-side confirmation of the close entry; part 1 = /tmp/r4d_cal.csv race-free rerun, part 2/3 = my r4c2 published-frame pass)
Freeze/heat drift side by side, land fraction of 344,688 land cells, p15/p5/p85 stored clim vs ERA5 years inside baseline:
  metric        1998     2010     2019    slope        rel 21y
  below15(frz)  14.90%   12.73%    9.95%   -2.33pp/dec  -33.2%
  below5(finder) 4.80%    4.14%    2.87%   -0.90pp/dec  -40.3%
  above85(heat) 15.48%   17.89%   18.95%   +1.67pp/dec  +22.4%
  NH below15    14.44%   11.65%    9.86%   -2.19pp/dec  -31.7%
  SH below15    15.54%   14.20%   10.08%   -2.52pp/dec  -35.1%
Per hour 00/06/12/18Z the drift is flat (1998 below15 14.7-15.1% all hours; 2019 9.7-10.2%) -> no per-hour or time-indexing component; it is pure climate drift against the frozen 1990-2019 climatology. Linear extrapolation to 2026: freeze 8.6% vs observed published-frame mean 7.2-8.1% (on-trend to slightly colder-side of trend); heat 20.3% extrapolated vs observed 25.8-26.8% (2026 ran hotter than linear trend — the frame window 2026-08-11..11-02 was an extreme season).
2026 daily rule (all 4 steps below/above): freeze 2.443% land (NH 1.22%, SH 4.11%) vs heat 11.710% (NH 11.0%, SH 12.6%). AND-of-4 retention from mean step-rate: freeze 2.44/7.6 = 32% vs heat 11.71/26.2 = 45% — the AND rule hits cold events ~40% harder because midday steps lift t2m back above p15 on marginal cold days (warming-amplified diurnal asymmetry: minima warm faster than maxima).
Painted 18Z run lengths: heat ge1/ge2/ge3/ge4 = 40,361/21,624/12,767/8,142 (31.6% of ge1 reach 3+ days); freeze 8,422/3,401/1,482/672 (17.6%). Freeze runs decay twice as fast.
Nov seasonality (in-baseline Nov below15): 1998 NH 18.8 / SH 12.1 -> 2019 NH 11.3 / SH 4.8 — NH Nov freeze still ~2x the annual mean (autumn onset) but down 40%; SH Nov collapsed hardest (12.1 -> 4.8). Hemisphere: SH drift slightly larger than NH (-2.52 vs -2.19 pp/dec).
ANSWER to 'does warming alone explain the freeze shortage': mostly yes, plus a rule asymmetry. A pure mean-shift of the distribution predicts |Δ| tails roughly equal at symmetric quantiles; observed is heat +3.5pp vs freeze -5.0pp over 21y, i.e. the cold tail erodes ~1.4x faster than the hot tail grows (nighttime-min warming / diurnal-range shrinkage). On top of that, the AND-of-4 daily rule and the run-length layer suppress the now-marginal cold events harder (32% vs 45% retention; 17.6% vs 31.6% 3+day runs). So the map is behaving exactly as calibrated in 1990-2019; the 2026 look is baseline drift + the AND rule, not a per-hour or threshold bug.
Method note (for reuse): the per-year clim2 stores keep time as raw int64 'hours since <year>-01-01' with a CF units attr — decode via xarray (or + year origin), NEVER np.asarray(t,'datetime64[ns]'); that bug silently compared every step against doy=1 hour=0 clim. Clim store zarr chunks put quantile inside chunks, so sel(quantile=) redecodes 6x — one threaded tile pass to /tmp/r4_clim.npy memmap was ~2 min vs 40+ min for the eager xarray load.

[orchestrator, round 5, 2026-11-10] USER: "deploy it" (the dur rev v2 map fix, 29609e9). Assigned to diurnal-code. All six touched files have MOVED in dogfish since baseline c395798; agents are live in dogfish (term_55602a39 "merged global map", term_b61ce383 'ar-ramp', term_f3c16ead design successor, term_e87b37a0 scoring, term_bd4274e6 score compare). Deploy = ops/remote.sh sync from dogfish, so the fix must land in dogfish's working copy.

[orchestrator, round 5 reassigned, 2026-11-10] USER asked for a pi agent to run the deploy instead. diurnal-code (Hermes) was stopped before touching anything (no rollback dir, no merge). New owner: deploy-pi (pi harness, launched in dogfish). Same round-5 brief.

[deploy-pi 01:20 UTC] ACK round 5. Rollback snapshots of the six files saved to notes/rollback_v1/ (md5: mergedmap 3fee88a4, app 181cc0db, index 1a79a169, styles 6d94e4ad, exceedance 7432aca7, test_raw_exceedance bdbf2692). Notifying term_55602a39 + term_b61ce383 to pause those files; holding edits ~2 min before 3-way merge.
[deploy-pi 01:46 UTC] term_55602a39 and term_b61ce383 no longer exist (not in orca terminal list). Notified instead the 4 live dogfish agents: term_5202a158 + term_7ad6ac24 (both pi, active, one already probing exceedance.py/servers), term_e87b37a0 (scoring), term_bd4274e6 (score compare) — all input_accepted, asked to pause the six files until DONE. Holding 2 min, then merging.
[deploy-pi 02:24 UTC] 3-way merges applied to all six files in dogfish (all agent work kept). Conflict calls: (1) mergedmap.js/styles.css: kept the AR IVT-ramp layer work (lay entry bins:5, --dur-ar-* tokens, ivt paint/hover/legend paths untouched); heat/freeze ramp follows the approved fix verbatim (heat 1-7+ / freeze 1-3+ bins, fix palette + RUN_ALPHA alpha-ramp) — this supersedes the interim 5-bin flat-alpha gold->maroon/cyan->navy heat/freeze redesign from the merged-map work; flag design/frontend to re-check palette. (2) tests: kept dogfish superset (label_sig/track/ivt tests, 5-array layer_fn fixture) + appended the 3 v2 tests; ported the 2 v1-semantics assertion tests (run_recurrence/backfill) to v2 encoding — they asserted the sawtooth behavior. exceedance.py: DUR_REV v2 + RUN_CAP/_run_day merged alongside IVT_CLASS_NAME/SCHEMA3/LAYER_REV v7 (v7 NOT bumped). node --check mergedmap.js/app.js: OK locally. No objections logged in the 2-min window; now testing on ewb. NOTE: ewb-ivt-v7 lane rebuild is RUNNING on the box (ar-ramp's, PID 358929); my forced .run regen must wait for it to finish (it will write v1-semantics sidecars + index on completion).
[deploy-pi 02:25 UTC] Tests on ewb: pytest -q tests/test_raw_exceedance.py tests/test_viz_unit.py -> 20 passed. JS syntax OK. ewb-ivt-v7 rebuild FINISHED 01:55Z (336 frames indexed, layer_rev v7, dur.rev v1 — v1 sidecars as expected); no exceedance/pipeline job running (weekly Mon 07:00Z, store 05:30Z, pipeline idle). Syncing merged tree and launching forced lane rebuild: ops/remote.sh bg durv2-rebuild python -m ewb_live.viz.exceedance --force (rewrites all 336 .bin/.run/.trk/.ivt + index dur.rev=v2; LAYER_REV stays v7 so the AR lane is byte-compatible with the just-finished build).
[deploy-pi 02:32 UTC] durv2-rebuild CRASHED after AR pass: TypeError in _daily_runs -> _run_day(list(heat), None) on the first mirror day. This bug is IN the fix commit 29609e9 itself (ch=cf=None initializer predates it but v1 run_recurrence tolerated None; v2 _run_day does not). Unit tests miss it (they never call _daily_runs) and the real-data repro was a standalone script. Deploy-pi fix: zero-init the C grids in _daily_runs when None (2-line, exceedance.py, commented). Live index untouched (dur.rev still v1 -> live JS falls back cleanly). Re-testing + relaunching rebuild.
[deploy-pi 02:51 UTC] DONE — dur rev v2 LIVE. Rebuild finished 02:48:26Z (unit inactive, no errors): all 336 frames rewritten (.bin/.run/.trk/.ivt), index.json dur.rev=v2 seed=2026072500 layer_rev=v7. VERIFY: public https://ewb.espr.ai/data/results/raw_exceedance/index.json dur.rev=v2; served mergedmap.js md5 == dogfish copy (832d38fe...); painted-solid (low-7bit) cell counts for 2026-11-01 heat 65,865 and freeze 3,085 BYTE-IDENTICAL across 00/06/12/18Z (also 2026-10-15: 31,584/6,629) — sawtooth gone; prov-bit cells decay within day as designed (112,075->60,826 on 11-01, matches report3 exactly). pytest (exceedance+viz_unit) 20 passed on ewb; node --check clean. No commit/stash/push in dogfish per brief. DONE sent to term_5202a158/term_7ad6ac24/term_bd4274e6; term_e87b37a0 (scoring) terminal not writable — could not deliver DONE (it does not touch the six files; whoever reads this, tell it the pause is lifted). Follow-ups for design/frontend: (a) heat/freeze palette reverted to the v2-series ramp (7/3 bins + alpha ramp); the 5-bin flat-alpha redesign is in notes/rollback_v1/styles.css — re-coordinate before re-landing; (b) dashboard/eventviz.js durAlpha comment claims it mirrors mergedmap heatRunBin/freezeRunBin — those helpers are gone (eventviz builds runs from event masks, functionally unaffected); (c) 29609e9 itself crashes in _daily_runs (None seed) — fixed 2-line in dogfish copy, must be carried into the branch or the next sync regresses it.

[diurnal-code (Hermes) takeover-verify, 2026-11-10 ~03:00 UTC] deploy-pi's 02:51 DONE noted; I was re-launched on round 5 after the handoff and found the deploy already applied. Did NOT re-merge or re-sync — instead verified independently on the box:
- index.json (local + public https://ewb.espr.ai/data/results/raw_exceedance/index.json): dur.rev=v2 seed=2026072500, layer_rev=v7 intact.
- served JS: curl https://ewb.espr.ai/mergedmap.js md5 == dogfish working copy (832d38fe). All six lane files = dogfish parity (last action was rsync in remote.sh run).
- v2 invariant on real data: for ALL 84 mirror days the completed-day field (r&0x7F) painted-cell count is IDENTICAL across the day's 00/06/12/18Z frames (0 violations, both heat and freeze). Raw r!=0 counts vary within a day only via the provisional bit (today's partial rule) — by design.
- pytest tests/test_raw_exceedance.py tests/test_viz_unit.py on ewb: 20 passed. JS: node --check clean (mergedmap+app).
- None-seed crash fix (deploy-pi) confirmed on box: exceedance.py md5 5a4822f3 both sides.
- Released the three still-live paused agents (term_5202a158, term_7ad6ac24, term_bd4274e6); term_e87b37a0 no longer exists.
- succ-7's rev-flip gap: no mixed state on disk now (force rebuild rewrote every .run), so the guard is defense-in-depth only; design lane still owns it.
Report + rollback recipe: notes/diurnal-code_report5.md. No commits made in dogfish.
