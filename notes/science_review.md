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

---

# Section I — AR tropical exclusion, tracking/persistence, seam connectivity, curation, scores (2026-11-09, successor 3)

## Method + a correction against myself
Audited the live box (frames 336, rescored 26,200 rows, full_run_complete 07:02Z) with
sr12/sr13. FIRST PASS CONTAINED FALSE POSITIVES: my raw-bin decode skipped
`np.unpackbits` (byte-as-pixel). All raw-layer claims from that pass were retracted on
channel at 07:4xZ before any verdict was written; scripts now import the canonical
`exceedance.frame_layers()` decoder. Everything below is from the corrected run.
Independent code paths (xarray event masks, parquet, JSON) were never affected.

## Rule implementation (code, both surfaces) — PASS
`ewb_live/events/ar_filter.py` is the single rule (8-conn labeling, strict |lat|<15 touch,
storm-driven TC screen 500 km / ±24 h +6 h grid tolerance, in-or-out over ALL of a track's
steps); called by detection (`ar.py:151`) and by the raw-layer builder
(`exceedance.py:394`, same call chain → D1/D2 can't diverge). `|lat| = 15.00` center
policy pinned in SPEC + code (ewb 03:0x post; cells at exactly ±15 retained, documented).

## Raw layer — PASS (value-level, all 336 frames, independent IBTrACS)
- AR pixels at |lat| < 15: **0**. Pixels exactly at ±15.00: 57 — compliant by pinned rule.
- Retained AR px vs every gated IBTrACS storm point (my own haversine, 102 storms):
  closest retained pixel to any TC center across all 336 frames = **503.4 km**
  (2026101700) — 3.4 km inside the 500 km line. Compliant; margin is thin by construction
  (grid 0.25°), noted, no action.
- The earlier "NOLO 7.6 km residual" was my decode artifact — withdrawn.

## Events — PASS
- 12 ARs; TC-screen provenance 12/12; events↔tracks bijection holds (12 unique track_ids).
- Tropical drops verified at pixel level: a3bb50ccde3d (10.75°), 97c52bf337b5 (7.0°),
  1ff0485667cc (10.25°) — genuine mask pixels < 15°, correctly dropped; 5d13cb4e4747
  (12.5° per ewb's recorded touch, consistent with its disappearance in the seam rerun).
- bbox-touch survivors are padding artifacts, pixel-verified: 4a9c67b01f25 min|lat| 19.0°,
  bf07d11c8060 window-wide min|lat| 19.5° — no mask pixel in the band. Curation
  82dc9c1b533c has exited selection via the seam/merge churn (not a tropical drop).

## Seam (periodic longitude) — PASS
- UK Oct-3 AR (0df36ee11032): single component/track (`ar_20261005T18_4764`), single bbox
  [50.5, 71.75, 0.25, 23.5], no split twin. (Track id stamps Oct 5 vs event start Oct 3 —
  provenance quirk only.)
- All bboxes: arc < 180°, no bad wraps, none ≥180. Three events cross 0°E (e86c13f47bc0,
  c3a4e175680c, +1) — each a single event with unique track_id, curation-conflict-free.

## Flicker — PASS with scope note
Independent per-frame rebuild (8-conn, ≥50 px) + 30%-overlap linkage over all 336 frames:
1,835 objects, **84 on-off-on transitions — 1 in a real AR region** (22-px speckle, Bay of
Bengal 2026-08-11; a sub-threshold speckle, not a curated object), the rest single-frame
speckle churn at the floor. The Japan-scale blink the owner saw is not reproducible in the
published layer; track-level filtering + hysteresis is working. Scope note: this audits the
published grid, not the ledger's internal linking; the 22-px speckle is cosmetic and below
the 500-px upstream floor.

## Scores — 2 open items
- Symmetry: 12/12 selected ARs scored, 0 phantom ids, 26,200 rows, AR CSI@48 mean 0.1282.
- **[MAJOR] e86c13f47bc0 short-lead-zero persists at completion**: CSI = 0 for ALL models
  at leads 0–144 h; only ifs_hres nonzero (0.081, ~192 h band). H4-style zero fingerprint on
  a mirror-path event (window ends Nov 1 < mirror end Nov 2) — the ARCO mechanism does NOT
  apply, so this is a NEW root cause. ewb acknowledged WATCH→ must root-cause H4-style.
- **[MINOR] scorecard AR@48 ifs n=11 vs 12 scored**: leave-one-out on the raw parquet
  identifies **bbbd78137ddd** as the excluded event (ifs@48 0.1685). Silent exclusion —
  needs a documented reason or inclusion.

## Churn (my reconstruction from evidence; ewb's final map still pending)
Original 15 → final 12: dropped 7 = tropical {a3bb, 97c52, 1ff04, 5d13c} + seam-merge
{8fe8ae212d73 → into 4a9c6, 791068e83b87, e62186d67646}; retained 8; new 4 (bf07d, eb65e,
c3a4e, 79d28) admitted at track level. Awaiting ewb's authoritative map to confirm.

## VERDICT section I: PASS with 2 open items
Blocking close-out: (1) e86c root cause (MAJOR — do not ship AR lane headline stats until
resolved or disclosed); (2) scorecard exclusion reason for bbbd78137ddd (MINOR). Churn map
confirmation pending from ewb. Everything else — raw compliance, event rule, seam,
flicker, bijection, curation — verified on corrected decoding.
— science-review (successor 3), 2026-11-09 07:5x UTC

## Section I — addendum 2026-11-09 08:5x UTC (post-ponytail sweep, live box state)
- Raw-layer D2 now triple-confirmed CLEAN on the published layer: fixed d2_pixel_audit,
  sr12, and the earlier battery all give min AR-px->TC-center = 503.4 km > 500 km over all
  336 frames; every frame has a Nov-9 04:33Z rebuild mtime, so the durbackfill-contaminated
  Aug-26 cluster (LALA 0.0 km) is gone from what ships. `layer_rev` still reads v4 — the
  stamp is stale, the bytes are current-screen (ewb's v5 bump is a cache-key formality).
- e86c13f47bc0 MAJOR: purge landed (817 stale rows dropped; 0 rows now, rescore unit
  ewb-live-e86crescore3 ACTIVE mid-run). short_lead_zero=[] is purge-effect, NOT a fix —
  MAJOR stays open until rows return with sane short-lead CSI; watcher armed.
- Churn map received (ewb: 3 same-id + 4 re-id + 6 track-level drops + 5 new = 12). My
  id-level reconstruction from the 20:43 snapshot differs in re-id bookkeeping (8 same-id
  keeps from exact-id diff), but the load-bearing invariants both agree on: no shipped
  event references a dropped track, events<->tracks bijection 12/12, curation conflicts 0.
  Accepted as delivered; the drop-reason classes match my pixel-verified set.
- Open for §I close: (1) e86c rescore result, (2) bbbd78137ddd scorecard exclusion reason.

## Section I — FINAL CLOSURE 2026-11-09 09:1x UTC: PASS, no open items
- [MAJOR e86c13f47bc0] CLOSED. Root cause accepted: stale 27-row truth part from a Nov-7
  partial write (leads 192–234 only, fingerprint-only invalidation kept it) — the ARCO
  mechanism genuinely did not apply, as flagged; this was a third, distinct cause.
  Verified after e86crescore3: 1,182 rows, 11/11 models x 41 leads, pair_status all
  'scored', short-lead CSI 0.03–0.32 across models (ifs mean 0.094 at <=48 h), max 0.318.
  Whole-lane tripwire `ARs with all-zero short-lead CSI` = [] on the current parquet.
- [MINOR scorecard n=11] CLOSED, and my attribution was WRONG: leave-one-out pointed at
  bbbd78137ddd, but the actually-absent event was e86c (no lead-48 rows in its stale part;
  LOO matched bbbd within my 0.0006 tolerance by arithmetic luck — recorded as a method
  limitation: LOO-attribution is only valid at tighter tolerance or with the cell's event
  list exposed). Now verified live: AR@48 ifs cell n=12, mean 0.1549 == 12-event parquet
  mean exactly (e86c ifs@48 = 0.0455). No silent exclusion.
- Raw-layer D2: triple-confirmed CLEAN (503.4 km > 500 km, 336 frames, rebuilt bytes).
- FINAL VERDICT section I: PASS.
— science-review (successor 3)

## §J — Owner HIGH report: landfalling ARs not considered events (2026-11-09, CLOSED — no bugs, four rule attributions)
Full audit in `notes/review_artifacts/ar_landfall_funnel.md` (65-row candidate table, ledger top-20, funnel counts, maps sr-arfunnel-1..5). Verdict: every landfall rejection attributable to a named owner rule; zero bugs (TC screen/tropical/land mask/seam re-verified; mirror window + cross-type overlap dropped zero this window, proven by replaying production rank_and_cap). Impact ranking: (1) landfall_days>=3 floor: 37/65, incl. IVT 980/943/840 landfallers; (2) span<=60 cap: deletes window's #1 AR eb42387c81a3 (IVT 1038, 8 landfall days, 3255 px/day); (3) severity quota: ranks 15-23, IVT 938 out while 600-653 ship; (4) publish gate peak_land<500: silent -3 (15->12, no decision log). Land-locked raw-map blobs (Kara/Ural/monsoon) correctly die at ocean-start. Tracking-split real at ledger level (304 1-day land-touching fragments (daily-union px)), benign at event level. Owner ASKs: floor 2-vs-3, span exemption, ranking formula. ewb owns: gate rejections -> curation.json + always ERROR-log. Repro scripts scripts/review/sr14*.py; ledger JSON /tmp/sr14_funnel.json on box. — science-review (successor 3)

### §J addendum (pi, 2026-11-10) — rank_cap_replay mismatch root-caused, no production bug
The replay-vs-shipped mismatch flagged by ponytail 2026-11-10 00:10Z is code-era
drift: shipped artifacts are from the floor=3 world (Nov-09 08:13Z runner);
AR_MIN_LANDFALL_DAYS was relaxed to 1 later that day (owner landfall-funnel
amendment, select.py:184). A/B on the box with the recompute_merged_max_consec
mirror applied: floor=3 reproduces the shipped curation exactly (26 floor-pass,
9 quota drops incl. 85636ad4eb4c, 2 merges, gate triple a6126c/adcdad/ce2db7,
kept 28 == shipped 28); floor=1 reproduces the reported mismatch set exactly.
rank_and_cap is deterministic and correct. rank_cap_replay.py pinned to the
artifact era (EWB_REPLAY_LANDFALL_FLOOR=3 default, unpin after next curation
run). Next curation will ship 06ceb4d8d4fe and the merged 49a026+8002c product
e1bd4ec451c5 in place of 0df36ee11032/8002c1ab3ba0 — exactly as §J predicted
for the floor change. Dispositions: channel_ponytail_science_review.md
(pi, 03:1xZ), channel_science_review.md (pi, 03:2xZ).

## §K — Flicker re-audit against dur-rev-v2 layer (2026-11-10, referee)

Scope: §I's flicker verdict was generalized from a truncated 15-blink view.
Re-ran sr13 (v2, full dump) on the current 336-frame published layer and
attributed every curated-window blink to the per-frame `.trk` label sidecars
(decoders imported from ewb_live.viz.exceedance, never hand-decoded).

Findings:
1. Population: 75 flicker blinks over the window, 49 >= 500 px (was reported
   as ~15 floor specks). Sample truncation in §I understated prevalence.
2. Curated overlap: 5 big blinks fall inside published curated AR event
   windows AND bboxes (6b4dec9dc8db x2, 920f2f81e54d x2, bf07d11c8060 x1;
   766-9745 px).
3. Attribution (.trk labels, before/gap/after frames):
   - 920f2f81e54d @ 2026090606: NOT a blink — gap frame holds 2840 AR px,
     same label id (1083) before and after. sr13's 3x3 chain matcher splits
     continuous masks; artifact of my audit script, flagged for sr13 fix.
   - 4 TRUE blinks (6b4dec9dc8db @ 2026082712 gap-px 21 and 2026082918 gap-px
     0; 920f2f81e54d @ 2026090706 gap-px 0; bf07d11c8060 @ 2026091406 gap-px
     43). Inside the event's own bbox+window the AR mask is empty or
     near-empty at the gap hour, and the before/after components carry
     DIFFERENT track ids; none of these gap frames appears in any track's
     `bridged` list. The tracker never bridges them; the display layer
     shows on-off-on within the curated footprint.
4. Not a v2 regression: LAYER_REV v7 and the mask/screen rules are
   unchanged by the dur-rev-v2 rebuild (duration sidecars only); these
   blinks predate v2 — §I simply missed them.

Verdict: §I's "no published flicker within curated event windows" is
OVERTURNED for these 4 cases. Requirement "no on-off-on objects in
published raw layer after track-level filtering" is violated at the
display level for curated AR windows. Lane call for owner: whether the
gap-bridge (exceedance.py _ar_window_pass, bridged) should extend across
1-step interior dropouts of curated-track footprints, or whether the
events curator should exclude windows containing empty-interior gaps.
Referee recommendation: bridge interior dropouts up to the existing
bridge window when the event is curated (the pixels are already
re-extended in the other direction); do not shrink events.

Method note: first attribution pass silently indexed nothing (bbox is
[latmin,latmax,lonmin,lonmax], grid south-first) — caught by a global
mask/label coverage check (mask px with zero labels must be 0; it was 0
globally but non-empty nowhere was impossible given blink px). Verify
non-empty before trusting a bbox result.

Artifacts (survive worktree git-clean — root-owned box logs, NOT worktree):
  /opt/ewb-live/logs/sr13_v2_report.json   (full blink dump + curated overlay)

### §K follow-up — v3 re-audit with authoritative `.trk` label veto (2026-11-10)

sr13 chain matcher fixed: chains now carry the per-pixel track-id sets from the
`.trk` sidecar (decoded via imported `labels_from_bytes`); a candidate on-off-on
is vetoed when before/after components share a label id. Re-run on the full
336-frame dur-rev-v2 layer (box: `logs/sr13_v3_stdout.json`):

- v2 reported 75 blinks / 49 >=500px / 5 curated-window. v3: **68 / 43 / 4** —
  the 7 removed are chain-matcher artifacts, including `920f2f81e54d @2026090606`
  (label 1083 continuous), exactly as §K predicted. The 4 TRUE curated flickers
  are unchanged; §K verdict stands, now machine-confirmed, not hand-adjudicated.
- Join caveat found the hard way: `ar_tracks.json` value ordinals and curated
  `details.track_id` ordinals are DIFFERENT generation eras (`ar_20260828T12_2349`
  has no exact ledger value; only an unrelated `ar_20260927T00_2349`). Event->label
  joins by id are invalid on this layer; use label-free mask geometry inside the
  event bbox instead (assert non-empty index sets first — a silent empty slice
  once reported 'all clear').
- Stronger evidence found: label-free AR-px series in the event bbox for
  `920f2f81e54d` (window 0906-0909): dark at 090600,090606,090706 AND a
  **3-frame blackout 090800/090806/090812** between bright neighbors (1084px ->
  0,0,0 -> 1372px). The 1-step blink rule does not even count a multi-frame
  blackout; the curated footprint visibly vanishes for 18h. 6b4dec9dc8db dips to
  21px@082712 and bf07d11c8060 to 43px@091406 inside its box.
- Referee recommendation unchanged and strengthened: extend display gap-bridge to
  interior dropouts (incl. multi-frame) for curated/tracked ARs, or exclude
  gap-containing windows from curation. The blackout case argues the gap-bridge
  extension should cover >=3 consecutive dark frames, not just single steps.

## §L — Population audit: interior dark frames in curated AR footprints (2026-11-10)

Label-free AR-px series (imported `frame_layers`, bbox-geometry only — no cross-era
id joins) for all 12 `event_type=="atmospheric_river"` curated events, over their
window frames in the published dur-rev-v2 raw layer:

- **9 of 12 AR events have >=1 interior frame with zero AR pixels in their own bbox**;
  11 of 12 have a dark frame somewhere in-window. Max consecutive dark runs:
  8002c1ab3ba0 **7 frames (42h)**, 0df36ee11032 / 56b282629cf6 / e86c13f47bc0 5,
  bbbd78137ddd 4, 920f2f81e54d 3, 65243d05a247 2; eb65e5e99645 / 4a9c67b01f25 /
  6b4dec9dc8db / bf07d11c8060 <=1. Only 029719d1e741 is continuously bright (min 78px).
- The 4 sr13 v3 curated flickers are the TIP: the on-off-on rule counts only
  1-frame gaps; multi-frame blackouts (majority of the damage) are invisible to it.
- Caveats: (a) event_type of non-AR curated events (15 heat_wave, 1 freeze) makes
  AR-darkness meaningless there — excluded; (b) a dark interior frame can also mean
  the curated window outlives the object's actual life or spans two objects —
  indistinguishable at footprint level because event->label id joins are invalid
  across generation eras (§K follow-up). Either reading is a curation violation:
  the published footprint of a curated event does not match the published frames.
- Verdict: flicker is the DOMINANT condition of the curated AR set (9-11 of 12),
  not a 4-event exception. §K's decision (extend gap-bridge across interior
  dropouts, incl. multi-frame, vs exclude gap windows from curation) now has full
  population support. Referee recommendation: extend gap-bridge to multi-frame
  interior dropouts for curated tracks AND add a curation gate that rejects
  windows with interior blackouts the bridge cannot justify.
  Artifact: box `logs/` + scratch sr14b (script trivial; re-derivable in 2 min).

### §L.1 — AMENDMENT (2026-11-10): exact-hour windows + dead/moved classification
Corrections to §L (referee self-audit):
1. sr14/sr14b clipped frame windows by DAY (`d0 <= tag[:8] <= d1`) — a window
   ending 2026-10-30T00:00 wrongly included frames through 10-30 18Z. Re-run with
   exact `YYYYMMDDHH` bounds. Headline survives (9/12 events, 25 interior dark
   frames) but §L's "max run 7 = 42h" was inflated by post-end frames; true max
   interior run = 3 frames (18h; 920f2f81e54d 090800–090812, confirmed real).
2. New classification, bbox+5° AR presence per interior dark frame:
   - DEAD (no AR in bbox+5°) — 19 frames / 6 events (920f x4, 0df3 x4, 8002 x4,
     56b2 x4, 65243 x2, eb65 x1): the object is GONE — curated window outlives or
     spans gaps in the object's life. NOT flicker; a curation-window defect.
   - NEARBY (AR alive outside static bbox) — 6 frames / 5 events (e86c x2, bbbd,
     4a9c, 920f@090606, 56b2@102412): object alive, bbox too tight / object moved.
   - sr13 v3's 3 blink events (6b4dec, bf07, 920f partial) have ZERO interior
     bbox-darkness — true object blinks happen while the bbox stays lit by other
     AR pixels. The two failure modes are disjoint at bbox level.
3. Consequence for the owner decision: gap-bridge fixes ONLY the sr13 blink class
   (2–3 events). The majority class (6/9 interior-dark events) needs a curation
   gate: clamp curated windows to the tracked object's actual life and reject
   windows with unbridgable interior dead spans. The builder flicker gate does not
   catch this class (a permanently-ended track is not an on-off-on blink).
   Artifacts: scratch sr14d_exact.py (re-derivable, 2 min on box).

### §L.2 — Gate claim verified against source (2026-11-10)
`flickering_tracks` (exceedance.py:410-424) fires only on an interior gap between
two appearances of the SAME track id (min_span 12). A track that DIES has no
second appearance -> structurally invisible; and the build comment (:659-661)
states physical blinks re-link as NEW ids in ar_filter, "invisible here by
design" — sr13_flicker chains are the designated real detector, and they are
audit-only (post-rebuild box gate), not a curation gate. So NO shipped automated
check rejects a curated window containing an interior dead span. §L.1's
two-mechanism recommendation stands, now source-confirmed.

### Version pin (all of §K–§L.2)
Frame lane: generated 2026-11-10T02:48Z, schema 3, 336 frames 2026081100→2026110218
(window/cutoff same) — every §K–§L.2 script saw this one lane version.
Curation input: events_selected.json sha256:51f96b150336625a. A lane rebuild
(generated/frames change) or events file change invalidates these numbers;
re-run scratch sr13_flicker.py + sr14d_exact.py to refresh.

## §M — Non-AR curated events audited on their own planes (2026-11-10, sr15)
Extended the §L.1 exact-hour interior-darkness audit to all 28 curated events,
each tested against its OWN lane plane (heat_wave->h, freeze->f, AR->a;
frame_layers plane order 0=h,1=f,2=a confirmed by result, see below).
Result: identical totals to the AR-only run (28 events, 9 with interior dark,
25 frames, 19 DEAD) and all 9 flagged ids are atmospheric_river. =>
15 heat_wave + 1 freeze events have ZERO interior-dark frames on their own
planes — the window-outlives-object defect is AR-class-only (heat/freeze
exceedance areas are broad and stay non-empty across event bboxes).
Plane-order sanity: if h/f were swapped, heat events would read dark on the
freeze plane en masse; they read fully lit. Order verified by construction.
Lane unchanged since §K pin (generated 2026-11-10T02:48:26Z, 336 frames) —
pin still valid.
Referee scope is now COMPLETE across all event classes and both layers.
Artifact: scratch sr15_all_kinds.py (sr14d + per-kind plane index).

## §M.1 — Addendum: plane order from source; raw lane IS public (2026-11-10)
1. frame_bytes(heat, freeze, ar) packs heat|freeze|ar in that order
   (exceedance.py) — plane 0=h, 1=f, 2=a CONFIRMED from source, upgrading
   §M's result-shape inference.
2. mergedmap.js renders data/results/raw_exceedance/{ts}.bin for ALL frames
   globally (mergedmap.js:156,801) — the dashboard shows the raw exceedance
   canvas for every frame, curated or not. => sr13 v3's 68 blinks, incl. the
   43 big (>=500px) non-curated ones, ARE user-visible flicker on the live
   merged map. The §K curation-side fix does not cover them; the owner's
   gap-bridge decision should consider a raw-layer track-level bridge as well.

## §N — The 43 big non-curated blinks, characterized (2026-11-10)
From box logs/sr13_v3_stdout.json `big_blinks` (gap_frame, bbox, px):
- Sizes 503..9745 px (median ~2.5k); 36 distinct gap-days, max 3 blinks in any
  frame — DIFFUSE, not one broken day/frame.
- Geography: 24 S-mid / 19 N-mid latitude, ZERO tropical big blinks — the
  tropical mask rule is working; all visible flicker is in permitted zones.
- 21/43 intersect a curated AR bbox somewhere, but only 4 fall inside that
  event's window AND bbox (the §K set); the rest are pass-throughs near the
  two giant bboxes (bf07 spans the S-Atlantic corridor) — weak evidence, do
  not read as curated-event membership.
- User impact: per §M.1 these are rendered on the live merged map. At ~1.2
  blink-frames/day globally the aggregate visibility is real but low-grade;
  a raw-layer track-level gap-bridge (same mechanism as §L.1 recommendation)
  would remove all of them at once. No separate mechanism needed.
Artifact: scratch sr13_v3.json (copy of box report). Handoff TODO "audit 43
big blinks" CLOSED here.

## §O — Acceptance gate pre-registered (2026-11-10, before any fix exists)
scripts/review/sr_gate.py (repo, synced to box) runs on the box after any
lane rebuild / curation change. Gates, fixed NOW so goalposts can't move:
  G1 sr13_flicker.py: in_curated_ar_window_and_bbox == [] (no curated-blink).
  G2 sr15_event_lifecycle.py: 0 interior DEAD frames on each event's own
     plane (h/f/ar). NEARBY frames report-only — objects may out-move a
     static bbox; that's a display-tightness issue, not a lifecycle defect.
Exit 0 only when both pass. Known-bad baseline on the pinned lane: G1=4,
G2=19 — first run MUST fail both (a gate that passes on known-bad is broken).
sr13/sr15 stdout now carry a GATE_JSON: marker line for machine parsing.
Also promoted sr15_event_lifecycle.py from 24h-pruned scratch into repo so
the §L.1/§M evidence artifact survives.

### §O validation run (2026-11-10, lane 20261110T0248)
sr_gate.py on box: G1 FAIL n=4, G2 FAIL n=19, NEARBY=6 — identical, frame for
frame, to §K and §L.1 manual results. Gate validated on known-bad. Any future
exit 0 == fix accepted. logs/sr_gate_20261110.log on box.

## §P — Design-lane 25/75 blink split: validated on full timeline; their script is broken (2026-11-10)
Claim under review (channel_design_ewb 2026-11-09): blink px = 25% wholly-gone
components / 75% edge erosion; root cause 6-hour threshold jitter.
Replication of tmp/blink_class.py detector (pixel blink = a&c&~b, parent comps
labeled at t-1, whole if <10% of comp survives at t) on the FULL 335-window
timeline, current lane (2026-11-10T02:48 build):
  wholly-gone: 67,089 px / 239 objs;  edge-eroded: 202,504 px  ->  24.9% / 75.1%.
VERDICT: the split reproduces exactly on the full timeline. Design-lane
conclusion stands; root-cause stories converge (threshold jitter; 75% is border
jitter, 25% true whole-frame dropouts).
CAVEAT: tmp/blink_class.py as written cannot produce that number today —
unpackbits yields uint8, so `bl` is uint8 and `lab[bl]`/`comp[bl]` are FANCY
indexes (positions 0/1), not boolean masks. One-frame proof (2026081106,
2,309 real blink px): as-written `np.unique(lab[bl])` sees exactly ONE label —
0, i.e. background — while the bool-cast sees 13 parent components; and
`(lab==0)[bl].sum()` returns 1,495,065,600 instead of 2,309. As-written the
script classifies background rows 0/1, prints whole=0 + bogus edge counts, and
hangs for >15 min on the ::7 sample doing billion-element gathers (killed).
Their 25/75 must have come from an earlier or interactive variant; the claim text lives in
notes/channel_design_frontend.md:966 ("Classification (sampled)"). Note their
blink-PX totals (~270k, worst-frame lists) ARE reproducible from the broken
script because plain `bl.sum()` is immune to the dtype bug — only the
component classification was garbage. Conclusion
right, artifact wrong — do NOT reuse tmp/blink_* scripts. Corrected memory-safe
replication promoted: scripts/review/blink_class_full3.py (bool-cast + bincount).
Mechanism implication for the owner call: hysteresis (design option A) attacks
the 75% border-jitter majority at the source; gap-bridging masks the 25% whole
dropouts (the ones sr13 counts as track blinks). They are complementary, not
either/or: sr_gate G1/G2 only certify what gap-bridging + curation clamp fix.

## §P.1 — design-lane root-cause report: remaining stats verified (2026-11-10)
Referee check of the load-bearing numbers in notes/channel_design_frontend.md:945-980,
run read-only on the pinned lane (generated 2026-11-10T02:48:26Z):
- Blink-px total: **269,593** — matches their "~270k" exactly.
- Single-step track share: current lane **86.7%** (2892/3335, t0→t1 ≤6h) vs their
  77% (v5 lane). Direction holds, stronger on dur-rev-v2; exact % is lane-version
  dependent, as the §P version pin predicts.
- Worst blink frame: current lane 2026090600 (4,950 px, 17.5% of AR px); their
  claimed worst 2026090400 = 5,327 px ≈19% measures 4,885 px / 25.6% here.
  Same order of magnitude; exact identity of the worst frame shifts between
  lane builds. Their implied AR denominator (~28k) actually matches 2026090600
  (28,345 px) — likely a tag slip in their note, not a method error.
Verdict: root-cause report VERDICT = validated; all mechanism-relevant facts
(pixel blink everywhere, ~270k px, majority-single-step tracks, 25/75 split)
reproduce. Exact per-frame/per-track statistics must be quoted with the lane
build attached. No changes to the owner decision packet needed.

## §Q — curation-lifetime clamp cost simulation (scripts/review/sr16_clamp_sim.py, 2026-11-10)
Simulated the REQUIRED clamp (window end = object life; unbridgable DEAD runs
cut, window-reject if DEAD at frame 0) on the pinned lane, bridge sizes 1 and 3.
Consistency check vs §L.1: all 6 DEAD events appear; 920f run (3 frames) exact;
non-DEAD events keep.
- bridge=3: 1 clamp / 0 rejects / 11 keep — 4 frames lost of 180 curated-AR (2.2%).
- bridge=1: 4 clamps / 1 reject — 37/180 lost (20.6%).
New fact: 8002c1ab3ba0's curated window STARTS dead (DEAD run at window frame 0)
— curation accepted a window whose object is absent at t0; only the reject path
of a lifetime gate catches this.
Implication for the owner: the clamp is near-free (2.2% curated frames at
bridge=3) and kills the entire §L.1 interior-DEAD failure mode; it is
independent of the A/B/C pick.

### §Q addendum — starts-dead class is source-verified uncaught (2026-11-10)
validate.py's AR checks are scalar self-consistency only (peak IVT vs
threshold, landfall_days>=1, centroid_lat in bbox±2.5°). No shipped check
requires mask presence in ANY window frame; centroid/landfall are aggregate
stats, blind to WHEN the object exists. The §Q 8002 starts-dead window
passes every curation validator. Claim stands as source fact, like §L.2.

## §R — gate amendment + §Q correction (2026-11-10, pre-implementation, legitimate)
Flaw in my own §O pre-registration: G1/G2 counted raw blink/DEAD *frames*,
but a fix shipping a K-frame gap-bridge legitimately leaves runs <=K — the
gate would have rejected a correct fix. Amendment (sr_gate.py --bridge N,
default 0 preserves original semantics; sr15 now emits dead_runs + start_dead):
- G1: curated-window blinks with gap > bridge (blinks are gap-1 by construction).
- G2: interior DEAD *runs* > bridge (interior-only; run to window edge = tail death, not flicker).
- G3 (new): no curated window starts DEAD — the §Q dead-at-t0 class, which
  interior-only G2 provably cannot catch.
Verified on pinned lane: --bridge 0 -> G2 FAIL (12 runs/19 frames, baseline
preserved); --bridge 3 -> G2 PASS but G3 FAIL ["8002c1ab3ba0"].
CORRECTION to §L.1/§Q recommendation: bridge=3 ALONE kills the interior
flicker class (longest interior run = 3; 8002's run-of-4 only reaches 4 by
including its window-edge frame). The lifetime clamp is required only for
the dead-at-t0 class (1 event) + tail hygiene — cheaper than §Q stated.

### §R addendum — full amended-gate baseline confirmed on box (2026-11-10)
sr_gate.py (no flags, bridge=0 default), pinned lane 2026-11-10T02:48:26Z:
G1 FAIL 4 curated blinks (= §K), G2 FAIL 12 DEAD runs/19 frames (= §L.1),
G3 FAIL 1 start-dead window 8002 (= §Q). Every gate fails pre-fix, matching
each section's independent count — the amended gate is calibrated.
(Note: the run's printed RC=0 was ssh-pipeline artifact, $? after tail; the
FAIL lines are authoritative.)

## §S — referee quantification: region assignment centroid-vs-overlap (design lane owner-call, 2026-11-10)
Question (channel_design_frontend.md:1574, ewb_live/regions.py on box): centroid
assignment vs footprint-overlap>25% for thin ARs. Measured on pinned lane
(sr17_region_assignment.py): plane-2 footprint pixels of all 12 curated AR
events over exact-hour windows, binned per dashboard/regions.json box.
RESULT: 0 disagreements. Centroid regions == regions holding >=25% of
footprint for every event (incl. the thin ones and 8002 near the dateline).
The centroid scheme's cost on the current curated set is ZERO; the design
lane's concern is a real theoretical failure mode (footprint majority outside
the centroid region) but has no observed instance in 12/12 events. No fix
needed now; the overlap rule only earns its complexity when a DISAGREE fires.
Caveat: >=25% computed on window-summed pixel counts; hemisphere boxes
co-match by design (documented multi-membership).

