# guardian-design — launch-polish audit (EWB Live)
Started 2026-11-08 ~13:45 UTC. Role: principal product designer / dataviz QA. Never git commit. Compute only on the box.
Off-limits until `RAW MAP READY` (channel_design_frontend.md header): raw-exceedance map (#raw-sec, rawmap.js, raw_exceedance.py + data).
Screenshots: notes/screenshots/gd-*.png (before = `gd-before-*`, after = `gd-after-*`). Channel: notes/channel_guardians.md.
Scratch harness (not in repo): Playwright against the live site; `GD_LOCAL=1` routes dashboard files from the worktree for pre-deploy checks.

Severity: Blocker = cannot launch; Major = visible defect/wrong content on a core view; Minor = polish.
Owner column: D = design (me), F = guardian-frontend (JS), E = ewb (data), I = infra.

## Findings
| ID | Sev | View | Issue | Fix | Owner | Status |
|----|-----|------|-------|-----|-------|--------|
| G-01 | Major | Event pages (all) | IFS HRES baseline + aifs_single maps missing: viz cells `unavailable (build failed)` 92/94 (built during s3fs/aiobotocore bug) | Rebuild all event viz via direct driver (no raw tail), serial, MemoryMax 12G | D | rebuild running (heat done, AR pending) |
| G-02 | Major | Home / global | Time row grid `auto 1fr auto` vs DOM play·date·slider → slider squeezed right | CSS scoped to #global-sec | D | fixed + deployed |
| G-03 | Major | Event detail | Per-cell 1px dark stroke → hatch/moiré over every exceedance region (ref = flat fill + region outline) | eventviz.js tierDataURL: fill + boundary-only outline | D (patched JS, told F) | fixed + deployed |
| G-04 | Major | Event detail | Single 540px column centered, toolbar flush-left → ragged, 2.7k px page | CSS: `.ev3-root` 2-col grid (obs + first model side by side), unscored events obs-only 640px | D | fixed + deployed |
| G-05 | Major | About | Stale copy; no AR rule, limitations, credits/licences, metric directions; `data/results/` path | Rewrite About (index.html) from SPEC + science_review | D | fixed + deployed (CI sentence held until tooltip shows CI, G-16) |
| G-06 | Major | Event detail | Region chip wrong: Bering Strait AR → "Russia, Europe" | gazetteer continent (RU lon>60E ⇒ Asia) | F | open (ask #2) |
| G-07 | Minor | Scorecard | Baseline row used orange focus-like ring | muted left key-line + "baseline" tag | D | fixed + deployed |
| G-08 | Minor | Footer | Left edge outside the shell | max-width var(--shell-max) | D | fixed + deployed |
| G-09 | Minor | Tokens | `--ink`, `--accent-soft` undefined | defined (aliases) | D | fixed + deployed |
| G-10 | Major | Home scorecard | Heat/freeze headline = signed DurationMeanError "event" column, no lead columns; freeze n=1 not flagged | headline RMSE by lead; "1 event — indicative" | F | CLOSED 15:25 (RMSE hero, verified live) |
| G-11 | Minor | Global map | Default date (Oct 29) shows 1 AR → empty-looking hero; list row title truncates / dates wrapped | default busiest day (F); list-row grid layout (D, fixed) | F/D | CLOSED 15:25 (busiest day, Sep 30, 4 events) |
| G-12 | Minor | Detail/catalog/global | Titles are "Heat Wave 2026-09-02 to 2026-09-04" (dates repeated) | `Heat wave · <region>` | F | CLOSED 15:25 (H2 "Heat wave · Sudan") |
| G-13 | Minor | AR detail | IVT field hard-clipped at bbox+2° (visible rectangle edge); obs panel label lacks truth ("Observed IVT") | viz: pad AR window + recompute mask (risk: mask edge effects) — post-launch | D | deferred |
| G-14 | Minor | Catalog | Table clipped at 1440/390 (Days/Peak off-canvas, End hidden) | hide Start/End (title carries dates), title wraps | D | fixed + deployed |
| G-15 | Minor | Header | `title` attrs mention `data/results/` | plain-word titles | D | fixed + deployed |
| G-16 | Major | Scorecard tooltip | Tooltip lacks 95% CI (data has it) and higher/lower-is-better | add rows | F | open (ask #8) |
| G-17 | Minor | Controls | Default model filter 6/11 hid half the roster | default all | F | fixed by F (11/11 seen) |
| G-18 | Major | Scorecard + charts | EarlySignal header `[h]` & blurb "lead time" — values are 0–1 share of events | label/units/blurb | F | open (ask #7) |
| G-19 | Minor | About | Per-chip `IFS/GFS FORCING` tags (jargon; aigfs mis-tagged) | plain chips | F | open (ask #9) |
| G-20 | Minor | Units | `kg/m/s` vs `kg m⁻¹ s⁻¹`; hyphen vs U+2212 minus | unify | F | open (ask #11) |
| G-21 | Minor | Catalog map | Empty strip above 85°N; Leaflet attribution/zoom light-on-dark glare | maxBounds (F); CSS theme chrome (D, fixed) | F/D | partly fixed |
| G-22 | Minor | Global Gantt | Scale not aligned to slider; no date cursor | align + cursor | F | open (ask #13) |
| G-23 | Major | A11y | axe: aria-selected on listitem (critical), role=img w/ focusables, 33× label-content-name-mismatch, dark chip contrast 2.4–2.8:1, unlabeled week picker, empty th | HTML/CSS fixed (D); JS (F) | D/F | partly fixed (asks #16–19) |
| G-24 | Minor | Event pages | `_gfs` model panels `unavailable (build failed)` where the GFS-forced archive has no run near the date (archive holes; s3fs reports it as TypeError) | `_reason()` → "no archived run near this date" | D | fixed in code (next rebuild/weekly) |
| G-25 | Minor | Global map | Heat red vs AR green ΔE 11 (deutan) / 17 (protan) | AR footprint hatch | F | done by F (hatched swatch seen) — verify map |
| G-26 | Minor | AR panels | Event-box dashed outline and forecast contour both `--accent` | bbox → ink-secondary | D (patched JS) | fixed + deployed |
| G-27 | Minor | Story/catalog | `peak 20.9 °C` on a heat wave = ERA5 daily-mean peak | label/title | F | open (ask #25) |
| G-28 | Major | AR story | "4 landfall days" on a 3-day event (landfall_days > duration on 11/15 ARs) | data fix (E); UI: print landfall date | E/F | open |
| G-29 | Major | Detail Scores fold | First Plotly chart 1006px in a 478px card (overlap) until a resize | resize after layout | F | open (ask #22) |
| G-30 | Major | n/a reasons | 3 pair_status reasons are raw exception text | normalise (E) + UI generic fallback (F) | E/F | open |
| G-31 | Major | Hygiene | meta.json (public) carries `perf.peak_total_rss_gb` = box spec | drop from served meta | E | CLOSED 15:35 (perf gone from public meta.json) |
| G-32 | Minor | Head | No favicon/description/OG/theme-color for a public launch | added favicon.svg + meta | D | fixed + deployed |
| G-33 | Minor | Event maps | Name chip covers the field centre; integer zoom fills only 25–40% of panel | anchor chip at bbox NW; zoomSnap .25 | F | open (asks #26/#27) |
| G-34 | Major | Scores (data) | 3/15 AR events (a3bb50ccde3d, 1ff0485667cc, 0c7a7f10f667) have CSI = EarlySignal = 0.0 for all 11 models at all leads → biases AR means, event pages show 0.00 columns | rescore or mark excluded | E | reported 14:25 |
| G-35 | Minor | Loading | Half-built chrome (empty filters, blank scorecard) while data loads | hide controls until populated; scorecard shimmer | D | fixed + deployed |
| G-36 | Minor | Fonts | OWB uses licensed Matter/MatterMono; we ship Inter + IBM Plex Mono (OFL) | accepted substitute, noted in About credits | D | closed |
| G-37 | Major | Event story vs map | Merged events: story "4d max run" vs map badge "max 11d" (31a871378c3e), 4 vs 7 (e4cc82710996), 3 vs 4 (88bb73d7ae25) — details.max_consecutive_days not recomputed on merge | E fix select.py; F: story uses viz.obs.max_consec | E/F | reported 14:30 |
| G-38 | Major | Scorecard heat (data) | Peak-max MAE at leads 246/252 h ≈ 10 K for every model (≈2 K at 228 h) — verification artefact shown in "10d" column + 11 K spike in event Scores chart | drop rows >240 h or fix alignment (E); UI: bin ≤240 h (F) | E/F | reported 14:50 |
| G-39 | Minor | Public site | New CSP blocks Cloudflare's injected beacon.min.js → console error on every public load; 7 e2e tests fail on the public origin | turn the beacon off in Cloudflare (or allow-list it) | F/I | reported 14:25 |
| G-40 | Major | AR event 0c7a7f10f667 (science) | Observed IVT field is a closed vortex with a calm core = tropical/post-tropical cyclone, not an AR; B8 TC rule (IVT ≥1000) missed it (peak 992); also an all-zero-CSI event | drop / tighten TC rule | E | reported 14:50 |
| G-41 | Major | Mobile home | Page scrolled sideways (scrollWidth 568 at 390/360): `#gm-map-wrap` pushed out by a `1fr` single-column grid + SVG intrinsic width (regression after the global-map JS update) | `minmax(0,1fr)` + min-width 0 on gm grid/wrap ≤979px | D | fixed + deployed |
| G-42 | Major | Raw map | Striping (fixed by F in worktree), pole row band = data (rows lat ≤ −89.5 fully set), light theme has no ocean/land contrast, slider default blue, a11y labels −6h/+6h | pole rows skipped in renderer; ocean tint; slider CSS (D done); aria-label (F) | F/D | partly fixed; ask #31 |
| G-43 | Major | Scorecard (all lead cells) | `mergeBinned` blends neighbouring leads with an unweighted mean (and first row's CI/n): 34/90 heat hero cells differ >25% from the pipeline value at that lead (ifs heat Lowest-max MAE 1d shows 0.94, true 24 h 0.157); tooltip mean/CI come from different rows | use exact-lead row, else n-weighted mean w/o CI | F | CLOSED 15:25 (exact-lead rows; ifs heat RMSE 2.33/2.42/2.52/2.71/2.85 = scorecard.json; Lowest-max MAE 0.16/0.28/0.77/0.91) |

## Visually verified event pages (31/31, live, 1440 light; dark/390 spot checks on heat, freeze, AR)
Heat (15): 35ab174ce7d5 4b6147e617a0(+dark) 6deee6471cea 56caf7420b9a 5481835ecdc5 95aeafb7fd7f(+390,+scores) 0a65373050e2 c736a266a826 9210c96bc91d e4cc82710996 32d04a30d801 f2fdddcd92fe 7e2b955f8f88 31a871378c3e(+scores) 511f7c22604d — Freeze (1): 88bb73d7ae25(+390) — AR (15): a3bb50ccde3d(+390) d6309bf334d8 3d32db128915(+scores) 0e530ab9eebf 97c52bf337b5 255f68102c43(dateline) 82dc9c1b533c bebcc8d36395 1ff0485667cc 0c7a7f10f667 029719d1e741 0df36ee11032 4a9c67b01f25 3935b8c53d9d e1bd4ec451c5. After viz rebuild every page has the IFS HRES panel (ifs/aifs 85/94 cells ok; remaining gaps = beyond-horizon, GFS-archive holes, aigfs no t2m, fourcastnet no q — all rendered as explicit reason lines).

## DONE (interim verdict, 2026-11-08 ~15:20 UTC — launch-readiness)
**Verdict: CONDITIONAL GO — not yet "zero majors".** The product is visually launch-grade (layout, dark/light, mobile, a11y: axe-core 0 violations on home/about/event pages in both themes (verified live), e2e 25/25 on the public URL, public scrub CLEAN, About complete, 31/31 event pages verified). What blocks an unconditional launch is **data/number correctness owned by other lanes**, listed in priority order — the first five are what a scientist reviewer will find in five minutes:
1. **G-43 (F, MAJOR)** hero scorecard cells are not the value at that lead (`mergeBinned` blends neighbouring leads; 34/90 heat cells >25% off; tooltip mean/CI from different rows). Fix specified in channel (ask #30). *Open at 15:20.*
2. **G-34 (E, MAJOR)** 3/15 AR events have CSI = EarlySignal = 0 for all models (a3bb50ccde3d, 1ff0485667cc, 0c7a7f10f667) although the viz overlap shows hit ≈ 900+ — scoring artefact biasing every AR mean. Rescore or exclude.
3. **G-38 (E/F, MAJOR)** heat Peak-max MAE at leads 246/252 h ≈ 10 K for every model; UI now drops >240 h rows (frontend, verified in code) — data should still be fixed.
4. **G-40 (E, MAJOR)** 0c7a7f10f667 is a tropical/post-tropical cyclone (closed IVT ring with calm core), not an AR.
5. **G-31 (E, MAJOR hygiene)** public meta.json still carries `perf.peak_total_rss_gb` (box memory) — SPEC "Public site hygiene" (scrub pattern doesn't catch it; verified still served at 15:15).
6. **G-10/G-11/G-12 (F)** heat/freeze hero headline = signed Duration error only (should be RMSE by lead), global map opens on a 1-event day, detail H2 still the data title (exact patches posted, ask #3–#5; region continent fixed ✓).
7. **G-37 (E)** merged events' `max_consecutive_days` — UI now shows viz value (verified "11d max run"), data still wrong; **G-30 (E)** 3 pair_status reasons are raw exception text (UI hides? verify), landfall_days > duration (UI prints date instead ✓).
Everything in my lane is closed: G-01–G-05, G-07–G-09, G-14, G-15, G-21 (CSS), G-23 (HTML/CSS), G-24, G-26, G-32, G-35, G-41 (mobile overflow regression) — see table. Frontend closed: asks #7–#14, #16–#18, #20, #22, #23, #25 (hatched AR), #26–#29, region continent, dateline tests; verified live today.
**Post-launch polish ideas:** (a) dueling-map hero with ONE shared colourbar and model switcher; (b) pad the AR viz window ≥6° beyond the bbox so IVT fields don't end in a visible rectangle (needs mask recomputed on the larger window); (c) polar projection or capped latitude for the Greenland freeze (0.25° cells smear in Mercator); (d) Gantt scale aligned to the slider + selected-day cursor; (e) replace Plotly (4.5 MB) with inline SVG small multiples; (f) 12h-offset leads: show only leads with n ≥ 3 in charts; (g) real fonts parity (Matter/MatterMono are licensed; Inter + Plex Mono are the OFL substitutes); (h) og-image/CDN caching/Esri key (TOU) before heavy traffic; (i) raw map ocean/land fill via `--map-ocean` (ask #31) and pole-row skip; (j) per-event "why this event" one-liner from the science review corroboration notes.

## DONE (final re-QA, 2026-11-08 ~15:30 UTC)
**Verdict: GO for the dashboard (design + front-end). Data caveats below must be either fixed by ewb or accepted before the public announcement.**
Re-verified live on https://ewb.espr.ai after frontend's latest deploy: G-43 binning (hero cells == scorecard.json rows at 24/72/120/168/240 h), G-10 RMSE headline (heat/freeze) / CSI (AR), G-11 default date = busiest day (Sep 30, 4 events), G-12 H2 "Type · Country". axe-core 0 violations (home/about/event × light/dark/390), e2e 99 passed on the public URL, `public_scrub_check.sh https://ewb.espr.ai` CLEAN, antimeridian PASS, 31/31 event pages verified.
**Open — owned by ewb (data), not blockers for the UI:** G-31 `perf.peak_total_rss_gb` STILL served in /data/results/meta.json (SPEC "Public site hygiene" violation, one-line fix: drop `perf` from the served meta — recommend doing this before announcing); G-34 three all-zero-CSI ARs; G-40 tropical cyclone in the AR set; G-37 merged-event max_consecutive_days; G-30 raw exception text in pair_status; G-38 246/252 h artefact (UI already drops >240 h).
**Open — minor (mine/frontend):** ask #31 raw map in LIGHT theme: ocean and land both white (only the coast hairline separates them; dark is fine) — fill sphere with `--map-ocean`. AR near the dateline shows region "Russia" (fine; "Bering Strait region" would be nicer).
**Post-launch polish:** unchanged from the interim list above.
**Update 15:35 UTC:** G-31 data leak gone (peak_total_rss etc. no longer served) BUT the new scrub pattern `"perf":` flags the leftover empty `"perf": {}` key in meta.json (and weeks entries) -> `public_scrub_check.sh https://ewb.espr.ai` exits 1. Harmless content, but the scrub must be CLEAN before launch: ewb to drop the key (or relax the pattern). Re-run the scrub after their fix.
**Update 15:55 UTC — ask #31 CLOSED (design lane patched rawmap.js/index.html, deployed):** raw map sphere now fills with `--map-ocean` (land stays `--paper`), canvas repaints on theme flip (MutationObserver on data-theme), AR legend chip tooltip = "AR mask (EWB, before cyclone screen)…", About now says the raw map is the unfiltered upstream mask and that tropical cyclones are screened with IBTrACS in the event list. Verified live light/dark/390, axe 0, raw e2e 8 passed. Regression screenshots: notes/screenshots/gd-after-raw-{light,dark}-1440.png, gd-after-raw-light-390.png. No automated pixel test added (post-launch: assert ocean pixel != land pixel in light theme).
**Still open at hand-off:** scrub exits 1 only because of the empty `"perf": {}` key (ewb to drop it); data items G-34/G-37/G-30/G-38 (ewb). G-40 is covered by the owner decision (IBTrACS screen for the event list).
