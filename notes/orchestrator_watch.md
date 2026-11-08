# Orchestrator watch: active agents and their acceptance criteria

| Agent | Handle | Done when |
|---|---|---|
| ewb (successor 5) | term_e87b37a0-5838-4c60-bebd-7910c2abb99d | Stall postmortem (notes/postmortem_stall_20261107.md) + fixes; heat p85/freeze p15; FCN RH->q; B2 mirror fix; ARs must start over ocean and end on land (all-land rejected, tests); Freezes fixed (funnel documented); same-event-only merging with duration cap; zero time+space overlaps; up to 15/type; list posted to design; full 90-day scoring for all models on the box (scores.parquet, scorecard.json, meta.json with climatology and weekly fields); ewb_live.weekly end-to-end on the box; real `## DONE` in notes/ewb.md |
| design (successor 6) | term_f3c16ead-1e8d-4d15-bc73-1d93f45ea9e9 | p85/p15 copy, empty states, refreshed review set from the final list, real headline numbers; Simplification plan written + executed (OWB-like sparse UI, ~70% less copy, before/after screenshots, deployed); eventviz v3 + OWB restyle live on :8050; 6 review events (2/type) from the final list, built on the box; notes/review_round1.md with 'REVIEW READY', 6 deep links + screenshots + questions; then waits for owner feedback |
| mlwp-store (successor 2) | term_85cad75b-c61d-4e84-a9a9-20e61e5e2509 | Full-archive virtual store (no rolling window), daily 05:30 update with lock/watchdog/GC, edge-chunk guard, sources verified 2021 + yesterday, `## DONE (successor 2)`  **DONE 20:51 (verified)** |

| frontend (successor 2) | term_55602a39-edbc-42a8-8310-703b3f5e6d2a | map box click opens event detail (same as list) +; A1-A5 (back navigation, duplicate 'more metrics', transparent field, framing, model selector) + B (heat/freeze tier maps per owner reference) live on :8050 with e2e tests; '## DONE' in notes/frontend.md |
| ponytail-setup | term_5f83df4f-8c49-4397-8667-b7770dcf2a62 | Ponytail skill installed for Hermes; hourly systemd review timer live; review brief written; first run done; owners nudged; '## DONE' in notes/ponytail.md  **DONE 00:5x (verified: 2 full sweeps, 5 findings, owners nudged+fixing)** |
| global-dashboard (design+frontend) | see notes/briefs/global_dashboard.md | Equal Earth global map of all 90-day events at the top of the home page, time slider, multi-select, URL state, tests; GLOBAL DASHBOARD READY posted |
| raw-exceedance-map (design+frontend+ewb) | see notes/briefs/raw_exceedance_map.md | second Equal Earth map, raw per-6h exceedance (heat/freeze/AR overlaid, toggles), no boxes, slider, tests; RAW MAP READY posted; gates guardians |
| merged-global-map (frontend+design+ewb, Hermes) | see notes/briefs/merged_global_map.md | one Equal Earth map: 6-hourly raw layers + slider + event boxes from qualified_at to end; MERGED MAP READY posted |
| infra-domain (successor 2) | term_987fee64-7d9d-40a0-ac39-4a5d70147c29 | https://ewb.espr.ai live with valid TLS (or OWNER ACTION NEEDED checklist posted), proxy as a systemd unit on the box, documented, '## DONE' in notes/infra_domain.md |

Done (no watch): sources, infra, ops, legacy-review, portability (phase 1).

## Pending reviewers (owner request): launch when the trigger is met
| Reviewer | Brief | Trigger |
|---|---|---|
| design-review (principal UI designer) | notes/briefs/review_design.md | DONE (round 2 clean, 2026-11-07) |
| science-review (principal scientist) | notes/briefs/review_science.md | ACTIVE (successor 3, post-reboot, G-34 re-review) → term_bd4274e6-0142-48b1-8cb2-9871d237e93e; done when section H verdict in notes/science_review.md |
Launch with: orca agent launch --agent hermes --surface terminal --prompt "$(cat notes/briefs/<brief>)" --json, then add the handle to the table above.

_Updated 2026-11-07 18:2x UTC after the Orca restart: all sessions relaunched as fresh successors._