## §S.1 — §S caveats closed + n<5 muting quantified (2026-11-10)
(1) Per-frame rule (region holds >=25% of a FRAME's footprint, union over
window) re-run vs centroid: still 0/12 DISAGREE — the sweep-across-regions
failure mode has no instance on the pinned lane either. Centroid verdict stands.
(2) n<5 scorecard muting, cell census from events_selected (28 curated) x
assign_regions: 10 filled region x type cells; 3 have n<5 —
north_america/heat_wave (n=1), global/freeze (n=1), northern_hemisphere/freeze
(n=1). Muting n<5 today silences ALL freeze scorecards and the only NA heat
cell. If the owner wants freeze visible at all, the threshold must come with
an "n shown, dimmed" fallback or it deletes a whole event class from the UI.
Caveat: n here = curated events per cell (the binding constraint), not the
scorecard's underlying verification sample count.

## §T — consequence note: pi's landfall-floor FLAG vs the acceptance gate (2026-11-10)
channel_ponytail_science_review.md 03:14Z (A/B-proven on box): shipped events
are floor=3-era; the 3->1 relaxation means the next pipeline run swaps the
curated AR set (06ceb4d8d4fe in; 0df36ee11032 + 8002c1ab3ba0 out via merge
e1bd4ec451c5). Referee consequences:
1. All §K-§S baselines (G1=4, G2=12/19, G3=1, 0/12 region-disagreements) are
   pinned to events_selected sha 51f96b150336625a and do NOT transfer.
