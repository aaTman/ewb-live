# PARDIG-vs-IMERG bias check (ne-fix-flood, 2026-11-19)

Owner step 1 of the PARDIG swap (briefs `fix_flood.md` UPDATE): measure the
difference before switching. Reproducible tool:
`python -m ewb_live.tools.pardig_bias_check` (run on the LXC; full JSON in
`/opt/ewb-live-dev/logs/pardig_bias.json`, 2026-11-19T07:07Z). Method: the
9 top-published flood events (+1 high-latitude) and the 6 newest global
sample days; both sources joined to the ERA5 0.25 deg land lattice (IMERG
with the lane's cnt>=48 screen, PARDIG from its daily cache); sums over the
event boxes (land only, IMERG-finite cells) and global per-latitude-band
totals + cells >= 20 mm.

Anonymous GCS reads verified working first (bucket listing 200; frames via
the public XML/Zarr endpoints, no credentials — the owner-card was not needed).

## Reach (the headline win)

| store | last complete day | typical latency |
| --- | --- | --- |
| IMERG V07 Late (dev cache) | 2026-11-16 (window start 2026-08-21) | ~2.5 h-months |
| PARDIG-NRT | 2026-11-18 (today 11-19 partial: 15/96 frames) | ~2-8 h |

95 PARDIG days cached vs 89 IMERG (window 2026-08-16..11-18 vs
2026-08-20..11-16).

PARDIG also serves days IMERG's store never had (2026-08-16..19 lookback and
the 2026-11-17..18 tail), and the flood lane finally runs at
near-real time.

## Global, by latitude band (6 sample days, 2026-11-13..18)

| band | PARDIG/IMERG volume | cells >= 20 mm |
| --- | --- | --- |
| 0-23.5 | **1.03** | 1.07 |
| 23.5-45 | **1.00** | 1.07 |
| 45-60 | **0.66** | **0.40** |
| 60-67 | **0.43** | **0.04** |
| > 67 | (excluded from this probe) | GPM never samples there; the product's own docs call those values unconstrained extrapolation |

Read: at low latitudes PARDIG and IMERG carry the same water (and PARDIG
flags ~7 % more >=20 mm cells — its 0.1 deg AI field is dense, so a light
areal estimate rarely cancels to NaN). Mid-latitudes in this season PARDIG is
1/3 drier; subpolar sites it calls heavy-rain days almost never. Sample is
November troughs in the northern band — seasonal, not a constant — but the
pattern direction matches the product description (dense-inference light rain
in the tropics, conservative outside the radar-training domain).

## Event boxes (volume ratio PARDIG/IMERG, land & IMERG-finite cells)

| event | lat | days | ratio |
| --- | --- | --- | --- |
| Cuba 11-12..15 | 21 | 4 | **0.26** |
| Vietnam/Myanmar 09-25..29 | 14 | 5 | 0.84 |
| China/Vietnam 08-21..25 | 21 | 5 | 0.65 |
| India/Bangladesh 09-21..26 | 21 | 6 | 0.84 |
| China/Japan 08-29..09-05 | 27 | 8 | 0.79 |
| Mexico 10-02..08 | 17 | 7 | 0.49 |
| Argentina 09-27..10-01 | -29 | 5 | **1.03** |
| Myanmar/SE Asia 11-04..12 | 18 | 9 | 0.57 |
| Wisconsin/US 11-03..06 | 49 | 4 | 0.63 |

Spatial pattern: PARDIG tracks the IMERG event *shape* day-by-day (correlated
peaks in every probe; e.g. Argentina 09-28: 34.4 vs 34.1 billion m3), but the
intensity is systematically lower, typically 0.5-0.85x, and the two
mid-latitude cases (Cuba 0.26 — an IMERG-heavy-histogram outlier event;
Wisconsin 0.63) drop hardest. IMERG's own bias versus the ERA5 climatology is
1.6-2.6x high (audit_flood.md F1); PARDIG sitting nearer the ERA5-matched
distribution at low latitudes is the direction this lane needed.

## Consequences for the lane (accepted by the owner, restated)

1. **Fewer/smaller detections.** With the ERA5-p95 rule UNCHANGED (owner
   decision), event counts and peaks drop where PARDIG is drier — most
   strongly beyond ~45 deg N/S. This moves the lane toward the "5 % of wet
   days" ideal the audit said IMERG overshot ~2x; it is not an error, but the
   before/after counts on :8051 will show a visibly smaller flood set.
2. **High latitudes thin out** (60-67 ge20 ratio 0.04): beyond the GPM
   training domain neither product is observational truth; PARDIG says so in
   its own docs. Floods like the earlier "Flood - Russia" events will
   likely stop qualifying.
3. **No unobserved-NaN semantics:** PARDIG estimates every cell; IMERG's
   "NaN = no observation" honesty is replaced by the day-completeness rule
   (a day with ANY missing 15-min frame serves nothing) and the land-only
   ERA5 threshold lattice.
4. **Latency:** floods appear on the map within hours of happening, and the
   map no longer shows a multi-month observation cliff.
5. **Rate cap:** 15-min rates cap at 75 mm/h — sub-hourly extremes clip at
   18.75 mm/step; daily totals (the lane's variable) are only touched in
   the heaviest convection.
6. Provenance/UI: the lane is labelled **AI-estimated precipitation (Google
   PARDIG, CC BY 4.0)** everywhere, events carry `data_source.kind`, and
   IMERG remains one env var (`EWB_FLOOD_SOURCE=imerg`) away until the owner
   approves removal.
