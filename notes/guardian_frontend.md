# guardian-frontend — launch hardening log (2026-11-08)

Role: production-hardening of `dashboard/*.js` + serving layer. Coordination:
`notes/channel_guardians.md` (with guardian-design). Never commits; deploys with
`ops/remote.sh sync`. Raw-exceedance map (`rawmap.js`, `#raw-sec`) is excluded
until a `RAW MAP READY` header lands in `notes/channel_design_frontend.md`.

## How to run the tests (one command)

```bash
scripts/e2e.sh                              # whole Playwright suite, Chromium + Firefox + WebKit
EWB_E2E_BROWSERS=chromium scripts/e2e.sh -k axe     # subset / extra pytest args
EWB_LIVE_URL=http://127.0.0.1:8061 scripts/e2e.sh   # another deployment (e.g. a dev proxy)
```
- Engines: `.venv/bin/playwright install chromium firefox webkit` once. WebKit on a
  rootless dev container also needs distro libs: `scripts/e2e_webkit_libs.sh`
  (downloads Debian runtime libs to `.e2e-webkit-libs/`, no root; `e2e.sh` picks them up).
- Suites: `tests/e2e/test_dashboard_fe.py` (owner-feedback + global/raw tests, Chromium) and
  `tests/e2e/test_guardian_fe.py` (this audit: routing robustness, focus/keyboard,
  leaks, axe-core, serving hygiene; parametrised over all engines).
- `tests/e2e/vendor/axe.min.js` is axe-core 4.14 (MPL-2.0), used by the a11y tests.
- Plain `pytest` still excludes e2e (`addopts = -m "not e2e"`).

## Findings

Severity: B = blocker, M = major, m = minor, n = nice. Status: FIXED = fixed + regression test,
OPEN = known, DEFER = owned by someone else / blocked.

