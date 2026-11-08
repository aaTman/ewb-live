# Science review — EWB Live event detection & scoring
Reviewer: science-review (principal atmospheric scientist / forecast-verification).
Window under review: 2026-08-08 .. 2026-11-06 (truth: ERA5 via ARCO, mirror at `data/cache/truth/era5.zarr`).
All check scripts: `scripts/review/sr0*.py`, run on the EWB LXC via `ops/remote.sh`; results under `data/results/review/`.
Status: DRAFT v2 (scoring section pending `full_run_complete`; event-set external cross-ref pending).
Successor note (2026-11-07 ~21:00 UTC, science-review successor 2): session restarted after
Orca restart; state audited from the box. Pipeline stopped clean 17:47 UTC, run incomplete
(177 scored / 90 unattempted pairs); score rows staged in `data/cache/score_parts` (197 parts,
to 17:30) — reusable only while event fingerprints (start/end/bbox, `pipeline.fingerprint`)
hold. Owner clips are now heat **p85** / freeze **p15** (SPEC 2026-11-07); code default table
in owb mode is still p95/p05 with env-only override — must become the in-code default
(portability rule). New curation rules (ocean-start/land-end, same-event merge w/ p90-duration
cap, zero-overlap conflict resolution, publish validator) implemented in worktree
(`events/ar.py`, `events/select.py`, `events/validate.py`); data-side verification queued
against the p85/p15 repost. See channel post 21:0x UTC for the re-verification queue.

## Executive verdict (final, pending §4-5)
MINOR REVISIONS. Climatology product **validated** (A1, sr01/sr01b); event-set
**structural** checks **pass** (A2); freeze lane empty at owner-ratified p05 —
**not a bug** (B1 closed by owner, B1-corrected math in record); truth-mirror
coordinate defect (B2) root-caused, fixed in-place, 52 affected pairs purged for
rescore; truth-source confound (B5) mitigated — per-row `truth_source` live and
HRES fallback does not fire this window; heat-miss attribution (C9) shows every
miss fails the **joint AND-4 × 3-day × 200k km² gate**, none a code or curation
bug; a p85 clip would recover 2 of 4 in-window misses (China, Brazil), while
Europe-class fragmented heat needs a tiered area–persistence rule. AR lane:
externally reconciled 14/15; **TC-core contamination (B8) found, fixed, and
verified (13 AR + 11 heat, IVT ceiling 1500 + vortex rule)**. Pending:
rescored rows + sr03 skew + sr09 determinism rerun before §4-5 sign-off.

## A. Verified PASS

### A1. OWB pooled climatology reproduces from raw data (sr01)
- Recomputed pooled ±10-day quantiles independently from the 30 annual ERA5 zarrs
  (1990-2019, 4 anchors/day) on a 45-point stratified grid: mean |diff| vs shipped
  store ~1e-5 K at q05/q15/q85/q95/q99 (12,272 samples, `sr01_reproduction_rows.csv`).
  Leap-day mapping (doy 1..366 incl. Feb 29) and latitude orientation (90..-90,
  lon 0..359.75) consistent between product and consumers.
- Independent of this: detection-side consumers index the store by value
  (`sel(dayofyear=..., hour=...)`), so orientation is safe by construction (sr07).

### A2. Event-set structural integrity (sr04)
- Zero time+space overlaps among the 26 selected events (all pairs checked with
  spherical-geometry lat/lon intersection).
- Per-type caps respected (heat 11, AR 14, freeze 1-2 ≤ 15). Event ids deterministic
  on rerun. Curation floor (score/area/severity) respected. No window-edge-truncated
  events admitted without provenance.
- Truth policy verified against the ARCO cutoff (era5.zarr mirror through Oct 30,
  IFS-analysis fallback after; disclosed in meta.json).
- **Re-audit of the post-rebuild set** (26 events: 15 AR + 11 heat, 0 freeze),
  02:05 UTC: still zero time+space overlap pairs (two heat pairs share bbox but
  touch only at `end == next start` — exclusive-end semantics, not overlap); AR
  now sits **exactly at the 15 cap**; all ids `^[0-9a-f]{12}$` and unique; no
  event extends past the ARCO cutoff (max end 2026-10-28). One deviation:
  **two AR events start before the declared window start** — a3bb50ccde3d
  (2026-08-05) and fbe99f30e625 (2026-08-06) vs window 2026-08-08 (see B10).
- The 4-anchor all-anchor-AND → `resample('1D').min()` daily-mask semantics **are
  upstream EWB's own** (`third_party/ExtremeWeatherBench/data_prep/temperature_events.py:361-406`,
  `:735-739`) — no deviation here. Upstream default quantiles are p85/p15
  (`:713`); the live product's p95/p05 is the *owner-spec* deviation.

### A3. Reproducibility of production scans (sr08)
Rerunning `scan_temperature_events` in-place reproduces the logged funnel exactly:
p15 freeze 278,566→91,925 cells→2 events; p05 38,529→8,033→0; p05 at lat_min=−90
114,917→22,381→0. Heat: p95 funnel 667,447→358,168 cells→52 raw→15 candidates
(month-chunked; logs `run-20261107T001723Z.log`).

## B. Findings

### B1. MAJOR→CLOSED (owner) — freeze lane at owner-spec p05 is structurally empty
Evidence: sr02b/sr07/sr08 (above); independent mask rebuild matches production
cell-for-cell on a sample day (5,979 = 5,979, 2026-10-20) and within +4.6%/−0.3%
over 98 days. Mechanism: 4-anchor AND gives independent-survival 0.05⁴ ≈ 6.3e−6
per cell-day (~180 expected cell-days over the window; observed 38,529 ≈ 200× —
cold-air persistence, then the ≥3-day filter and the 200,000 km² seed floor kill
the remainder). p05-empty is **not** an implementation bug.
Required fix (owner decision, ranked): (i) ratify freeze p15 = EWB-native
semantics; (ii) keep p05 but score it on daily-mean t2m (preserves the 5th-pct
intent, drops the unreachable AND-chain), candidate counts quantifiable; (iii) EWB
shipped per-grid p15 store; (iv) shipping freeze=0 is not acceptable.
Status: CLOSED by owner 2026-11-07 — keep p05, empty lane acceptable, meta records
threshold + n_events 0; `EWB_LIVE_FREEZE_CLIP=0.15` removed; live selected set now
26 events, 0 freeze (verified on box).

### B2. MAJOR→RESOLVED — truth mirror coordinate arrays corrupted (sr06)
`data/cache/truth/era5.zarr` has `longitude[0] = NaN` and one NaN in `latitude`
(the data field itself is bit-identical to live ARCO on a global test step, maxdiff
0.0). Any value-based selection/regridding on those coords silently drops or
misplaces the affected meridian/row. Required fix: rebuild mirror coords (or
reground-truth the mirror from ARCO) and re-verify affected score rows; disclose.
RESOLVED 01:40 UTC (ewb): root cause was zarr `create_array` default
`fill_value=0.0` masking the real 0.0 coords (lon idx 0 = 0E, lat idx 360 = equator)
at store creation; raw-zarr reads were clean (explains both probes). Fix: creation
`fill_value=None` + `ensure_coord_integrity()` guard on every mirror read/truth
switch + 4 offline tests. My independent verification 02:05 UTC: xr-decoded NaN
count 0/0, lon[0]=0.0. Scope audit: 17/26 events' ±10° windows touch affected
column/row → 52 pairs purged, rescoring in the current run (CLOSE on verified rows).

