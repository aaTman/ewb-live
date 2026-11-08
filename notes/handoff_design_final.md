# Handoff — design (final, successor 5) — 2026-11-08 ~07:50 UTC

Companion doc: **notes/kt_design.md** (full knowledge transfer — current as
of this morning; read SPEC.md first: everything runs on the `ewb` LXC via
`ops/remote.sh`, never `git commit`, successors keep the role name 'design').
Ownership: frontend owns `dashboard/*.js`; design owns CSS/tokens/layout
specs/copy/visual-QA + `ewb_live/viz/`. Team channels: `notes/channel_*.md`
(append-only); ponytail triage: `notes/channel_ponytail_design.md` (every
finding so far applied and answered).

## Signed off live at https://ewb.espr.ai (probed this morning)

1. **Green AR token** — `--series-ar` #2e7d32 light / #66bb6a dark; chips,
   dots, map strokes verified by computed-style probes (5.13:1 / 6.40:1).
2. **IVT paper colormap with transparent minimum** — sequential Blues
   #dbe9f6→#08306b, domain 250–1000 kg m⁻¹ s⁻¹, alpha 0 below 250; colorbar
   first pixel rgb(219,233,246) probed live today; 400 mask tick marked.
3. **≤75vw container** — `--shell-max: min(75vw, 1280px)` centered at
   ≥1100px (measured 960px content @1280vw), full-width + gutters below;
   no horizontal overflow at 390px.
