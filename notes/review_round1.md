# REVIEW READY — EWB Live, round-1 review (design sign-off 2026-11-08 04:00 UTC)

Public site: **https://ewb.espr.ai** (this is the review surface; LAN URLs retired).
Layout stress-test with synthetic score cells: append `?data=mini` (amber chip
in the header means synthetic — never quote those numbers).

Status digest (verified live):
- Event set: **31 curated events** — 15 heat waves, 15 atmospheric rivers,
  1 freeze (raw 90-day candidates: 228; the catalog serves the curated,
  scored set). Window 2026-08-10 → 2026-11-08.
- Thresholds: heat ≥ p85 / freeze ≤ p15 of the OWB ERA5 1990–2020
  climatology (SPEC owner decision); ARs at EWB default IVT p90 with the
  ocean-start/landfall rule.
- Scores: 18,407 rows over 11 models, baseline IFS HRES.
- Truth: ERA5 (ARCO) through 2026-11-01 23:00 UTC, then IFS HRES 0-h
  analysis fallback; viz fields use the ERA5 mirror.
- **Visual QA 2026-11-08 (design):** AR green tokens pass in both themes;
  IVT field matches the EWB-paper Blues ramp with the 250 floor / 1000 cap
  and the `≥400` mask tick. One open defect: **map event-box click/hover/
  tooltip is inert** (Leaflet LayerGroup-vs-FeatureGroup binding bug;
  one-word fix pending on the frontend lane). Table rows and deep links
  open the detail view normally, so the review flow works.

## The five events to review (2 per type; only one freeze exists)

Deep links open the detail directly (`#event=<id>&lead=<h>`).

| # | event | deep link | why this one |
|---|-------|-----------|--------------|
| 1 | AR · Bering Strait (crosses 180°) | https://ewb.espr.ai/#event=255f68102c43&lead=24h | antimeridian-crossing AR (peak 920 kg m⁻¹ s⁻¹), one continuous frame; all 11 models scored |
| 2 | AR · Europe (Aug 26–29) | https://ewb.espr.ai/#event=3d32db128915&lead=24h | mid-latitude North-Atlantic→Europe landfall, dense land-label framing |
| 3 | Heat · US Southwest/Southern Plains (Sep 2–5) | https://ewb.espr.ai/#event=95aeafb7fd7f&lead=24h | the heat event over a US population center |
| 4 | Heat · Africa Sahel→Horn (Oct 11–22) | https://ewb.espr.ai/#event=31a871378c3e&lead=24h | longest heat run in the window (11 days), revalidated under the p85 severity-sampler fix |
| 5 | Freeze · Greenland (Oct 16–22) | https://ewb.espr.ai/#event=88bb73d7ae25&lead=24h | the only freeze that qualifies at p15 this window |

All five serve real event-viz assets (schema 3): observed field, per-model
maps at the selected lead, mask/overlap contours, colorbar with units.

## Headline numbers (real scores, as of the 02:17 UTC publish)

- **AR, CSI @ 24 h** (skill vs IFS HRES, mean over 6–8 events):
  aifs_single **+13.8 %**, aigfs +7.9 %, pangu_weather +6.5 %,
  graphcast +0.6 %, baseline 0.
- **Freeze, RMSE @ 24 h** (n=1 event — indicative only):
  pangu_weather_gfs +8.5 %, fourcastnet_v2_gfs +5.4 %, aurora_gfs +4.1 %;
  aurora −11.0 %.
- **Heat, RMSE @ 24 h**: raw model means span **2.00 °C
  (fourcastnet_v2) – 2.27 °C (aurora_gfs)** over 15 events; the
  vs-IFS skill column prints after the in-flight re-run lands the IFS
  heat rows (below).

## What changed since the review hold (2026-11-07 21:35 UTC)

- p85/p15 heat/freeze thresholds shipped end-to-end (finders, copy,
  criteria tooltips, About methodology).
- Event set re-pinned to the curated 31 (previous review ids churned —
  window rolled to Nov 8 and ids were rebuilt; old links are dead).
- Freeze lane is non-empty: one Greenland freeze (the empty-state design
  stays for future windows).
- AR green (`#2e7d32`/`#66bb6a`, AA both themes) replaced the brown accent
  across chrome; IVT fields moved from magma to the paper-matched Blues
  ramp with transparent floor (design QA above).
- Viz rebuilt post truth-cache keying fix (no fabricated-truth tiles
  possible; 31/31 assets schema 3).

## Caveats (honest states you will see)

- **Re-run in flight** (ewb lane, ETA ~17:00 UTC today): 4 AR events show
  pending score states; the two heat pins lack IFS/AIFS/AIGFS rows until it
  completes (their panels say so; their vs-IFS cells are blank, not zero).
  None of the pending ARs is in the review set.
- `ifs_hres` and `aifs_single` **viz panels** show "unavailable: build
  failed" on every event page — a source-library bug in the ewb lane
  (scores unaffected, only the two model maps). Reads as an explicit
  reason line, never a blank tile.
- `a3bb50ccde3d` (Aug 5 AR) is a documented zero: all models crash EWB's
  spatial-displacement metric on its target (upstream bug, excluded with
  evidence). Renders with — cells and no viz, by design.
- Freeze headline stats are n=1 — indicative, not conclusions.
- Basemap tiles need internet on the viewer side.

Previous-round verdicts (round-2 CLEAN: a11y/contrast/keyboard/round-3
agenda) stand; the round-3 agenda leads (dueling-map hero, verdict strip)
stay sequenced against the data timeline in `notes/design_review.md`.