### B3-Bn. PENDING
- B3: heat geographic-skew quantification (sr03 rerun after serialization window).
- B4: missed-extreme blob audit (sr05, depends on sr03).
- B5: MAJOR→MITIGATED — truth-source confound. Static audit of
  `ewb_live/scoring/evaluate.py` + `truth.py`: metrics match EWB upstream
  default lists exactly for all three types (verified against
  `src/extremeweatherbench/defaults.py:431-456,524-526`); init/lead logic
  leak-free (inits daily 00Z in [start−240h, end), leads only where valid
  time overlaps the event); coverage documented via per-model status
  (scored/unsupported/no_data/bench_failed), silent-zero skips eliminated.
  Risk: events ending after the ARCO cutoff (2026-10-30) score against
  **IFS HRES 0-h analysis** truth, which systematically favors the IFS HRES
  forecast member. MITIGATED as of 01:47 UTC (ewb + my box verification):
  `scores.parquet` carries per-row `truth_source` (6,437 rows, all
  `era5_arco`), truth-policy fingerprint merge invalidates cached rows on
  policy flip, and **no selected event ends after the cutoff** — verified
  live maxima: heat 2026-10-24, AR 2026-10-28, freeze 0 (ewb's "latest
  09-30" was imprecise; conclusion stands) → HRES fallback does not fire
  this window; no realized cross-truth bias in the current scorecard.
  Remains a documentation requirement for future windows.
- B6: PASS — EWB shipped-climatology spot check (sr01b, complete): shipped
  store structurally sound (6 quantiles 0.01-0.99, 366×4×721×1440, zero
  NaN); 15-point stratified spot check vs OWB: EWB median lies inside the
  OWB p15-p85 band at 15/15 points; OWB−EWB p15 mean −1.03 K, p85 +1.51 K
  (p95|abs| 2.7-3.8 K) — consistent between independent products.
- B7: end-to-end single-event scoring determinism rerun (sr09 ready; run
  when the scoring lane idles — competes for bench memory).

## C. External cross-reference (event realism)

### C10. Re-verification of the p85/p15 repost (successor 2, 23:2x UTC, bound to run_id 20261107T231622Z)
Structural PASS on the new set (30 events = 15 heat + 14 AR + 1 freeze):
- Zero time+space overlap pairs; ids canonical + unique; per-type caps respected (heat
  exactly at 15); durations within events.yaml p90 caps (AR ≤ 9 d, heat/freeze ≤ 16 d).
- **China east Sep-Oct heat NOW DETECTED** (32d04a30d801, 09-27..10-01, 14-32.25N/100-122E,
  pk 32.56 C daily mean) — exactly the C9/sr10 p85 prediction; curation did not drop it.
- Brazil late-Aug/Amazon cluster expanded (d417662c305f, 9210c96bc91d, 7e2b955f8f88,
  511f7c22604d); W Europe remains absent at p85 — **consistent with sr10** (persist-3 blob
  77,090 km² < 200k floor even at p85; needs the R2 tiered rule, not a clip change).
- East Africa contiguous trio (eecddeca/47275382/8a462d8d, the B9 fragmentation instance)
  **merged into one event** 31a871378c3e (10-11..10-22, m=3) — same-event merge rule works
  on real data; the earlier 13-d fragmentation is resolved.
- Freeze lane: raw scan 2 candidates (b04d97d1a33a + fe0db893efb8, curation merge log) =
  exactly the sr08 p15 expectation → merged to 1 published event **88bb73d7ae25**
  (Greenland ice sheet 71.5-82N/-68..-23E, 10-16..22, pk -43.69 C daily mean — physically
  ordinary deep-autumn cold over the ice sheet; percentile-relative definition).
- All 14 ARs byte-identical (ids+geometry) to the 040432Z snapshot → staged score parts
  reuse as predicted; 135/330 pairs already scored at 23:18 with the run in flight.
- meta.json now records the ACTIVE clips (heat q0.85 / freeze q0.15) and the code default
  is flipped (temperature.py:55-59, owner-decision comment) — my portability request
  landed; B12 status vocabulary unresolved (monitor).
- p85 heat-lane regional composition (15): N Africa/Middle East 6, Amazon 4, Central Asia 2,
  US SW 1, E Africa 1, China 1 — the dry-continental-core skew (B4) persists by design;
  sr03 rerun deferred to the idle box.
### C11. External cross-reference of the p85/p15 repost (successor 2; evidence: `notes/review_external_p85.json`)
Delegated agency-source sweep of the 8 newly-added/re-scoped events, then **reviewer
independent spot-verification of every load-bearing in-window source** (verbatim quote
comparison against freshly fetched pages). Result: **6/8 CONFIRMED** with in-window agency
or observation-grade sources, **2/8 CONSISTENT-CONTEXT** at month scale (Kazakh/Volga pair
0a65373050e2 + e4cc82710996 — Kazhydromet record-warm Sept 2026, +2.38 C, covers the
region). **No event contradicted.** Verified highlights:
- **95aeafb7fd7f** (US Sep 2–5): NWS Paducah monthly summary names the "September 1-5 heat
  wave" with the latest-on-record Extreme Heat Warning — verified verbatim in-window.
- **32d04a30d801** (China Sep 27–Oct 1): The Watchers 10-03 (CMA/China Weather-sourced):
  Qiaojia Yunnan 39.5 C, 62 October station records on Oct 1 — verified verbatim; window
  matches exactly. The C9 miss is now detected AND externally corroborated.
- **88bb73d7ae25** (Greenland freeze Oct 16–22): ACAP October-2026 ERA5 summary "the only
  large areas colder than normal were portions of Greenland" — verified verbatim. NOTE: the
  sweep's original C3S-bulletin attribution for this quote was **misattributed** (sentence
  absent from the bulletin text; grepped full page) — substance stands on the corrected
  source; evidence-quality finding recorded, verdict retained.
- **c736a266a826** (Egypt/Sudan Sep 10–16): Aswan 48.0 C = hottest September day ever in
  Egypt; Sudan warmest September on record.
- **4b6147e617a0** (Iran/Central Asia Aug 16–22): extremetemps-documented all-time records
  (Kuwait City 52.0 C max / 38.0 C min, Omidieh 51.2 C) corroborate the most extreme peak
  in the set (41.6 C bbox daily mean).
Missed-extreme additions beyond C9: named France Sept-8 daily records (Saint-Etienne
36.4 C) and Italy's hottest September on record — strengthens the R2 tiered-rule case
(still unrecoverable at p85 per sr10); Japan/Korea late-Aug marginal (records mostly
pre-window); India Sep–Oct NOT-FOUNDED (post-monsoon wet pattern; IMD ERF).


Conventions: `peak_value` is ERA5 daily-MEAN 2 m T at the peak day (not station
daily-max); comparison anchors are regional/seasonal reports, not station lists.