2. 8002c1ab3ba0 — the ONLY G3 start-dead event and §Q's clamp target — leaves
   with the swap. A G3 PASS on the post-run set is NOT evidence the lifetime
   gate works; it may just mean the case vanished. To validate the fix
   honestly: run the gate BEFORE and AFTER the regeneration; the diff must be
   explicable by the set change alone, with the fix's bridge/clamp effect
   isolated on events present in BOTH eras.
3. sr_gate reads events_selected live, so it needs no change — but the
   acceptance run must record events sha in its log (lane pin is already
   printed; add events sha if the fix lane touches the script).

## §T addendum — gate now self-pins events sha (2026-11-10)
sr_gate.py prints `events_sha` (first 16 of sha256, events_selected.json)
next to the lane pin; verified on box: 51f96b150336625a on the current set.
Before/after-regeneration acceptance logs are now self-identifying per §T.

## §S.2 (2026-11-10) — §S tripwire shipped as gate leg G4
The §S recommendation ("keep centroid, ship sr17 as a regression tripwire") is now mechanical: `sr_gate.py` runs `sr17_region_assignment.py` as G4 and fails on any centroid-vs-overlap disagreement. Verified on the pinned lane (`--fast --bridge 3`, gate rc=1 from G3 as expected): G4 PASS 0 disagreements; G2 PASS, G3 FAIL ["8002c1ab3ba0"], events_sha 51f96b150336625a — baseline semantics unchanged by the addition. CLOSED.

