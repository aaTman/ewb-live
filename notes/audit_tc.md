# Audit — tropical-cyclone lane (agent `ne-fix-tc`, 2026-11-19)

Owner request (2026-11-19): "tropical cyclones … need to be fixed" — no specifics,
so this is an independent audit of the whole lane: IBTrACS ingest → event
selection → publish → scoring → TrackViz/chips/detail text.

**Method.** Nothing below uses the lane's own tests as evidence. Everything is
re-derived from the upstream archive (`ibtracs.last3years.list.v04r01.csv`, read
independently into pandas, own implementation of the qualification rule) or
measured off the built dev tree (`/opt/ewb-live-dev`, :8051, window
2026-08-20 … 2026-11-18). Reproduce with:

```
# on the ewb LXC, in /opt/ewb-live-dev (read-only)
.venv/bin/python notes/tc_audit.py --cache data/cache/ibtracs/fixes_ibtracs.ALL.list.v04r01.parquet \
    --events data/results/events.json --start 2026-08-20 --end 2026-11-18 \
    --raw data/cache/ibtracs/last3years.csv
.venv/bin/python notes/tc_timeline_diff.py 2026-10-26
```
(`notes/tc_audit.py`, `notes/tc_timeline_diff.py`, `notes/tc_day_published.py`
are committed with this note.)

## Verdict

The lane's *event logic is right*: 44/49 storms qualify, all 44 are published,
none extra, and every published peak wind + Saffir-Simpson category matches the
raw archive's 1-minute wind. The defects are around the event: the ingest cost,
an unenforced wind-averaging basis, a live-but-invisible system, and copy that
conflates "storms tracked" with "events published".

| # | defect | evidence | severity |
| --- | --- | --- | --- |
| TC-1 | ingest pulls the 333 MB ALL file for a 90-day window | 333 282 096 B vs 11 860 074 B (`last3years`), same `Last-Modified`, identical 2 895 fixes / 49 storms in the window | P2 (ops cost, refresh latency) |
| TC-2 | wind basis is not enforced per storm; Saffir-Simpson is applied to whatever agency value was picked | `WIND_AGENCIES` mixes 1-min (USA) with 10-min (WMO/TOKYO/CMA/KMA/HKO/BOM/NADI/WELLINGTON/REUNION/DS824/MLC/NEUMANN) and IMD 3-min (NEWDELHI) **per fix**; `category()` and `validate.py` ("kt, 1-min sustained") assume 1-min | P1 latent (0 events affected in this window — measured) |
| TC-3 | a system in the archive that is not yet qualifying is invisible | AKVAN `2026321N11087` (North Indian, fixes 2026-11-17 00/03/06Z, 39 kt, 6 h span): dropped by the 12-h span rule *only because the archive stops at 11-17*; UI says "44 storms tracked", nothing says a live system is pending | P1 (honesty/UX) |
| TC-4 | lane copy conflates counts | `noScoresNote(n)` prints "Not scored — 44 storms tracked from IBTrACS best tracks" where `n` is the **published events in the current view**, not storms tracked (49 in the window); the archive reach is stated only in the empty-lane note | P2 (text) |
| TC-5 | detection copy never states the wind-averaging basis | `TYPE_CRITERIA.tropical_cyclone` says "≥34 kt sustained", not "1-minute sustained, USA/JTWC-informed", which is what the number is | P3 (text) |
| TC-6 | window-edge truncation is silent | `storm_to_event()` clips fixes to the scan window and computes peak/track from the clipped set; the first two events (`Saudel`, `Lala`, start 2026-08-20 = window start) are clipped and nothing says so | P3 (honesty) |
| TC-7 | busiest-day: the box-lane event that does not paint is a dateline AR, not a TC | see "Busiest-day map" below | P1 FE, **not this lane's file** |
| TC-8 | "Not scored" is honest but unquantified: no track/intensity verification against the MLWP store | scoring path in `pipeline.py` (`UNSCORED_TYPES`), see "Scoring" | P2 |

## 1. Source reach — the 2.0 d lag is real, not a bug (asked in the brief)

Measured 2026-11-19 02:38–02:41 UTC from the ewb LXC (`now` = 2026-11-19 02:40Z):