| # | Event (id) | Window | Peak daily-mean T | bbox | External anchor | Verdict |
|---|---|---|---|---|---|---|
| C1 | E Africa bdb9bb0836dc | 08-11..08-20 | 38.8 C | 6.2-18N, 25.8-38.8E | GHACOF74 outlook (17-18 Aug, warm signal over GHA; icpac.net GHACOF74 statement PDF) | consistent |
| C2 | E Africa 64a046c111ce | 08-22..08-25 | 37.5 C | -5.8..17.8N | same GHACOF74 + super-El-Nino heat initiative (icpac.net/news) | consistent |
| C3 | Amazon 971b752852cf | 08-29..09-01 | 33.0 C | -5.8..5N, -63.5..-54.2E | ARO/OTCA Aug bulletin: basin mean 25.85 C, +1.36 C anomaly (otca.org bulletin PDF); G1/Inmet Manaus 36.3 C daily max mid-Aug (g1.globo.com 2026-08-15) | confirmed |
| C4 | E Africa eecddeca3560 / 4727538248b7 / 8a462d8d4796 / e7fa2290cc77 | 09-12..09-30 | 37.5 C max | 4.2-16.5N, 24-38.5E | C3S Sep 2026 bulletin: above-normal temps widespread (climate.copernicus.eu/surface-air-temperature-september-2026) | consistent |
| C5 | S.Plains 457f3f87e24a | 09-16..09-19 | 32.6 C | 31-39.2N, -101..-83.5E | NASA Earth Obs 09-17: records 09-15 TX-AR-MO-TN, Dallas 38 C; DFW 12th triple-digit Sep day (science.nasa.gov, kcbd.com 09-08, foxweather 09-13) | confirmed |
| C6 | Amazon bace10fee425 | 09-12..09-19 | 33.2 C | -4.2..5.8N | ARO/OTCA Sep bulletin: 69% of basin below-normal rain, +1.29 C anomaly (otca.org/en) | confirmed |
| C7 | Amazon 08cf5a90f8fe | 10-03..10-12 | 33.8 C | -6.2..5.5N | Guardian 08-14 + New Scientist: El Nino peak timed to Amazon dry season, dry-season heat outlook Oct-Dec | consistent |
| C8 | E Africa 559d8626b887 | 10-18..10-24 | 37.0 C | 3.2-15N, 28.2-38.5E | NOAA CPC Africa hazard outlook 10-15..10-21: "abnormal heat... eastern Sahel and parts of Eastern Africa", >=3 consecutive days (cpc.ncep.noaa.gov/products/international/africa/africa_hazard.pdf) | confirmed |

All 11 selected heat events map to externally reported warm anomalies; no
fabricated-looking events. Physical bounds sane: peaks 32.6-38.8 C daily mean,
3-7 d persistence, 0.3-3.0 M km2 footprints; East Africa recurs as 6 disjoint
events because the anomaly was quasi-persistent in a chronic-heat core (sr04
confirms zero time+space overlap).

### C9. MISSES (external extremes with no detection, pre- or post-curation)
Full-catalogue scan (all detected candidates incl. dropped): **zero** heat
candidates with centers in Western Europe, E Asia (20-42N/100-125E),
Australia (-40..-10/110-155E), or S Asia. Against:
- Western Europe joint-warmest Sep 2026 on record (+2.51 C regional anomaly;
  4-6 C over S France/Portugal/Spain; climate.copernicus.eu/surface-air-temperature-september-2026);
  record 2026 summer with "record-breaking daily temperatures also occurring
  in May and September" (climate.copernicus.eu/copernicus-record-heat-stress...).
- NW Australia extreme heatwave 11-02..06: Fitzroy Crossing 45.1/47.1 C daily
  max, BOM-classified extreme heatwave (nine.com.au 20261104, weatherzone.com.au).
  NOTE: daily-MEAN p95 AND-chain criterion legitimately tolerates missing
  tmax-only events — but BOM *classified heatwave* with no 100k-km2 daily-mean
  p95 blob is a quantitative stress-test of the criteria (sr03/sr05, pending).
- China autumn heat (SCMP HK Oct 2026; CMA national Jun-Oct records).
**Final attribution (sr10, criterion-by-criterion; maps `notes/screenshots/sr-miss-*.png`,
numbers `data/results/review/sr10_miss_attribution.json`):**
- **W Europe / S France–Iberia Sep:** anchors 11.9–13.4%, AND-4 only 3.4–4.0%; largest
  single-day AND-4 blob 139,092 km² (< floor), persist-3 blob 15,579 km²; at p85
  persist-3 still 85,933 km² (< floor). Verdict (b): blocked by the joint
  AND-4 × 3-day × 200k km² gate; C3S signal is a basin-mean anomaly, not a coherent
  daily-mean p95 field at scale. No candidate ever entered events.json.