## §U — ponytail round 2026-11-10 09:28Z: 3 findings, all applied (2026-11-10)

MUST-1 (gate fault line, sr_gate.py): VALID. run() aborted at the first failed
leg, discarding already-computed verdicts. Rewrite: every leg prints as it
completes; a crashed/verdict-less leg prints ERROR, the run continues, and a
broken leg can never yield exit 0 (ok stays False). G4 kept (regions.py +
regions.json present on box — G4 PASS verified; ERROR path is the defense for
trees without them, which is the correct treatment: broken leg != passing fix).
Baseline re-verified post-rewrite: G1=4 FAIL / G2=12 runs FAIL / G3=1 FAIL /
G4=0 PASS, rc=1, pins unchanged — bit-for-bit identical to §R.

MUST-2 (wrapped lon, sr13/sr15/sr16/sr17): VALID, fix applied verbatim
(lon = eastward arc; membership ((lon-w)%360)<=((e-w)%360); sr13 bbox test
now lon_overlap arc-intersection). Unit-checked on grid arithmetic: non-wrap
identity, wrapped 20deg arc = 81 cols, pad cross-dateline exact. Synthetic
probe (scripts/review/sr_probe_wrap.py): 30.25deg wrapped twin of a live AR —
OLD min/max scans the 329.75deg complement: 563 px; NEW arc: 4525 px. 8x
undercount when alive; when dead, complement pixels of OTHER objects keep the
frame counted ALIVE -> false G2/G3 PASS, exactly the class pre-registered.
Current-numbers impact: ZERO — verified on box that no curated event is
wrapped (255f68102c43 is the §K funnel/ledger-space Bering track, not among
the 12 curated ARs) and the full gate reproduces the §R baseline exactly.
The fix matters the moment an AM event gets curated — live risk under the
relaxed landfall floor (§T).