4. **About page** — zero hits served for public-URL/LAN-IP/host (worktree
   matches live; the only served delta is Cloudflare's edge script). Science
   complete: P85/P15 OWB climo thresholds, 6-hourly land-only ≥3-day rule,
   truth-fallback (mixed references) caveat, sign-normalised metrics, n
   events, forcing sources. Standing rule: About never carries URLs/hosting.
5. **Heat/freeze single-tier maps** — one exceedance class, one color,
   crisp cells, rest transparent (owner ruling, viz_v3_spec.md; live).
6. **Global event dashboard** — `#global-sec` is main's first child; Equal
   Earth vector map; day slider + play; 3-lane Gantt; legend/list counts
   track the selected date; multi-select via `#date=…&global=id,id`; open →
   detail, Back restores dashboard state (verified); honest empty-day
   caption; 31/31 events carry footprints in `global_timeline.json` (31 KB,
   schema 1, auto-rebuilt by the viz/weekly tail step). Evidence:
   `notes/screenshots/gd-desktop-1440.png`, `gd-mobile-390.png`.

Fixed + verified this session: catalog event-box click (`L.featureGroup`,
app.js:1297), TYPE_CRITERIA/About p85→p15 copy, build.py bbox/thr_daily arg
swap, truth-cache bbox keying (+ full viz rebuild unit `ewb-viz-truthfix`),
dead CSS removal. Offline suite **144 passed** (adds
tests/test_global_timeline.py).

## Open items, ranked

1. **ewb re-run in flight** (`full_run_complete` still false at last check).
   When it lands: refresh `notes/review_round1.md` (headline numbers, deep
   links to the then-current curated set, 2-per-type review shots), flip
   `REVIEW READY`. 6 heat-lane pairs (ifs_hres/aifs_single/aigfs × 2 pins)
   awaited from ewb.
2. AR per-day footprints on the global map (timeline repeats the anchor
   mask — documented shortcut; upgrade `build_ar` to store daily masks).
3. Stale bbox-blind zarrs in box `data/cache/truth/` — inert (exact-key
   lookup now); purge is ewb's lane.
4. Round-2 backlog (dueling-map hero, verdict strip) — notes/design_review.md.
5. Public-launch gates: Esri ArcGIS key (TOU) + CDN/caching in front of
   serve.py (in-process gzip cache).

## Recipes

Edit worktree → `./ops/remote.sh sync` (code only) → live at :8050 and
ewb.espr.ai (own assets no-cache). Viz rebuilds / scoring: box only,
systemd-run units (kt_design.md §10). QA: browser_exec probes (proxy for
tiles); `window.__debug.state`, `window.__ev3`. Screenshots: copy from the
shared tmp path IMMEDIATELY after capture_screenshot. No git commits.

## RAW EXCEEDANCE MAP — shipped 2026-11-08 (later than the KT block above)

Second global view `#raw-sec` live + design-QA'd on the public site
(frontend posts 'RAW MAP READY', channel 14:55). Asset:
`data/results/raw_exceedance/` — 336 frames 2026081000→2026110118,
zlib per-row packbits ×3 planes (display orientation), `index.json`
cutoff = ERA5 mirror end (Nov 1 18Z; Nov 2-8 legitimately frameless —
labeled). Builder `ewb_live/viz/exceedance.py` (renamed mid-session),
weekly tail hook in `__main__.py`, serve.py strict `.bin` route. KT §2 is
current. Ponytail 14:31 findings all fixed (timeline date-only fallback
days; per-row stride). CORRECTION 17:40: the d3-array tag is NOT dead —
d3-geo UMD consumes d3.Adder from it; tag stays (guard comment added),
my earlier removal never shipped.

## MERGED GLOBAL MAP — successor 6 session (2026-11-08 evening)

Brief `notes/briefs/merged_global_map.md`. design lane complete:
- **Asset**: `global_timeline.json` rebuilt on the box 18:40 — schema 2,
  all 31 events `q_source:"detection"` (ewb's `qualified_at` landed in
  events_selected.json 17:35); header mirrors raw_exceedance/index.json.
  Rebuild = `./ops/remote.sh run python -m ewb_live.viz.timeline` (seconds,
  box-side; no systemd unit needed — it's cheap and atomic).
- **Raw frames confirmed current**: 336 frames 2026081000→2026110118,
  cutoff = last frame = window end (gray-tick case unreachable until the
  pipeline window outgrows the ERA5 mirror).
- **QA round 1 posted** channel 18:55: pass list + 2 blockers for frontend
  (antimeridian .gm-hit spans the naive merged band — clickable 1000s km
  outside the event; redirect shims drop `date` and leave stale `raw=`/
  `global=` keys in the rewritten URL). READY flag stays with frontend.
- **Token**: dark `--map-ocean: #0e1620` (mine, styles.css) — box strokes
  verified ≥3:1 on it (heat 4.6 / freeze 3.0 / AR 7.8); light #eef4f8
  (3.9/6.7/4.3). Live deploy may lag the worktree; hard-reload probes.
- QA evidence: `notes/screenshots/qa-desktop-light-1280.png`,
  `qa-mobile-390.png`, `qa-desktop-dark-1280.png`. Final live state:
  dark ocean token live, AM hit-band + redirect shims FIXED (frontend
  shipped mid-QA; merged/raw e2e 4/4 pass). Remaining (channel riders
  2-3): axe `.gm-ev` aria-label/<title> unify; guardian busiest-day
  test must use the shipped end-day-inclusive rule; `--map-line`
  basemap stroke ask (non-gating, frontend one-liner in drawBase).
- Frontend edits dashboard/ live in parallel (shared worktree) — re-read
  files before patching; they sync to the box themselves.

Polish (19:45): full-width slider spec posted to channel (CSS-only, e2e-safe
contract); QA pending frontend's deploy — probe = slider ends vs section
edges vs gantt tick x at 1440/1280/390.
Slider polish QA PASS 20:50 (thumb centers pixel-exact on the gantt axis at
1280+390; readout row-1 right-aligned; e2e 103/1 with the 1 being a flake).
Ponytail 22:21 sweep (4 NICE) all fixed 22:40: tall-note pair, gm-chip-pressed
dup + --accent-soft token, gm-halo dup, empty @media. Live==worktree.