| NCEI v04r01 product | `Last-Modified` | latest `ISO_TIME` | size | rows / storms |
| --- | --- | --- | --- | --- |
| `ibtracs.ALL.list.v04r01.csv` | 2026-11-17 10:10:50 GMT | **2026-11-17T00Z** | 333 282 096 B | — |
| `ibtracs.last3years.list.v04r01.csv` | 2026-11-17 10:10:57 GMT | **2026-11-17T00Z** | 11 860 074 B | 25 035 / 413 (seasons → 2027) |
| `ibtracs.ACTIVE.list.v04r01.csv` | 2026-11-17 10:10:14 GMT | **2026-11-17T00Z** | 68 629 B | 142 / 4 |

All three NCEI CSV products carry the same publication stamp → the whole v04r01
CSV family is published on one cadence, ~1 day behind real time with the last
fix at the previous 00Z, i.e. **the observed 2.0–2.1 d lag is the archive's own
reach, not our refresh policy** (`meta.ibtracs.cache_age_h` was 1.4 h at the last
dev build, so our own refresh is timely). The near-real-time feed that would
beat it (`https://ftp.atm.npu.edu/pub/data/IBTrACS_NRT/`, the IBTrACS-NRT
mirror) does not answer from this network (curl exit, 20 s timeout; the
`…_last300days.csv` name 404s, and the directory index above lists every file
NCEI serves). Conclusion for the owner: 2 d is the floor on this network; the
lane's `lag_days` note is honest and should be *said* in the UI even when the
lane is non-empty (TC-3/TC-4).

## 2. Which 5 storms were dropped, and were they right? (asked in the brief)

Independent qualification of the 49 storms with a fix in the window (own code,
raw CSV, `USA_WIND`): **44 qualify → 44 published, 0 missing, 0 extra, 0
peak/category mismatch.**

| SID | name | basin | fixes | ≥34 kt fixes | max kt | why dropped | verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `2026235N12135` | ATSANI | WP | 27 | 0 | 29 | never reached TS force | correct |
| `2026248N22138` | (unnamed) | WP | 9 | 2 | 39 | only 2 fixes at ≥34 kt | correct |
| `2026270N11220` | (unnamed) | EP | 43 | 0 | 30 | never reached TS force (nature DS/TS) | correct |
| `2026285N16151` | (unnamed) | WP | 29 | 0 | 29 | never reached TS force | correct |
| `2026321N11087` | AKVAN | NI | 3 | 3 | 39 | 3 qualifying fixes span 6 h < 12 h | **correct rule, truncated source — see TC-3** |

Four are honest non-events (tropical depressions). The fifth is the interesting
one: Akvan's three fixes are 2026-11-17 00/03/06Z — the *end of the archive*. It
was a live system when the build ran and cannot satisfy the 12-h span rule until
NCEI publishes again. Dropping it is defensible; hiding it is not (TC-3).

## 3. Wind-averaging basis (asked in the brief)

`ewb_live/sources/ibtracs.py` chooses the wind per **fix** by agency priority
(USA → WMO → TOKYO → …). The averaging period differs by agency — USA/JTWC are
1-minute, NEWDELHI is IMD 3-minute, everything else is 10-minute — so a storm
can be spliced across bases, and `ewb_live/events/tc.py:category()` then applies
Saffir-Simpson bands (a 1-minute scale) to whichever value won. `validate.py`
declares the peak range as "kt, 1-min sustained", which nothing enforces.

Blast radius, measured: **in the dev window 5/2 895 fixes (0.2 %) carry a
non-USA wind and all five are null-fill cases; one storm (`ARNAB`, 22 USA + 5
null fixes) mixes sources; 0 published storms have a non-USA peak — 0 category
errors today.** Across the 3-year archive the fallback path is exercised
wherever `USA_WIND` is missing, so the fix is to enforce the basis (single basis
per storm, off-basis fixes excluded from qualification/peak) and to publish
`details.wind_basis` so any future storm states its own scale. The published
*value* is never silently unit-converted — an observation is what the agency
reported.

## 4. Busiest-day map — the missing box is a dateline AR (hand-off, TC-7)

Own read of the built tree for 2026-10-26 (`notes/tc_timeline_diff.py`): the
dashboard's day set comes from `global_timeline.json` (15 events), of which
**7 are box-lane** and one of those has a wrapped bbox:

* `atmospheric_river 4fabad88a37b`, bbox `[-49.5, -22.0, 144.75, -160.5]` —
  `lon_min 144.75 > lon_max -160.5`, i.e. the SPEC antimeridian convention.
  It is the box-lane event the integrator counted as "not drawn": the merged
  map's box path cannot draw a wrapped bbox (same root cause as the two
  `test_dateline_*` failures, which are TC/MLC track events).
* 18 published events carry wrapped bboxes (4 TC, 14 MLC, plus this AR), so the
  gap is systematic, not one event.
* Track-lane events draw on `#gm-track-marks`, not in `#gm-boxes`, so a "one box
  per published event" assertion cannot hold for the busiest day.

**This is not my file** (`dashboard/mergedmap.js` box drawing / dateline
unwrapping is `ne-fixes2`'s per the brief). Reported in the channel; I did not
touch it. Nothing here is a TC-lane defect: TC wrapped bboxes are correct per
SPEC and TrackViz unwraps them (its own antimeridian test passes).

## 5. Scoring — measured feasibility, not opinion (TC-8)

Today `pipeline.py` marks every TC pair `unsupported` ("no EWB benchmark for
best-track events") and the tab prints "Not scored — …". That is accurate as the
code stands, but "no benchmark" ≠ "no verifiable claim", so the question was
MEASURED, virtual store only (`notes/tc_score_probe.py`, run on the LXC in the
dev tree, read-only; report committed at
`notes/screenshots/tc_score_probe.json`, 2026-11-19):

* **What was probed:** the 3 strongest published storms (Champi, Polo, Lowell),
  4 archive models (graphcast, aurora, pangu_weather, fourcastnet_v2 — the
  Icechunk-virtualised CIRA MLWP models; ECMWF-direct models are NOT the
  virtual store and were excluded per the brief), leads 24/48/72 h, ±5° box on
  the best-track fix at the valid time: 36 probes, 36 served, no missing inits.
* **Cost:** median **0.81 s** per read → a full-lane pass is ~44 storms × 4
  models × 3 leads ≈ 530 reads ≈ **8 min wall** on the box. Cost is not the
  blocker.
* **Position is verifiable:** the forecast MSLP-minimum cell sits within 300 km
  of the best-track position in **32/36** probes (median 156 km; per-model
  24–72 h table in the JSON). The 10 m wind-maximum cell is NOT a reliable
  storm centre at 0.25° (median 314–686 km — it chases outer bands); MSLP is
  the honest position statistic.
* **Intensity is not verifiable against a 1-min best track at this
  resolution:** forecast-max wind minus best-track wind is ~−4 kt at 24 h but
  **−14 … −24 kt** at 48–72 h — the models' 0.25° floors dominate, so any
  wind-error number would measure the resolution, not the forecast.

**Verdict (documented, per the brief's "…or document why not"):** the smallest
honest version is a **track-presence check** — per published storm and model:
"carried this storm at lead L" iff the forecast MSLP-min sits within 300 km of
a best-track fix at the valid time, published per event as
`details["archive_check"]`. It is cheap (8 min/week) and honest, but it is a
**new metric surface**: it is not an EWB benchmark, does not fit the scorecard
rows schema (owned by the EWB lane), and printing it on the TC tab would imply
owner sign-off of the metric and its threshold. It was therefore NOT slipped
into this fix pass; the numbers above are the go/no-go evidence for an owner
decision. Until that decision, "Not scored — … No EWB benchmark for best-track
events yet" remains the true statement the UI prints.

## Fixed in this pass (see DONE entry in `notes/channel_new_events.md`)

TC-1 (window-aware source, 333 MB → 11.9 MB), TC-2 (one wind basis per storm +
`details.wind_basis`), TC-3 (`meta.ibtracs`: storms in window / published /
systems pending qualification, surfaced in the UI), TC-4 + TC-5 (lane and
criteria copy), TC-6 (`details.window_truncated` + detail-panel note).
TC-7: handed to the dateline/legend owner (it is not this lane's file).
TC-8: measured feasibility + documented verdict (§5), owner decision asked.
Branch `fix/tc` rebased 2026-11-19 onto `fix/flood` (⊇ `fix/mlc` ⊇
`fix/round2-legend-tc-wind`); TC-lane files are byte-identical across the
rebase. Verification ran on an ISOLATED dev tree (`/opt/ewb-live-tc`,
port 8054) because the shared :8051 tree was in a multi-agent clobber race
(see the INCIDENT channel entry); :8051 re-publication is left to the
integrator.