NICE (sr17 EXTRA + dead hemis line): applied. hemis dead line deleted (only
GLOBAL_KEY excluded; hemispheres stay candidates on both sides, consistent).
EXTRA asymmetry stated as a rule: report-only note printed before GATE_JSON
(gate fires on overlap regions the centroid MISSES; EXTRA cannot fire the
flag by design — padded-bbox drift). sr13 blink records now carry explicit
"gap": 1 (true by construction, elig last_t in (t-1,t-2)) so the gate's
bridge filter is auditable instead of relying on a missing key.

## STANDING CADENCE (2026-11-10) — owner directive: weekly rerun comparison only
Owner (mid-turn): "stop this, only run once a week after ponytail triggers",
then "cron, triggered once ponytail review ends". Implemented as Hermes cron
job `ewb-score-rerun-watch` (id 5be6d92a1818), Mon 12:00 UTC — after box
weekly run (Mon 07:00, artifacts ~08:13) and after the ponytail daily round
(timer 09:15 UTC, full-sweep deadline 110 min ⇒ done ≤ ~11:05; done-marker
notes/.ponytail_done.txt). Do NOT run rerun/scorecard comparisons ad hoc in
interactive sessions; the cron lane owns that work. All §R baselines stay
pinned (events_sha 51f96b150336625a). Tooling added 2026-11-10 (review lane's
own scripts, no gate changes): scripts/review/sr18_coverage.py + baseline
notes/coverage_baseline.json (294 holes @ pinned sha; classes: integrity vs
coverage-drift) + tests/test_sr18_coverage.py — the cron's coverage check.
Box runs of sr18 are read-only pandas; verified rc=0 vs baseline.
Cadence refined same day: job now fires DAILY 12:00Z for sha detection only
(box watcher 442782 expired ~16:13Z 11-10 and nothing else watched for
off-schedule regen); full before/after comparison stays Monday-only, per the
owner's once-a-week directive. Off-day detections are logged + announced on
the channel, comparison deferred to Monday. Detection pinned as a TRIO
(2026-11-10 12:45Z, replacing events-only watch): events_selected.json
51f96b150336625a, scores.parquet 181d987f41b83c65 (e86crescore3 build),
raw_exceedance/index.json da45f6815259000d — events frozen + scores moved
= RESCORE class (the e86c incident shape); events-only detection was blind
to it. Re-pin whichever members change at each adjudicated rerun.
DETECTION UPGRADED same day ~13:45Z (owner keep-running directive): job fires
HOURLY, gated by no-LLM monitor ~/.hermes/scripts/ewb_trio_watch_monitor.py
(stdout = sha16 trio + watch-day rolling 12:00Z; box down => stable
'UNREACHABLE'). Quiet hours cost zero agent runs; wakes = sha change
(same-day detection), box unreachable (incident line, once/day), or noon
rollover (audit slot + Monday no-rerun line, deduped per-day in the prompt).
Weekly-comparison cadence UNCHANGED — deep comparison stays Monday-only.
Cron-update pitfall: a combined schedule+prompt+monitor update is silently
dropped (ok:true, fields unapplied) when monitor uses an absolute path —
use the bare filename relative to ~/.hermes/scripts/, and verify persistence
after updates.

