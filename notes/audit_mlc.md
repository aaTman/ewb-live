# MLC lane audit — ne-fix-mlc (2026-11-19, owner request "mid-latitude cyclones … need to be fixed")

Audited the published MLC lane on the dev site (LXC `/opt/ewb-live-dev`, 90-day
build window 2026-08-20…, truth cutoff 2026-11-12T23Z) against **independent
references**: the IBTrACS v04r01 archive (what the TC lane itself publishes)
and the ARCO ERA5 source store — not the lane's own tests. Raw catalogue
143 events, 80 published by the quota (`select.TOP_N["midlat_cyclone"]=80`).

## F1 (HIGH) — post-tropical TCs leak in as MLCs: the genesis screen is structurally blind

`mlc.a1872482f814` "Midlatitude Cyclone 2026-10-21 to 2026-10-24" (genesis
2026-10-18T06Z at 34.25N −71.0W, min MSLP 983.4 hPa, lifetime 162 h) is the
**post-tropical continuation of Hurricane Josephine**, published as a separate
mid-latitude low — two events for one system, violating the lane's own
"one system, one event" rule (module docstring screen 5).

Measured overlap (dev build data × IBTrACS fixes, 500 km / ±12 h per fix):

* **21 of its 28 fixes** lie within 500 km of an IBTrACS JOSEPHINE fix, from
  10-18 (34.6N −70.8 vs Josephine ET 34.6N −70.8 — same grid cell) to 10-22.
* **0 of 28** are within range of a *tropical-nature* fix: Josephine is
  `ET` (extra-tropical) from 10-18T18Z and `SS` from 10-21T18Z throughout.
* Whole-window sweep of all 143 raw MLCs against all IBTrACS fixes: this is
  the **only** event with ≥2 overlapping fixes — the screen otherwise works.

Root cause, two compounding blind spots in `tropical_origin_checker`
(`ewb_live/events/mlc.py`):

1. **It tests genesis only.** A post-tropical transition spawns an MLC track
   that *starts on the TC's track* and then diverges; what proves "same
   system" is the track overlap, which nothing checks.
2. **It tests genesis only against NATURE ∉ {ET, MX}.** Here every fix within
   reach of genesis was `ET`/`SS` — the exact fixes the screen throws away.
   And the tropical phase had ended >7 d before genesis in the other basin
   segment, so widening the lookback alone would not catch it either.

(Consequence today is masked on dev — the quota happened to drop this event,
sev 44 < kept-min 89 — but a *weekly* build publishes it: it is a detector
defect, not a curation artefact.)

**Fix (systematic rule):** reject an MLC track when **≥2 of its fixes** lie
within `tropical_radius_km` (500 km) and ±12 h of **any-nature** IBTrACS fix
(tropical or ET/SS/MX…). Kept as a separate screen from the genesis test so
the Ciaran case (genesis near a *tropical* system up to 7 d back) stays
covered. Track-level, not event-level: runs in `scan_mlc_events` next to the
genesis check, fail-soft like it (no IBTrACS → screen off + log), and counted
into `lane_status.screened_by`.

## F2 (MEDIUM) — the quota drops the deepest storms: severity lets lifetime outrank depth

`mlc.severity = depth_below_990 × lifetime_days × (1.5 if bomb)` is a plain
product, so a shallow long-lived low outranks a monster. Measured on the dev
quota (63 quota-drops, all ids from `curation.json`):

| dropped | kept |
| --- | --- |
| sev 3–88 | sev 89–682 |
| **min MSLP down to 954.3 hPa** | |

The eight deepest quota-drops: 954.3 (48 h), 955.6 (54 h), 959.9 (54 h),
960.1 (54 h), 960.7 (48 h, bomb), 960.7 (66 h), 954.6 (60 h), 955.3 (60 h) —
hurricane-strength extratropical lows dropped while 966–975 hPa lows with
longer lifetimes stay (every kept event scored ≥ 89 on the product; the
deepest drops score 71–84 because 48–60 h lifetimes are only 2–2.5 days).
A 954 hPa September low is the event
people remember; the ranking is "arbitrary" in exactly the way the owner
suspected. (First draft of this line claimed ~985 hPa/10-day wanderers were
kept — wrong: under the old product they were also dropped, the real
outvoters are 7-day-class 966–975 hPa lows.)

**Fix:** make depth dominant — `depth² × min(days, 7) × bomb-nudge` — so
intensity cannot be out-voted by lifetime, and 954 hPa/48 h always outranks
989 hPa/300 h. Cap stays 80 (it is the tab's honest size budget, env-tunable
via `EWB_LIVE_TOP_N_MLC`); the defect was *which* 80.

## F3 (CONFIRM, no code change) — data ending 2026-11-13 is source reach, not a bug

ARCO's **time index runs ahead of its data** (nominal axis to 2050). The
lane reader (`sources/mlc_era5.read_mslp_window`) already drops all-NaN tail
frames ("measured 2026-11-18: every cell of 2026-11-12T00Z…2026-11-17T00Z is
NaN"). Live measurement this pass on the LXC (probe of
`mean_sea_level_pressure`, 6-hourly, 2026-11-06…11-20):
frames 2026-11-13T00Z onward **all-NaN, last real frame 2026-11-12T18Z**
(lag 6.2 d as of 2026-11-19T03Z). Latest raw MLC end is 2026-11-13T00Z
(= last core fix 2026-11-12T18Z + the midnight-end rule) — consistent. TC runs later (IBTrACS lag 2 d) and flood later (IMERG near-real
time) because their *sources* are faster. Honest per-lane reach; the only
open item is saying it in the UI, which is a design call (integrator's
pass-2 note, item 5), not a lane defect.

## F4 (LOW) — fragmentation: 2 of 143 raw events are near-twin genesis

Genesis pairs ≤400 km apart and ≤24 h apart: `9eb02c51a49c`/`847bd34bca89`
(380 km, 24 h, 08-26…28) and `b086a65dc681`/`fad8c6876ef3` (398 km, same day,
10-07). `dedupe_tracks` conflicts tracks whose fixes come within 6° at the
*same or adjacent* 6-hour step; two fragments that start ~12 h apart and
diverge can slip through at 398 km. Rate LOW: one of each pair typically
loses the bbox-overlap round in `select`, and both were quota-dropped in this
build. Not fixed this pass (fix would touch shared stitching, worse
regression risk than the artefact); recorded for the record.

## F5 — scoring: honest "Not scored" is the correct state; document, don't implement

MLC shows "Not scored" because (a) upstream EWB has **no metric set for a
pressure-minimum track** — there is no forecast-vs-MLC-tracker pipeline in the
reference implementation the lane is ported from, unlike the TC lane's
wind/pressure metrics; (b) scoring a genesis-initialised forecast track from
the CIRA MLWP virtual store would need the MLWP-only subsets path the owner
marked **DEV-ONLY** (`e4fd539`, excluded from promotion), so nothing built on
it could ever ship; (c) even the *feasible* half (compare MSLP against the
deterministic analysis) adds no information — the event *is* the analysis
minimum. Smallest honest version therefore stays: the published note ("Not
scored") + `pair_status: unsupported` per pair, same shape as the TC lane.
Revisit only if the owner asks for a specific metric definition.

## Priority order for this branch

1. F1 screen (code + unit test + rebuild) — correctness of the published set.
2. F2 severity (code + unit test) — correctness of *which* 80.
3. F3/F4/F5: documentation as above; no code.