| # | Sev | Finding / repro | Fix | Test | Status |
|---|-----|-----------------|-----|------|--------|
| F1 | M | Malformed percent-escape in the URL hash (`/#event=%E0%A4%A`, truncated/pasted links) throws `URIError` out of `hashParams()` inside `boot()` → whole site shows "Could not load score data" | `safeDecode()` | `test_malformed_hash_does_not_kill_boot` (4 variants) | FIXED |
| F2 | M | Shared event links rot weekly (ids rotate). `#event=<gone>` silently showed the catalog and left the stale id in the URL; opening such a hash while another event is open left the old detail up | catalog + self-clearing status note, stale `event`/`lead` stripped from URL, detail closed | `test_unknown_event_id_falls_back…`, `test_unknown_event_via_address_bar…`, `test_route_note_clears_itself` | FIXED |
| F3 | m | `?week=garbage` / expired week stayed in the URL while current data showed | param dropped + note | `test_unknown_week_param_is_dropped_from_url` | FIXED |
| F4 | M | Focus lost to `<body>` when an event opens from the keyboard (row hidden) and never returned on Esc/close/Back | focus → `#detail-title` on open; → originating row on close; theme flips inside a detail don't steal focus | `test_focus_moves_to_detail_heading…`, `test_theme_flip_inside_detail…` | FIXED |
| F5 | m | Global map: Enter on an event / list row rebuilt the nodes and dropped keyboard focus | `focusKey()/restoreFocus()` around `render()` | `test_global_map_keyboard_toggle_keeps_focus` | FIXED |
| F6 | m | `scrollIntoView({behavior:'smooth'})` ignored `prefers-reduced-motion` | `matchMedia` check | `test_reduced_motion_scroll_is_instant` | FIXED |
| F7 | m | `saveState()` wrote `localStorage` unguarded → theme/tab handlers throw where storage is blocked | try/catch | `test_storage_blocked_does_not_break_theme_toggle` | FIXED |
| F8 | M | **serve.py**: `globalmap.js`/`rawmap.js` (not in the no-cache list) were cached 1 h → a deploy can leave a new `app.js` talking to a stale sibling (`GlobalDash`/`RawDash` API skew) | all top-level own assets `no-cache`; vendor 7 d; fonts 1 y immutable | `test_own_assets_always_revalidate` | FIXED |
| F9 | M | **serve.py**: directory listings public (`/vendor/`, `/geo/`, `/tools/`), dev script `/tools/generate_sample_data.py` served | extension allow-list, dotfile/`tools/`/`__pycache__` blocked, `list_directory` → 404 | `test_no_directory_listings_or_dev_files` | FIXED |
| F10 | M | **serve.py**: no security headers/CSP on the origin (tunnel currently bypasses nginx front) ; `Server: SimpleHTTP/0.6 Python/3.12.3` | nosniff, XFO, Referrer-Policy, Permissions-Policy, CSP (`script-src 'self' 'unsafe-inline'` — CDN edge injects an inline bot-detection snippet), `Server: ewb-live` | `test_security_headers_present`, `test_csp_does_not_break_the_app` | FIXED (verify live) |
| F11 | m | **serve.py**: duplicate `Cache-Control` on `weeks.json`/204 probes; 404s cacheable ("Error response" Python page) | single header; branded 404 page, `Cache-Control: no-store` | `test_cache_control_is_not_duplicated`, `test_404_page_is_ours_and_not_cacheable` | FIXED |
| F12 | m | `?data=mini*` → console 404 for `pair_status.json` | served as 204 like other optional probes | (covered by no-console-noise tests on live data) | FIXED |
| F13 | M | No SEO/social: no canonical, og:image/url, twitter card, preview image, robots.txt | tags (resolved from `EWB_PUBLIC_URL`, dropped if unset → nothing host-specific in repo), `og-image.png` (schematic card, no fabricated scores), `robots.txt` | `test_index_head_social_and_seo`, `test_public_tags_dropped_when_no_public_url` | FIXED |
| F14 | m | Public hygiene: tooltip/title text exposed repo path `data/results/sample/` | reworded | scrub check | FIXED |
| F15 | M | a11y (axe 4.14): `aria-selected` on listitem (critical), `role=img` with focusable children, `tr[role=link]` label ≠ visible text (33 nodes), empty `<th>`, unlabeled week picker | `aria-current`, `role=group`, name from content, sr-only th text, aria-label | `test_axe_zero_violations` (home+detail, light+dark) | FIXED (raw-sec excluded, see F20) |
| F16 | m | design asks G-16..G-26 done in JS: EarlySignal label/units/blurb (0–1 share), true minus U+2212, CI + direction in tooltip, all scored models shown by default, `kg m⁻¹ s⁻¹` in Peak column, catalog map bounded to tile extent, "mean over events" noise removed, AR footprints hatched (CVD) | see channel | `test_early_signal…`, `test_negative_numbers…`, `test_default_models…`, `test_scorecard_tooltip_has_ci…`, `test_catalog_peak…`, `test_catalog_map_is_bounded…` | FIXED |
| F17 | n | Memory/listener leak hunt: 12 open/close cycles + control churn → DOM nodes and window/document listeners flat | — | `test_no_dom_or_listener_growth…` | CLEAN |
| F18 | n | Random-walk fuzz (open/close/Esc/Back/Fwd/reload/deep-link/tabs/leads/chips/search/theme/map-box), 3 seeds × 70 steps: URL ⇄ view invariants held | — | scratch fuzzer; deterministic variant `test_rapid_open_close_back_forward…` | CLEAN |
| F19 | m | Raw map: `raw_exceedance/index.json` absent → server 204 → `r.json()` throws → console warning on every page load | — | — | DEFER (raw map; frontend) |
| F20 | m | Raw map: `#raw-prev/#raw-next` aria-label doesn't contain visible text (axe `label-content-name-mismatch`) | — | axe test excludes `#raw-sec` until READY | DEFER |
| F21 | M | Event detail shows only the observed panel for most heat events: every model panel is "unavailable: build failed" (`ifs_hres`, `aurora_gfs`, `aifs_single` …) — data/viz lane | — | — | DEFER (design/ewb) |
| F22 | B | **Raw map** (owner): horizontal scanline stripes on every fill; slab along the bottom edge under Antarctica. Cause: per-latitude-row `drawImage` strips are ~0.5 px tall, `Math.ceil` made them overlap (alpha doubled → stripes, ΔG≈25) and the single pole row was stretched full width; d3 `invert` clamps below the sphere (slab) | inverse-mapped LUT raster (device pixel → nearest ERA5-centre cell; 2 projection calls per screen row), rows outside sphere skipped, pole rows blank, lon wraps mod nx; mobile height hugs the map | `test_raw_map_cells_tile_seamlessly_and_poles_are_clean` (dpr 1/2; fails on old renderer: spread 25) | FIXED |
| F23 | B | **Antimeridian** (owner, HIGH): AR crossing ±180 rendered as a full-width band on the global map (continuous-longitude run merged into one trapezoid; `proj()` wraps each corner) | runs cut at the seam; degenerate full-band bbox (lon span ≥ 300°) recovered from footprint masks (regional grid → occupied extent, global → widest circular gap), else no box drawn anywhere (catalog, global, detail); eventviz ignores full-band boxes; Russia east of 60°E → "Asia" | `test_dateline_event_renders_sanely_on_every_surface`, `test_degenerate_full_band_bbox_is_recovered_from_footprint`, `test_unrecoverable_full_band_bbox_draws_no_band` | FIXED (ewb fixes the source convention) |
| F24 | M | boot aborted when `weeks.json` is valid JSON `null` (`null.weeks`) → "Could not load score data"; found by a 7-file × 4-corruption matrix | null-safe `loadWeeks` | `test_bad_data_file_degrades_gracefully` (28 cases) | FIXED |
| F25 | M | CLS 0.20 home / 0.4+ on phone deep links: JS-filled nav/header/gantt/map/list had no reserved size; a `#event=` link painted the whole home page first | measured min-heights, font preloads, `.deeplink` class hides home sections until the detail is up, viz area reserved while loading | CLS measured in Playwright (home ≈0.09, event ≈0.06 at 1350; Lighthouse mobile home 0 on one run) | MOSTLY FIXED (see open) |
| F26 | M | LCP chain weeks → data → timeline → land (4 deep); timeline (8 KB) queued behind `scores.json` (269 KB gz) | GlobalDash self-starts + prefetches timeline/land; weeks fetched in parallel with data | (perf) | IMPROVED |
| F27 | m | Offline / viewer-network failure read as "map asset appears incomplete / will appear once rebuilt"; archived weeks claimed "not built" | distinct network + history copy | `test_offline_map_fetch_says_network…`, `test_history_week_event_explains…` | FIXED |
| F28 | m | CSP blocked the CDN edge's analytics beacon → console violation on every public page; string `wait_for_function` needs eval | beacon hosts allow-listed (owner may disable the beacon in the CDN dashboard); tests use `evaluate` polling | all e2e on public URL | FIXED |
| F29 | m | `scores.json` 269 KB gz is still on the first-render critical path; catalog Leaflet map is not lazy; raw map decode at dpr 2 costs ~50 ms/frame (10 fps OK) | — | — | OPEN (perf, non-blocking) |
| F30 | m | (closed) Lighthouse **event page desktop CLS 0.46** (Chrome 156 "desktop" run of `/#event=…`) although Playwright measures 0.06 for the same URL | — | re-measured after the CDN served the final assets: 0.074 | CLOSED |