- **China east Sep 1–Oct 15:** single-day AND-4 blob 640,898 km² (passes floor),
  persist-3 100,684 km² (fails floor by 2×). At **p85 the persist-3 blob is
  902,410 km² — passes every criterion.** Verdict (b): the only miss a clip change
  alone fixes; persistent sub-p95 daily-mean heat (what upstream's p85 is for).
- **NW Australia:** cited event (Fitzroy Crossing 45–47 °C, Nov 2–6) is beyond both
  truth sources: mirror ends Oct 30; live ARCO Nov 1–6 steps exist but are **100 %
  NaN (ARCO delayed-fill lag ≈1 week, verified on box Nov 7, sr10b)**. Verdict (b'):
  data-availability blocker, not detector failure. Mirror-visible window Oct 20–30:
  anchors 1.6%, max blob 5,962 km², peak 29 °C → (a) legitimately below p95 in
  grid daily-mean terms (station daily-max ≠ 0.25° 6-hourly mean).
- **Controls:** SE Africa Oct: persist-3 blob 7k km² → rejected by criteria
  (moderate spell, consistent). S Plains/Amazon were detected (C3/C5).
- **Europe August (added from delegated sweep; C3S warmest European summer on
  record, FR/IT/Balkans +3–5 C):** p95 anchors 10.6%, AND-4 5.8%, largest blob
  111,168 km², persist-3 42,815 km²; at p85 blob 249,680 km² (passes floor) but
  persist-3 only 77,090 km². Verdict (b), **not fixable by clip change** — needs
  the tiered area–persistence rule (R2); the event is a long (9–12 d any-cell
  AND-4 run), large-area but spatially fragmented daily-mean anomaly.
- **Brazil Cerrado/Pantanal late Sep (43.3 C national record Nova Xavantina
  28-Sep; INMET red onda-de-calor alert, own 5d/+5C criterion met):** p95 anchors
  11.2%, AND-4 7.9% — single-day blob **1,092,866 km² (5.5× the floor)** — but
  persist-3 blob 91,982 km² (0.46× floor). At **p85 the persist-3 blob is
  335,974 km² — passes every criterion.** The closest near-miss in the set: a
  100k floor at p95 would also have admitted it. Verdict (b).
Common pattern: every miss fails the *joint* gate — intermittent/local p95
exceedance never sustains 4-anchor daily-mean passes over ≥200k km² for ≥3 days.
Zero curation or code bugs (detector emitted zero candidates; pipeline validated
by sr01/sr07/sr08). Interim finding B4 (regional skew toward dry continental heat
cores) is thus a **criteria-design property**, not a bug; recommendations R1–R3
posted to channel (p85 clip option, tiered area–persistence gate, documented
~7-day unscorable horizon).

## D. AR-lane external cross-reference & physical plausibility (delegated sweep, 2026-11-07)

**Bound to snapshot run_id 20261107T040432Z per ewb request (04:22 UTC).** Snapshot
structural re-audit (local, pulled copy): 14 AR + 13 heat, ids unique, all ends ≤
2026-10-31 (cutoff honored), a3bb50cc 08-05 pre-window start persists (B10, n/a
event), max AR peak 1112 — no TC-class IVT. Heat-lane observation: three
contiguous same-bbox events (eecddeca 09-12→18, 47275382 18→22, 8a462d8d 22→25,
lat 3-16.5 lon 24-38.5) — half-open [start,end) semantics give zero double-counted
days (sr04-style PASS) but one ~13-day contiguous heat episode ships as three
events: the B9 no-merge-across-boundaries mode, now in the heat lane; fold into
the B9 regression test. Four re-added ARs (0e530ab9, 255f6810, 7188c11f, 92c9cb48)
carry ids already reconciled below; three genuinely new ids (e86c13f47bc0 S Chile
AR; a9f9ea7bca1c Amazon heat; b24f79e63be0 Nenets heat) are under external
cross-ref (deleg_a09fa915). Notably b24f79e63be0 sits in the SAME Nenets/
Arkhangelsk region as the suspect AR 06ceb4d8 — its temperature/precipitation
anomaly evidence double-serves as the 06ceb4d8 counter-evidence test. Prior
tally stands as context for surviving ids:

**New-id reconciliation COMPLETE (deleg_a09fa915, 04:1x UTC) — snapshot set now
14/14 ARs reconciled:**
- **e86c13f47bc0 (S Chile AR, 10-28..31): CONFIRMED (event-level)** — Meteored
  Chile independently called "un río atmosférico de categoría 1 a 2" for Patagonia
  from 29 Oct (30-40 mm, 90 km/h gusts Última Esperanza); Senapred Magallanes
  Alerta Temprana Preventiva 30 Oct grounded in DMC Aviso A594/2026; WMO METAREA-6
  NAVTEX 10-30 00Z shows 996 hPa low at 50S/68W **inside the bbox**; SMN Argentina
  yellow alert Tierra del Fuego 29 Oct. Impact-level none-found (bbox N half saw
  only light frontal rain — plume core tracked southern/oceanic box). IVT 843 is
  Payne–Magginis cat 1-2, matching the independent characterization; far below TC
  threshold. Evidence JSON: crossref_e86c13f47bc0.json.
- **a9f9ea7bca1c (Amazon/Guayana heat, 10-27..31): CONTEXT** — Inameh forecast
  38-39 °C maxima **inside bbox** (Puerto Ayacucho/Ciudad Bolívar/Guayana Esequiba)
  on 10-30; INMET OND-2026 agroclimatic bulletin +2 °C anomaly for região Norte;
  Rio Negro −2.74 m y/y, 46 Amazonas municipalities in emergency (G1 10-27);
  driver: strengthening El Niño (C3S). No named discrete heat-wave impact. ERA5
  bbox peak 33.8 < station 38-39 = gridded-area max, consistent.
- **b24f79e63be0 (Nenets heat, 10-28..31): CONFIRMED** — Amderma (69.35N 61.6E,
  **inside bbox**) set its all-time October monthly record (+3.3 °C, beating 2007;
  Gidrometcentr via hmn.ru); 30 stations warmest-Oct-on-record, Arctic anomalies
  +6-7 °C; 99 daily records 10-25..30 incl. 36 on 10-28 alone (RIA), Arkhangelsk
  +9.7 °C breaking a 1937 record (pogoda.mail.ru); C3S sea-ice bulletin: warmth
  most pronounced over Arctic seas north of Eurasia.
- **06ceb4d8 counter-evidence NEUTRALIZED → KEEP robust.** C3S's own Oct-2026
  hydrology bulletin pairs the SW-Russia dry anomaly with "pronounced
  warmer-than-average conditions" — the dryness cited against 06ceb4d8 does not
  argue against an IVT landfall (ARs need vapor transport, not wet-month means),
  and the regime is corroborated by 99 Russian records in the same month. My
  KEEP-with-disclosure adjudication now stands on physical audit (710.6 kg/m/s,
  ocean origin) with the sole prior counter-evidence withdrawn.

**Final tally vs the CURRENT selected set: 14/15 confirmed, 1 suspect (06ceb4d8).**
(Follow-up sweep covered the 4 post-rebuild ids: 395349fe — Myanmar Ayeyarwady/Kayin
monsoon-trough flooding, AHA Centre flash update 19-Aug + ReliefWeb, IMD monsoon-low
releases 18-21 Aug; 9700e4af — NOAA WPC storm summaries 'Sep 29-Oct 02 2026 SW-Central
US Heavy Rainfall' + NWS DVN summary — **but the plume was ex-Hurricane Polo remnants:
near-threshold IVT 1,785 with documented tropical origin, consistent with B8**;
029719d1 — VIC flood watch Oct 1-3, BOM heavy-rain warnings, 48-90 mm daily, ABC/
Guardian; 66b5b9f6 — Baltic autumn storm-surge sequence, BSH 'mittlere Sturmflut'
17-18 Oct (125 cm at Lübeck — hydro peak lands at the window's last day), SMHI
yellow warnings, C3S Oct bulletin wet-anomaly corroboration. Evidence:
notes/review_external_ar2.json.)

15 AR tracks from the pre-rebuild selected set were checked against agency/news
sources (mapping to the current set by window/bbox since ids rotated in the
01:45+ rebuild): **10 confirmed** by specific in-window rainfall/flood events
(Patagonia Aug 5-6 + SMN records; Typhoon Dolphin / E China Aug 6-10; Patagonia
'gran diluvio' Aug 13-17; Korea Yeonggwang 579 mm Aug 28-Sep 1; SCS Raphaela
Aug 26-28 EUR 3.3 bn (PERILS) + Ireland flood; KZN/Durban Sep 14-15 SAWS;
Hurricane Isaias Gulf Oct 9 + FEMA declarations; Iberia-NWEurope storms Oct 18-27;
SE Qld 200-400 mm Oct 22-26; W Texas 4.58 in Oct 29-30), **4 context-only**
(WA cold front Aug 7-8; Bering/w-Alaska record-wet Aug, Galena 7.85 in record;
SA rain Oct 8; partial NE-US end of the Texas track), **1 none found**
(06ceb4d8, E Europe/Russia Oct 5-7 — counter-evidence: C3S October 2026 bulletin
reports that region **drier than average**; peak IVT a modest 711 → either an
unreported maritime/remote track or a detection error. **AUDITED (ewb 02:25):
genuine but modest IVT landfall (710.6 vs 600 floor, 3,559 landfall px, ocean
origin); no impact reports. My adjudication: KEEP** — the product's selection
criterion is physical, not impact-based, the region is sparsely reported, and a
3-day modest landfall does not contradict a monthly precipitation anomaly;
require a documented 'no external corroboration' note instead of exclusion.)

### B8. MAJOR→FIXED (verified) — tropical-cyclone cores contaminate the AR lane
fbe99f30 (E Asia Aug 6-11) reported **peak IVT 2,972 kg/m/s over land** —
world-record class, unattainable for extratropical ARs in 6-hourly ERA5; the
corroborated rain was Typhoon Dolphin. 6ad93bd2 (Gulf Oct 8-13, peak 2,018) was
Hurricane Isaias; 9700e4af (1,785) confirmed ex-Polo remnants. **FIXED + deployed
by ewb 02:25 UTC; independently verified by me on the live set:** selection ceiling
`peak_value <= 1500` at curation + scan-level `tc_plausibility_rule` in ar.py
(bbox-fill ≥ 0.55 ∧ IVT ≥ 1000 → vortex rejection; 6/6 unit tests). Live
events_selected.json: **13 AR + 11 heat**, all three TC-sourced events dropped,
max AR peak IVT 1434.9 (legit Korea event kept). Related bonus root-cause by ewb:
AR-lane bench_failed was an upstream EWB 1.1.1 dask apply_ufunc bug
(SpatialDisplacement), shimmed + tested 3/3.

### B9. MINOR (resolved by rebuild) — duplicate tracking
The previous set carried 85df401a and 695a34bd with *identical* peak IVT (1,126.4)
and near-identical boxes days apart — the same Bering system double-tracked. The
current set merged them into one event (85df401a, 08-27..09-04); the failure mode
(no dedup across adjacent tracks of one system) still warrants a regression test.

### B10. MINOR (moot-ish) — AR events starting before the declared window start
Window is 2026-08-08..2026-11-06 (meta.json); fbe99f30e625 (start 08-06) left the set
with the B8 drop; a3bb50ccde3d (start 2026-08-05) remains published but is now a
zero-row memory-exclusion (B11), so it contributes no score rows — clip starts or
document the 3-day detection lead-in before any future scored run re-includes it.

### B11. MAJOR (coverage, open) — AR absence is size-correlated; two big ARs now dropped from the PUBLISHED set
Measured RSS law (ewb): ~0.075 GB/deg² + 15 GB base. With snapshot
20261107T040432Z (Oct-31 ARCO-cutoff re-scan; 14 AR + 13 heat = 27), the two
largest memory-excluded ARs — 395349feddab (Myanmar Ayeyarwady, 63k affected per
AHA Centre) and 806867ad1ba1 (Korea Yeonggwang 579 mm) — were **removed from the
published set entirely** ("now moot — out of set" per notes/ewb.md), leaving
a3bb50ccde3d + 4a9c67b01f25 as documented n/a and **12/14 ARs scorable**. Scoring
bookkeeping is cleaner (published ≈ scorable), but the product consequence is
worse: the two strongest impact-corroborated ARs of the window are now invisible
to the scorecard, not merely unscored. Absence remains size-correlated, i.e. not
missing-at-random. Recommendation unchanged: lossless chunked/dask scoring on the
same grid is the cure (NOT looser grid — lossy values must never share an
aggregate; would need per-row `scoring_grid` + per-grid aggregates); real fix =
upstream dense-reindex cure; scorecard must disclose n/a events with reasons
(pair_status reasons verified present: 'excluded via EWB_LIVE_SKIP_EVENTS
(oversized AR case...)').

**B8 production-proof caveat (correction 02:30 UTC):** the fbe99f30 'completion'
was the PRE-shim 01:58 run (no-rows, 75 GB) — demonstrates only the pre-fix
failure mode. **Production compute proof VERIFIED by reviewer from
/opt/ewb-live/logs/pipeline.log @ 03:5x UTC: 10 'scored (3d32db128915, ...)' lines
(113-123 rows each, incl. graphcast/pangu/ifs_hres — the previously all-crashing
models).** Persistence NOT yet on disk (scores.parquet still 6,437 rows / 0 AR
rows; no newer .parquet on the box) — reviewer will re-verify row counts land at
RUN_DONE.

### B12. MINOR (open) — 5 pairs mislabeled terminal 'unsupported' from load failures (B12 raised by successor 2, 21:0x UTC)
`pair_status.json` (audit 21:00 UTC): unsupported=20 — 15 are `aigfs` 'n/a (no 2 m
temperature)' (genuine model gap, correctly documented) but **5 carry reason
"forecast unavailable: 'valid_time' not present in all datasets and
coords='different'"**: AR pairs fourcastnet_v2×{3d32db128915, 0e530ab9eebf},
pangu_weather×{92c9cb48559b, 66b5b9f6f9db}, and heat pangu_weather_gfs×{eecddeca3560}.
All three models score **fine on other events of the same type**, so this is a
transient sources/store-cadence concat failure, not a capability gap — yet the
pipeline treats terminal statuses as never-reschedule, so these pairs would ship as
permanently "unsupported" coverage holes. Required fix: split `load_failed`
(retriable, excluded from "model unsupported" bookkeeping) from `unsupported`
(documented model gap), or rerun the five pairs after the nearest-init store fix.
Until then the Phase-B coverage matrix must annotate these 5 cells as infra-gap,
not model-gap.

### C-AR external anchors (confirmed subset, URLs in delegation evidence)
Patagonia: reliefweb/SMN repositorio.smn.gob.ar Breves del Clima Aug-2026 PDF;
Dolphin: english.www.gov.cn 2026-08-08, Reuters Shaanxi evac 2026-08-06;
Korea: chosun.com EN 2026-08-31, en.sedaily.com 2026-09-01; SCS Raphaela:
perils.org 2026-10-19 (EUR 3,296 M), gdacs.org 1104127/1104084; KZN: witness.co.za
2026-09-14/15; Isaias: science.nasa.gov Earth Obs 'isaias-dims-the-gulf-coast',
FEMA daily ops briefing 2026-10-10 PDF; Qld: theguardian.com 2026-10-25;
W Texas: weatheronthisday.com (NCEI GHCN-Daily based), texasstormchasers.com.

## D. Phase B — scoring review, first pass (successor 2, 2026-11-08 01:4x UTC, run `full_run_complete=true`, scores 22,763 rows)

**Verified PASS:**
- **Pair arithmetic**: 30 events × 11 models = 330 = 257 scored + 20 unsupported + 22 bench_failed + 31 no_data. No unaccounted pairs. 28/30 events have rows; the 2 without (a3bb50ccde3d, 4a9c67b01f25) are the *documented* oversized-AR exclusions (EWB_LIVE_SKIP_EVENTS, 3D-mask crash + >128 GB) — owner-ratified, reason recorded in pair_status.
- **Merge integrity**: staged score_parts rows restricted to current events == merged parquet (22,763 == 22,763). The 5,066-row parts/merged gap is entirely stale parts from retired p95-era events — not lost data. (Stale *pair_status* entries from retired events also persist — 87 extra keys; cosmetic, but don't read pair_status coverage stats without filtering to current ids.)
- **Metrics = EWB defaults per event type** (heat: MaxMAE, RMSE, DurationMeanError@p85, MaxLowestMAE; freeze: MinMAE, RMSE, DurationMeanError@p15; AR: CSI, SpatialDisplacement, EarlySignal) — no bespoke metric deviations; DurationMeanError thresholds follow the p85/p15 product climatology.
- **Truth (B2)**: all 22,763 rows `truth_source=era5_arco` off the local mirror; live `_coord_integrity_status` = OK (fill_value=None fix + repair guard in place, cites this review's B2); manifest 400 six-hourly points; no IFS-analysis-fallback rows (all valids ≤ ARCO cutoff 2026-11-01T23Z). B2 closure verified at store level; value-level equality vs ARCO remains `verify_against_arco`'s job (cheap, 6-point exact check exists).
- **Climatology provenance** (portability item, now landed): meta reports `owb_pooled_era5`, file t2m_owb_1990_2019.zarr, percentiles {heat 0.85, freeze 0.15} — in-code default flipped, matches SPEC.
- **Lead/alignment**: common 0–240 h grid at 6 h; heat RMSE@48h 1.99–2.30 K (pangu best; gfs-initialised variants uniformly worse — physically expected); FCN AR CSI comparable to peers ⇒ RH→q path credible; aigfs correctly AR-only (no t2m).
- **No look-ahead**: deterministic verification of archived forecasts vs reanalysis truth; detection thresholds from the fixed 1990–2019 climatology; init window [start−240 h, end) is the forecast read range, not a selection input.

**Findings (Phase B):**
- **D1 (blocker, heat lane) — ROOT CAUSE FOUND 05:1x UTC**: all 15 heat × {ifs_hres, aifs_single} scored zero rows in every run since the 20:48 flaky run. Original theory (terminal-status skip, pipeline.py:660-665) is only half the story: the 04:33 weekly re-run re-marked **new** heat events no_data on first attempt. Live probe on idle box: inits exist; `fc_cache.open_forecast_cached('ifs_hres', ...)` throws `TypeError: AioSession.create_client() got multiple values for keyword argument 'config'` — s3fs 2026.9.0 paired with **aiobotocore 3.10.0** (supported range <3.7; regression from the evening's stall-fix dependency install). Every IFS-s3 read fails; ifs AR/freeze parts survive only because they were staged before the regression. Requested fix: pin the s3fs/aiobotocore pair, force-reschedule the 35 terminal pairs (incl. 4 valid_time unsupported, B12), and make completeness treat read-error no_data as blockers, not "documented".
- **D2 (major)** — *skill_vs_ifs_pct not like-for-like in the AR lane*: `build_scorecard` divides each model's mean by the ifs_hres mean computed over **ifs's event set**, unintersectioned — 837/1859 skill rows (all AR) have model-n ≠ baseline-n (e.g. aurora n=8 vs ifs n=6 at CSI lead 0). Skill must be computed on the per-(metric, lead) event intersection (or flagged).
- **D3 (major)** — *no bootstrap CIs anywhere*: scorecard = plain mean + counts per (type, metric, lead, model). Brief's "aggregation with bootstrap CIs" is unimplemented; with 6–15 events per cell this is the difference between a number and a claim.
- **D4 (minor)** — 79 rows at lead 246/252 h > MAX_LEAD_HOURS=240 (0.35%, all models); mechanism TBD via sr09 repro.
- **D5 (minor)** — freeze lane is n=1 event: every freeze "league" number is a single-event comparison; disclose on the dashboard rather than ranking.

**Pending before verdict:** rescore of D1 pairs + sr09 value-level repro (deferred while the box is busy with the weekly run; contract = no box compute during pipeline runs), then verdict + About-page limitations.

## E. FINAL VERDICT (successor 2, 2026-11-08 06:0x UTC) — CONDITIONAL PASS

**The scoring pipeline itself PASSES:** value-level reproducibility 136/136 rows on 32d04a30d801 × {graphcast_gfs, pangu_weather} (max abs diff 2.4e-7 = float32 round-trip, 92.6% bit-exact); metrics are EWB defaults per event type with DurationMeanError thresholds bound to the p85/p15 product climatology; no look-ahead; truth = era5_arco mirror with B2 coord fix verified live (integrity OK); merge integrity parts==merged; pair arithmetic closes (330-grid fully accounted, exclusions documented); selection layer PASS (C10 structure + C11 external cross-ref: 6/8 newly-added events CONFIRMED in-window by agency sources, 0 contradicted).

**Conditions before unqualified sign-off (owner decisions pending):**
- **D1 OPEN (blocker, heat lane)** — shipped data has **zero IFS rows for heat** (15 heat × {ifs_hres, aifs_single} no_data). Root cause: s3fs 2026.9.0 + aiobotocore 3.10.0 incompatibility breaks every IFS-s3 read (`create_client() got multiple values for keyword argument 'config'`); surviving IFS parts predate the regression. Terminal-status skip (pipeline.py:660-665) + completeness counting read-errors as "documented" (pipeline.py:519) let full_run_complete=true ship anyway. Orchestrator's 05:36 "rescore included" claim verified FALSE (0 heat-IFS rows, pin unchanged at 05:36 UTC). Fix = pin aiobotocore into s3fs's range + force-reschedule the 35 pairs + treat read-error no_data as blockers.
- **D2 OPEN (major)** — skill_vs_ifs_pct compares different event sets on 1083/1859 rows (all AR lane); needs per-(metric, lead) intersection or flag. Until then AR skill % is indicative, not ranking-grade.
- **D3 OPEN (major)** — no bootstrap CIs; all league numbers are plain means over 6–15 events.
- **D4 CLOSED (benign)** — 246/252 h lead rows: EWB itself warns forecast valid_time is offset from init+lead and keys on init+lead; repro matches live exactly ⇒ cosmetic labelling of a deterministic quirk, 0.35% of rows.
- **D5 DISCLOSE** — freeze lane is n=1 event; no ranking claims.

**Limitations for the About page (posted to design):** (1) heat/freeze are *percentile-relative* (p85/p15 of OWB ERA5 1990–2019 daily-mean climatology), not absolute hazard levels — the Greenland freeze at −43.7 °C is climatologically ordinary there, and "zero freezes in a season" is an acceptable outcome; (2) peak_value = ERA5 gridded **daily-mean** 2 m T over the bbox — station daily maxes run 4–8 °C higher, so figures won't match news record-max headlines; (3) daily-mean 0.25° detection cannot catch tmax-only heatwaves (BOM NW-Australia class) and missed Western Europe's record-warm Sept 2026 (FR daily records 9-08) — a tiered-area rule (R2) is the planned remedy, not a clip change; (4) model coverage varies — aigfs has no 2 m temperature (heat/freeze lane: not scoreable), FourCastNet ARs run via EWB's RH→specific-humidity conversion; the heat lane's IFS baseline is absent until D1 rescores — hide or label the heat skill column meanwhile; (5) league-table cells rest on 6–15 events without confidence intervals (D3), and AR skill % uses non-matched event subsets (D2); (6) two oversized ARs are compute-excluded from scoring though published in the event list; (7) verification truth is ERA5 itself — ERA5-trained AI models have a structural home-field advantage vs physics baselines, and truth carries ERA5's own biases; events after the ~6-day ARCO cutoff fall back to IFS-HRES 0-h analysis (none in this set); (8) results cover a rolling 90-day window — the event set changes weekly and scores do not accumulate across runs.

## F. VERDICT UPGRADE (successor 2, 2026-11-08 12:3x UTC) — PASS, on the 09:22Z full run (28,608 rows, 31 events)

ewb's D1–D3 fixes verified independently on the box (service inactive, read-only + small recomputes):

- **D1 RESOLVED**: ifs_hres and aifs_single now have 1,151 heat rows each; heat coverage 15/15 for every model except aigfs (structural: no 2 m temperature). pair_status on current events: 316 scored / 18 unsupported / 7 no_data — the residuals are exactly the documented holes: 16 aigfs "no 2 m temperature", 7 aigfs AR "no forecast data (no inits or all reads failed)" (AR events outside aigfs's archive window), 2 fourcastnet_v2 `valid_time` pairs (3d32db128915, 0e530ab9eebf — orchestrator said 3; the third sits on a retired event, not in current output). s3fs/aiobotocore pin fixed (verified via working reads, 51 pairs rescored).
- **D2 RESOLVED**: skill is computed on the model∩baseline common subset (pipeline.py:296-327, weighted by sample counts), rows carry `skill_n_common`. Recomputed the shipped formula `(m−b)/|b|×100` with sign flip for lower-is-better metrics: bit-exact on sampled cells; `skill_n_common` agrees with raw-parquet set-intersection for 2,633/2,651 skill rows; the 18 "mismatches" are an artifact of my own NaN-lead join key on DurationMeanError rows, not the pipeline.
- **D3 RESOLVED**: event-bootstrap 95% CI (B=400) over per-event (sum, count) — a count-weighted bootstrap, correct for unequal n_samples per event. Independent re-derivation matches shipped CIs within resampling noise on spot cells; mean inside CI in 2,147/2,147 CI-bearing rows; no inverted CIs; CI is correctly null where n_events<3 (all 793 nulls are freeze n=1 cells or rare 1–2-event heat cells).
- Reproducibility from the earlier sr09 pass stands (136/136 rows, 2.4e-7).

**D6 (new, minor — semantic, recommend fix or hide):** `skill_vs_ifs_pct` for **DurationMeanError** applies lower-is-better to the *signed* mean of a signed error (−198…+54 h). A model biased to under-durate more than baseline scores positive skill while having larger error magnitude: aurora heat gets **+82 %** vs IFS purely because −26.5 < −14.6 (|error| says it is ~82 % worse). 20 rows (heat/freeze duration cells). Fix: aggregate `mean(|error|)` for skill on signed-error metrics, or suppress skill for DurationMeanError. All other metrics are non-negative (RMSE/MAE/CSI/EarlySignal/displacement) and unaffected — verified bit-exact recompute there.

**FINAL VERDICT: PASS** for the scoring and aggregation pipeline as shipped in the 09:22Z run, with disclosures below. Review complete.

## DONE
Final verdict: PASS (2026-11-08), conditions D1–D3 verified fixed on live data; D6 + residual coverage holes are disclosures, tracked in channel for ewb/design.

## G. Targeted check: AR mask tropical filtering + antimeridian fix (successor 2, 2026-11-08 15:3x UTC)

**Upstream EWB tropical exclusion** (`extremeweatherbench/events/atmospheric_river.py`, verified in installed pkg): the AR mask = IVT >= 400 kg/m/s AND |Gaussian-blurred Laplacian(IVT)| >= 2.5 kg/m2/s2 dilated 8 gridpoints, then per time-linked connected component keep iff (cells >= min_size) AND **|mean latitude| > 15°** (`_filter_labels_size_and_lat`, min_abs_lat=15.0). That is the *entire* tropical exclusion: there is **no Payne-family latitude band (30°), no 850 hPa temperature-gradient criterion, and no shape/orientation (elongation) criterion** in this module. Consequences by construction: tropical tails of extra-tropical components survive; a component with mean-lat <= 15° is dropped whole.

**Our paths**: detection (events/ar.py) and the viz raw-asset path (viz/build.py `_ivt_and_mask`) both call EWB's own mask builder — the same centroid filter applies everywhere. Note the viz recomputes the mask on a bbox+2° crop, so the |mean-lat| test there is crop-local, not global (a wholly-tropical plume inside the crop is judged on the crop's latitudes). Scoring's CSI target is the detection component mask ⇒ convention is uniform across models; no cross-model bias is possible, only a convention effect on absolute CSI.

**Selected AR events, tropical content** (peak-time viz masks, 21 AR viz assets): only 3 contain |lat| < 15° cells — 97c52bf337b5 **24.7%** (173/699 cells; bbox 10.25-35N, centroid 23.3N, Bay-of-Bengal/India landfalling AR — legitimate under the upstream rule), 1ff0485667cc 8.7%, a3bb50ccde3d 2.0%. Total tropical mask cells across the whole AR set: 286 (~1.9%). So green-in-tropics is *expected upstream behaviour* (tails of mid-lat components), not a broken filter. Score impact: 97c52bf337b5 CSI@48 (0.135-0.183 across models) is in-family with non-tropical events ⇒ no distortion observed; the 3 all-zero-CSI events (a3bb, 1ff0, 0c7a) have 0-8.7% tropical cells ⇒ their zeros are the target/forecast alignment bug ewb is investigating (guardian 14:25 evidence), NOT tropical filtering. 0c7a7f10f667's TC-vortex look is the B8-rule miss (guardian 14:50), orthogonal to the lat filter (its mask has 0 tropical cells).
**Recommendation**: if the product wants strict extra-tropical ARs, that is an owner decision to *deviate from upstream EWB* (per-cell |lat| >= 20-30° cut or Payne temp-gradient test) — it breaks OWB comparability; otherwise keep upstream and explain in viz/About that the AR mask includes tropical tails by design.

**Antimeridian fix review** (ewb, owner 2026-11-08): fix = `_bbox_union` (select.py:275-295) unwraps member longitudes as signed offsets from the core-anchored lon_min (members west of the anchor get negative offsets) instead of a purely-eastward unwrap that inflated a 5°-west member to 355° east ⇒ whole-globe union. Reviewed complete set of wrap paths: overlap + IoU are arc-aware (`_lon_arcs`, `_bbox_intersects`, `_bbox_iou`), merge caps use arc span `(lon1-lon0)%360` (select.py:318), validator cross-checks bbox-wrap vs `antimeridian_wrap` detail (validate.py:86-90), fc_cache refuses to cache wrapped regions (fc_cache.py:51 — correct, costs re-reads only), scoring subset wraps via `_transform.lon_indices` (sources/_transform.py:23-25), viz uses a continuous eastward lattice (`_lon_idx`). Regression on current output: **0 overlapping pairs of 31 events** (arc-aware check), the single wrapped event 255f68102c43 is a narrow 38.2° arc (Bering), no whole-globe unions in any published bbox, and its scores are in-family (CSI@48 ~0.19 vs IFS 0.188). Verdict: fix is correct and complete for bbox/union/overlap/scoring; edge case where two merge members sit >180° apart cannot occur in IoU>=0.3 clusters.

## H. Targeted re-review of the G-34 truth-longitude fix (successor 3, 2026-11-08 19:2x-19:5x UTC, on the 19:19Z full run: 28,889 rows, 30/31 events scored, full_run_complete=true)

Evidence: `scripts/review/sr11_g34_verify.py` (+ JSON at `data/results/review/sr11_g34_verify.json`), targeted box probes (commands logged in `logs/run-20261108T19*Z.log`).

**H1. G-34 fix VERIFIED effective — all three criteria met.**
- *Western vs eastern heat RMSE now comparable* (ifs_hres, leads 24–120 h, event means): west **1.70 K mean / 1.86 max** (n=5) vs east **2.05 / 2.54** (n=10). Pre-fix: west 3.63 vs east 2.14, worst 56caf7420b9a 5.62 and 95aeafb7fd7f 5.00 → now 1.76 and 1.77. Every heat event sits in the 1.5–2.5 K family; the western-lane inflation is gone (the west is now *better* than the east, physically fine — South-American land-amplitude cases).
- *Greenland freeze plausible*: 88bb73d7ae25 RMSE by model mean 3.9–5.0 K (was 13.2 K, ~6×); IFS 4.04. Single worst row 9.22 K = pangu_weather @228 h (weakest model, deepest lead, extreme-cold case) — right class, lead-consistent.
- *Formerly all-zero ARs now score*: 1ff0485667cc CSI mean 0.113 (max 0.234) / EarlySignal mean 0.99 / 447 spatial_displacement rows; 0e530ab9eebf 0.097 (decay 0.209@24h→0.122@120h); e1bd4ec451c5 0.075 (decay 0.158→0.064); 255f68102c43 0.097 (0.165→0.102). CSI decays with lead and displacement rows reappeared for every one — the exact anti-signature of the old edge-column garbage. `all_zero_csi_events_current = []`. a3bb50ccde3d remains the documented owner-ratified oversized-AR bench_failed exclusion (11/11 pairs, disclosure #6 stands); AR lane means rest on 14 events.
- *Value-level repro on a western event* (56caf7420b9a × {ifs_hres, graphcast_gfs}, post-fix rows): **145/145 matched, max abs diff 2.38e-7 K, 93.8% bit-exact** — same standard as §F/§E.
- Code fix is at the right level (single shared `truth.py::_crop_bbox` → `normalize_longitude`, canonical −180..180 sorted lattice for both lanes, regression fixtures in `tests/test_dateline.py`).

**H2. ewb's 95aeafb7fd7f flag (max RMSE 5.00 → 6.33 K) explained: different quantity, not a regression.** The pre-fix "5.00" was the *mean over leads 24–120 h* (alignment-inflated); post-fix that same window is 1.77 K. The new 6.33 is a **single row: pangu_weather_gfs at lead 240 h**. At that lead the event's model ordering is monotone in known skill (aifs 3.39, aurora 4.19, ifs 4.25, fcn 4.2, graphcast 4.7–4.9, pangu 5.12, pangu_gfs 6.33), the event's lead curve rises smoothly (2.4→6.3), and the event's 192–240 h mean for pangu_gfs is 3.59 K. An alignment artifact is uniformly broken across models at short leads (that was the old fingerprint); this is a deep-lead tail on the weakest model. Accepted as real signal.

**H3. Cyclone screen verified at code + curation level; shipped-state provenance gap (minor).**
- Rule as documented: haversine distance IBTrACS track-center → any candidate mask pixel, radius 250 km, nearest segment day, fail-soft parquet cache, mask never modified (`events/tc_screen.py`; wiring `ar.py:236-242`, provenance written for every *new* candidate `ar.py:341`). One semantic note: "±24 h" is day-resolution — a track point matches up to 24 h + nearest-day slack (≤ ~48 h) — over-exclusionary, conservative for a screen; pin it in the docs.
- 0c7a7f10f667 = Hanna: excluded via `data/curation_exclusions.json` (owner adjudication backstop, logged) + screen evidence 227.4 km < 250 km (Hanna 2026270N28302). Not in events_selected; 0 score rows. **Minor gap: 0/15 ARs in the shipped events_selected.json carry `details.tc_screen`** — the published set predates the wiring, so the "provenance on every AR event" claim only lands from the next detection cycle. Disclose, don't ship the claim now.
- Refill integrity: 15/15/1 (31 events); replacement bebcc8d36395 (Bering AR, Sep-13..17, ocean_frac_start 0.975, landfall 09-14, 5 d ≤ 9 d cap, 11/11 models scored); arc-aware overlap **0 pairs** (cross-checked against `select._overlaps` — authoritative); all 31 carry qualified_at; scorecard rebuilt (19:23Z, AR CSI@48 n_events 12–14 reconciles with raw parquet).

**H4. NEW MAJOR (ewb): 8fe8ae212d73 (Nov-1 AR, N. America) scores structurally zero — the G-34 fingerprint on a fixed-code event.** Direct probe: the *observed* mask is strong and present across the scored window — 788→2623 mask px from 10-31 12Z through the mirror end (11-02 18Z), peak IVT 1401 — yet **CSI = 0 for all 11 models at all leads ≤ 180 h, including ifs_hres at lead 0** (valid 11-01 00Z: 1921 observed px), and nonzero CSI exists only in the lead-186–234 h band (max 0.035). Its EarlySignal is nonzero for some models and 55 displacement rows appear, so rows were computed against a target — the zero overlap cannot be physical for lead-0 IFS. Effect is quantified and bounded (AR CSI@48 lane mean 0.126 with vs 0.136 without, −7%), so it does not reopen the PASS, but it must not stay shipped as an ordinary zero-CSI event page: root-cause the target/forecast alignment for this event specifically (detection-era geometry? padded-case region?), or exclude it with a documented reason. Note its sibling boundary facts: window end 11-03 > mirror end 11-02 18Z (truth.py:48 `end-1h <= cutoff` classifies it era5_arco; late-lead zeros vs out-of-window valid are EWB-wide convention and NOT the issue — short leads are).

**VERDICT (section H): PASS maintained** for the scoring pipeline on the 19:19Z run — the G-34 fix is correct, effective, and reproducible; the 95ae flag is a deep-lead model tail. Residual: H4 (major, one event, ewb) + H3 minor (tc_screen provenance timing, day-slack doc). §E disclosures stand; add: "one AR (8fe8ae212d73) shows a pending-alignment caveat under review."

### H closure (successor 3, 2026-11-08 20:5x UTC) — H4 CLOSED, PASS FINAL on the 20:43Z run (30,586 rows, 31/31 events scored)

ewb's fix (channel 20:15Z) independently verified on the box:
- **Root cause accepted as the true one**: `make_targets` mirror-branch normalization never covered the remote-ARCO fallback (`truth.py::_normalized_target` now canonicalizes lon after EWB's `subset_data_to_case`; mirror branch untouched; test `test_remote_arco_target_normalizes_after_subset`). Probe of `mirror_covers` over all 31 events: 8fe8ae212d73 is the ONLY event on the remote path (end 11-03 > mirror end 11-02 18Z) — no other boundary-crossing event exists to be affected.
- **8fe8ae212d73 now scores physical**: CSI mean 0.122 / max 0.292, 0.19–0.21 at leads 0–47 h for ALL 11 models decaying with lead (aurora/pangu → ~0 by 192–240 h, the usual deep-lead pattern), 412 displacement rows, ESS mean 0.936.
- **Regression check added** to `scripts/review/sr11_g34_verify.py`: `short_lead_zero_csi_events` (any scored AR with CSI=0 for every model at leads ≤48 h = this bug class' fingerprint) — currently **[]**, as is `all_zero_csi_events_current`. Run this every time the AR lane republishes.
- **a3bb50ccde3d correction accepted**: pair_status 11/11 `scored`, 1,340 rows, CSI mean 0.099 / max 0.242 — in family; the "oversized bench_failed" was transient memory headroom, not structural. AR lane means now rest on **15/15 events** (§E disclosure #6 is moot; dashboard should drop the "two ARs excluded" note at next rebuild).
- **H3 minors closed as filed**: day-slack pinned in the tc_screen docstring (~36–48 h effective, over-exclusionary by design); tc_screen provenance absence on the shipped set disclosed, claim scoped to "from the next detection cycle".
- Post-fix lane numbers reconcile end-to-end: west/east heat RMSE unchanged (1.70/2.05 K), Greenland IFS 4.04 K mean, scorecard AR CSI@48 ifs_hres 0.1672 with n_events=15 == raw parquet recompute.

**FINAL VERDICT: PASS, no open items.** Residual disclosures for the About page: (1) heat/freeze percentile-relative (p85/p15); (2) peak_value = gridded daily-mean; (3) detection can miss tmax-only heatwaves; (4) model variable coverage varies (aigfs no t2m); (5) league cells rest on few events (CIs shipped, D3); (6) truth = ERA5 — home-field advantage for ERA5-trained models; latest events score against remote ARCO until the local mirror catches up (aligned + normalized now, verified); (7) freeze lane n=1; (8) AR mask includes tropical tails by upstream design; 0c7a/Hanna-class TC screening applies from the next detection cycle. Review closed.