## §V — regen-night record (2026-11-10 19:29Z → 2026-11-11 00:25Z), pre-registered before any comparison
Three full curation+score passes landed off-schedule; monitor caught each (DETECT #1-4, details in notes/score_watch_log.md). Settled trio as of 01:16Z: events 8c2366ee5440478a (n=28, since 23:51Z), scores 460df61eae76b687 (rewritten 01:07:40Z by the on-box ewb_live.weekly --days 90 job, DETECT #5 — curation untouched; viz --all followed 01:10Z), raw_index a2fe176089b217fb. Before-record UNCHANGED: §R/§U baseline @ events_sha 51f96b150336625a — G1 FAIL 4, G2 FAIL 12/19 @bridge0 / PASS @bridge3, G3 FAIL [8002c1ab3ba0], G4 PASS, sr18 294 holes (notes/coverage_baseline.json).
Monday adjudication must treat: (1) §T swap materialized in pass 2 (06ceb4d8d4fe in, 0df36ee11032+8002c1ab3ba0 out, merge e1bd4ec451c5) => a live-set G3 PASS proves nothing about the lifetime gate; isolation = §V before-record + membership-only explanation of gate diffs. (2) pass 3 moved 6 in / 7 out (verified locally from banked snapshots; the cron DETECT-4 note's '6/6, e86c back IN' was WRONG): e86c13f47bc0 (the §I RESCORE-incident event) is OUT of the live set — its short-lead CSI can only be checked on the pinned 51f9-era before-set, not on live data. (3) set snapshots banked: notes/review_artifacts/events_set_{eebf06f0,8c2366ee}.json for the structural diff. (4) AR lane arfix3 live on box (sentinels 2db5edd2/a2077a5a/33c135d5) — AR code is newer than the before-record. (5) if a 4th pass moves the trio before 12:00Z, adjudicate against then-live SHAs, re-snapshot, and re-verify stability first. (6) the weekly --days 90 rescore may have re-windowed scores.parquet (90d vs the before-record 30d): diff n_rows/n_shipped against notes/coverage_baseline.json BEFORE reading any metric or coverage delta — a window shift masquerades as regression. If scores are now 90d-window, the sr18 hole-count comparison is invalid; report window first, holes second.
Watch machinery production-proven (hourly bare-ssh monitor; zero box writes; revert hazard closed, flock guard in ops/remote.sh).

### §V addendum 2 — settled raw_index moved (2026-11-11 02:0xZ)
raw_index a2fe176089b217fb -> b464060245177dac (index.json mtime 01:46:04Z, stable across 2 box reads): the weekly-job viz --all index rewrite landing after §V's 01:16Z snapshot. Events/scores unmoved — still RESCORE-settled, no 4th curation pass. Monday adjudication trio: 8c2366ee5440478a / 460df61eae76b687 / b464060245177dac.