## Before / after (https://ewb.espr.ai, Lighthouse 13, Chromium 156)

| page / preset | perf | a11y | best-pr | SEO | LCP | CLS | notes |
|---|---|---|---|---|---|---|---|
| home desktop — before | 87 | 96 | 81 | 100 | 1.3 s | 0.205 | 11 axe issues |
| home desktop — after | **97** | **100** | 81 | **100** | 0.4 s | 0.11 | |
| home mobile — before | 79 | 96 | 81 | 100 | 5.3 s | 0 | simulated 4× CPU, slow 4G |
| home mobile — after | 71–79 | **100** | 77–81 | **100** | 4.6–5.4 s | 0–0.18 (run-to-run) | real-network FCP 0.9–1.0 s |
| event desktop — before | 72 | 100 | 73 | 100 | 1.3 s | 1.416 | home painted before detail |
| event desktop — after | **97** | **100** | 81 | **100** | 1.0 s | 0.07 | earlier 0.46 was a stale-CDN run |
| event mobile — after | 87 | **100** | 73 | **100** | 1.0 s | 0.07 | |

Best-practices ceiling: the only failing audit is `deprecations`, raised by Cloudflare's injected JS-detections snippet
(`/cdn-cgi/challenge-platform/…`: FencedFrame / StorageType.persistent) — not our code; switching "JavaScript
Detections" off in the CDN dashboard removes it. On the origin over http the same page also loses `is-on-https`.
The event pages additionally fail `image-aspect-ratio`: the field overlays are lat/lon rasters intentionally stretched
to Mercator by Leaflet.

