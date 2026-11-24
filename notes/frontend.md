# frontend — status (ewb/frontend owner)

Coordination: `notes/channel_design_frontend.md` (design), `notes/channel_ponytail_frontend.md`
(hourly review), `notes/channel_infra.md`. KT: `notes/kt_frontend.md`.
Handoff: `notes/handoff_frontend_final.md`. Repo rules: edit files only, no
commits; all compute via `ops/remote.sh` on the LXC; nothing infra-shaped
ever ships in served code (site is public).

## DONE

All owner-requested items are LIVE on https://ewb.espr.ai and pinned by the
Playwright suite (`pytest -m e2e`, opt-in via pyproject addopts; runs against
`EWB_LIVE_URL`):

1. Back/close/Esc navigation from event detail (+ deep link, forward,
   scroll restore) — `test_open_back_forward`, `test_open_then_close_button`,
   `test_open_then_esc`, `test_deep_link_then_back`, `test_lead_deep_link_nonfirst_lead`.
2. Single "more metrics" button (never duplicates) — `test_more_metrics_button_never_duplicates`.
3. Map-box click opens detail, back restores the map + highlight —
   `test_map_box_click_opens_detail_and_back_restores_map`.
4. Transparent fields below threshold + IVT paper colormap (Blues ramp,
   250 floor transparent, 400·mask tick) — `test_ar_detail_transparent_and_fitted`,
   `test_heat_detail_reference_component`, `test_freeze_detail_reference_component`.
5. Green AR type token in both themes — `test_ar_type_color_token_both_themes`.
6. ≤75vw centered content container (`min(75vw,1280px)` ≥1100px) —
   `test_page_width_shell_caps_content`, `test_mobile_screenshots_no_overflow`.
7. Missing-viz graceful message (distinct not-built vs failed) —
   `test_viz_404_shows_graceful_state`, `test_global_timeline_404_is_graceful`.
8. Heat/freeze single-tier maps (P85 heat / P15 freeze only) — heat/freeze
   reference-component tests above.
9. GLOBAL EVENT DASHBOARD (Equal Earth, 90-day slider, multi-select, chips,
   Gantt, URL state; design signed off 2026-11-08 07:2x) —
   `test_global_dashboard_renders_and_slides`,
   `test_global_select_and_deeplink_roundtrip`,
   `test_global_chip_filters_catalog_without_errors`,
   `test_global_timeline_404_is_graceful`.
10. OS theme flip repaints an open detail — `test_os_theme_flip_repaints_open_detail`.
11. RAW EXCEEDANCE MAP (second global view: canvas, 6-hourly frames, heat/freeze/AR
    toggles, play/speed, IFS badge, `#raw=…&layers=…`; no event boxes) — code
    live, pinned by `test_raw_renders_steps_and_badge`, `test_raw_layer_toggles_and_url`,
    `test_raw_url_roundtrip`, `test_raw_honest_without_data`. Flips to real frames
    when design's `raw_exceedance/index.json` lands (336 frames already built;
    honest "not available yet" state proven live until then).

Ponytail review rounds 04:42 → 13:31 (2026-11-08) all triaged and fixed —
full record appended in `notes/channel_ponytail_frontend.md`.

Test result: `EWB_LIVE_URL=https://ewb.espr.ai pytest -m e2e` → **20 passed**
(69 s, 2026-11-08 12:2x UTC; 24 with the raw tests, 2026-11-08 14:0x UTC, local
webroot) — the live suite now runs against the public
site end-to-end (test client sends a browser UA because the public edge
403s Python-urllib's default; the dashboard itself was never affected).

---

## 2026-11-08 20:5x UTC (successor 2) — MERGED GLOBAL MAP, post-reboot session
- `dashboard/mergedmap.js` is the one map surface now (`globalmap.js` +
  `rawmap.js` deleted): canvas raw layers + event boxes (qualified_at→end,
  day-inclusive), gantt strip, 6-hourly slider, multi-select, URL
  `#map=…&sel=…&layers=…` with one-time redirects from `#raw=` /
  `#global=&date=`; hero parks on the busiest frame; past ERA5 cutoff the
  canvas honestly empties + "raw layers end {cutoff}Z · ERA5 lag" badge while
  boxes keep animating.
- Dateline region label fixed: `regionFromViz` handles the AR-lane schema-3
  flat `obs.mask` (chunked by `grid.lon.length`), antimeridian-unwrapped
  mask centroid → the ±180° AR reads "the North Pacific", not "Russia".
- Ponytail 19:15 + 20:35 findings all fixed + deployed (qualTs ISO fold —
  pinned by an ISO-qualified_at fixture event; no null-pinned frame fetch;
  per-theme packed colours; in-flight fetch dedupe).
- Design QA round-1 blockers (AM hit-rect split, redirect shim date→map) +
  riders (axe name-content via SVG open-pill, `--map-line` coastline ink,
  badge copy) resolved; stable build announced to design 20:25.
- Tests: `pytest -m e2e -q` → **104 passed** against the deployed box
  (20:4x, 4:42); `pytest -q` unit lane 2 failed — both
  `tests/test_stall_fixes.py` heartbeat tests (pipeline lane, ewb mid-edit).
- Deployed: public https://ewb.espr.ai md5 == worktree (mergedmap
  4c188ed2…, verified 20:25). READY header waits on design's ack per
  protocol; screenshots staged in `notes/screenshots/merged_*.png`.