## Guardians (owner request 2026-11-07): launch when frontend AND design have posted 'KT READY' (notes/kt_frontend.md, notes/kt_design.md, notes/handoff_frontend_final.md, notes/handoff_design_final.md exist)
Launch each with: orca agent launch --agent claude --model claude-sonnet-5-5 --effort high --surface terminal --prompt "$(cat notes/briefs/guardian_<role>.md)" --json
| Guardian | Brief | Handle |
|---|---|---|
| guardian-frontend | notes/briefs/guardian_frontend.md | term_3d6c7a6f-f1de-4131-9a7c-323bd5f49b7d (13:44) |
| guardian-design | notes/briefs/guardian_design.md | successor term_c688767f-5958-430c-b9ee-965c33555e2e (15:25; first term_db51e997) |

## science-review (successor 2) — state @ 2026-11-08 02:2x UTC
External cross-ref CLOSED (review_external_p85.json, 6/8 CONFIRMED, reviewer-verified; C11). Phase B first pass DONE (science_review.md §D): PASS on pair-math/merge/metrics/B2/clim-prov/look-ahead; blockers D1 (heat lane has NO IFS rows — 35 stale terminal pairs, reschedule requested in channel), D2 (AR skill not like-for-like), D3 (no bootstrap CIs). Verdict DRAFT pending: ewb rescore (running now), then sr09 value-level repro + D4 lead-anomaly check on BOX_IDLE (watcher proc_33842e022dc3).

| design (successor 5) | runtimeId 592c949a-784b-4d62-8399-8a26e01592b7 | launched 2026-11-08 03:15 UTC by design#4 at context limit; brief = notes/handoff_design_4.md |

## science-review | 06:1x UTC — FINAL verdict written: CONDITIONAL PASS, ## DONE in science_review.md §E
Correction on the 05:36 directive: the 04:49 run did NOT include the D1 rescore (verified 05:36: 0 heat×IFS rows, aiobotocore still 3.10.0). sr09 value-level repro PASS (136/136 rows, max diff 2.4e-7). D4 closed benign. Open for ewb/owner: D1 (pin s3fs/aiobotocore + force-reschedule 35 pairs + completeness must treat read-error no_data as blockers), D2 (1083/1859 AR skill rows not like-for-like), D3 (no bootstrap CIs). About-page limitations cross-posted to channel_design_frontend.md. Watcher proc_556e7fa8a3ce will notify when heat×IFS rows appear post-fix.

## science-review | 12:4x UTC — FINAL: PASS. science_review.md §F + ## DONE. D1-D3 verified on 09:22Z run; new D6 (duration-skill sign semantics, ewb) + residual holes (16 aigfs no-2m, 7 aigfs AR no-data, 2 fcn_v2 valid_time). About-page updates posted to channel_design_frontend.md. Watcher proc_556e7fa8a3ce retired (D1 moot). Review closed.

## [design -> ewb] 2026-11-08 raw_exceedance asset (brief raw_exceedance_map.md)
Building data/results/raw_exceedance/ (6-hourly raw detector frames for the
new global map). REUSES your functions, no re-implementation: heat/freeze =
frame-level operators of upstream build_exceedance_mask (p85/p15 OWB clim,
land mask via your build_land_mask), minus only the daily min-over-4-steps
pass (the map shows per-step output); AR = EWB
build_atmospheric_river_mask_and_land_intersection exactly as ar.py calls
it. Source = your local ERA5 mirror (data/cache/truth/era5.zarr — levels
match the AR set exactly), so the asset ends at your mirror end (Nov 1 18Z
now) and rolls forward as the mirror's 05:30 timer copies new steps; index
carries the cutoff explicitly. If you change mirror layout/vars or the AR
builder call convention, ping me (ewb_live/viz/raw_exceedance.py imports
them). Hooked into the viz CLI tail (incremental). /design

## [design -> ewb] 2026-11-08 merged-map qualified_at (briefs/merged_global_map.md §4)
Please add `qualified_at` (ISO 6-hourly, e.g. "2026-10-28T06:00:00") per
event in events_selected.json from the SAME detection logic, and document
the definition in SPEC §2. Suggested semantics (your call, you own the
detectors): heat/freeze — first step of the first qualifying day of the
consecutive run (the run's day-1 AND-pass starts at 00Z that day); AR —
first 6-hourly step where IVT>=400 & Laplacian & land-intersection rules
all hold (the landfall step). timeline.py passes it through to
global_timeline.json as `qualified_at` verbatim; if absent I emit
`start` truncated to the hour with `qualified_at_source:"start_fallback"`
so the map never blocks on you. Ping when it lands and I'll rebuild the
asset (one CLI call, incremental).

_2026-11-08 18:39 UTC: host reboot; ewb/frontend/design relaunched as Hermes successors (handles above). Guardians finished (GO); science review closed (PASS)._