Other metrics: payload home 738–776 KiB (scores.json 269 KB gz is 36 %); gzip on all JSON/JS/CSS; own assets `no-cache`,
vendor 7 d, fonts 1 y immutable; axe-core 4.14 = **0 violations** home+detail × light/dark × 3 engines (raw map included
after READY — see test; Lighthouse a11y 100); console clean on 31 event deep links, Chromium/Firefox/WebKit.

### Late additions (design G-43/G-10/G-11/G-12, 15:5x–16:2x UTC)
| # | Sev | Finding | Fix | Test | Status |
|---|-----|---------|-----|------|--------|
| F31 | M | **G-43 wrong headline numbers**: `mergeBinned` blended neighbouring leads (unweighted mean of means) and kept another row's CI/n — 34/90 heat hero cells >25 % off the pipeline value | exact-lead row (mean + CI + n from the same row); n-weighted fallback without CI/skill when no exact row | `test_hero_cells_are_the_exact_lead_value_with_its_own_ci`, `test_binning_falls_back_to_weighted_mean_without_ci` | FIXED |
| F32 | m | G-10 hero headline = signed duration error; G-11 global map opened on a near-empty day; G-12 detail H2 = dated data title | RMSE (heat/freeze) / CSI (AR) headline, "1 event · indicative"; busiest-day default; `Heat wave · S. Sudan` H2 | `test_hero_headline_metrics`, `test_global_map_opens_on_the_busiest_day`, `test_detail_heading_is_type_and_region` | FIXED |

## DONE

**Verdict: GO for the dashboard (frontend lane), with ONE gate outside it.**

Tests — `scripts/e2e.sh` (Playwright, 99 cases): **Chromium 99 ✔, Firefox 99 ✔, WebKit 99 ✔** against the box at the final deploy;
Chromium 99 ✔ against https://ewb.espr.ai (one timing flake on the CDN fixed in the test and re-run green). Dateline tests pass on the public URL.
No known blockers or majors in `dashboard/*.js` / `serve.py`.

Lighthouse (public, desktop): home 97 / 100 / 81 / 100, event page 97 / 100 / 81 / 100 (perf / a11y / best-practices / SEO).
Mobile preset: a11y 100, SEO 100, performance 71–87 (LCP 1.0 s on event pages, 4.6–5.4 s on the home page under
Lighthouse's simulated 4× CPU + slow-4G; real-network FCP ≈ 1 s). Best-practices 81 is capped by Cloudflare's injected
bot-detection script (two Chrome deprecation warnings from `/cdn-cgi/challenge-platform/…`) — disable "JavaScript Detections"
in the CDN to reach ≥95. Documented exceptions for the ≥95 target: best-practices (third-party script), mobile performance
(simulated throttling on a data-driven SPA; `scores.json` 269 KB gz is the remaining lever — F29).

**Gate outside my lane (blocks "scrub CLEAN"):** `scripts/public_scrub_check.sh https://ewb.espr.ai` now exits 1 because the
served `meta.json` (current + history snapshots) still contains the key `"perf": {}` that the scrub pattern forbids — ewb/infra
must drop the key from `meta.json` (pipeline) or relax the pattern. The tree-mode scan of `dashboard/` + `data/results/` is clean.

Not done (non-gating): design #15 (event-title restructure beyond the H2), G-23 Gantt/slider scale alignment, lazy-init of the
catalog Leaflet map, shrinking `scores.json`. Public CDN: Cloudflare Web Analytics beacon is allow-listed in the CSP (owner may switch it off).
