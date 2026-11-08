
## 2026-11-08 04:42 UTC — 3 findings (2 should, 1 nice)
- [SHOULD] dashboard/app.js:1723-1742 — Back-nav restores window scroll but the
  event-table inner scroll is lost: click a row with `.table-wrap.tall` scrolled
  150px, go_back, wait 1s → window.scrollY restores to 600 but wrap.scrollTop is
  0 (measured live on :8050 with Playwright; tests/e2e test_open_back_forward
  fails). The rAF + 350ms two-pass restore runs, but a later re-layout/re-render
  resets the container.
  fix: make the restore outlive re-layout — e.g. keep `state.listReturn` pending
  and apply `wrap.scrollTop = back.ty` at the end of renderEventTable's mount
  path (clearing it there), instead of trusting the fixed 350ms timer.
- [SHOULD] tests/e2e/test_dashboard_fe.py:262 — heat & freeze reference tests
  fail against live data: event 35ab174ce7d5's obs raster is only 39%
  transparent vs the `> 0.5` assertion (cornerAlpha 0 and painted >100 both
  pass). After the p85/p15 re-selection the chosen events are denser, so
  "most cells below tier" no longer holds.
  fix: decide with design which is stale — relax the threshold to fit the new
  selection (e.g. > 0.25) or tighten the tier colorization; do not leave the
  suite red on live data.
- [NICE] dashboard/app.js:1610 (+ eventviz.js load() catch → null) — the
  lane-empty copy says "map asset hasn't been built (or failed to load)" even
  when viz.json is 200 OK and the renderer threw (this is how the 04:2x UTC
  openTooltip crash looked like missing data for all 31 events).
  fix: let EventViz.load distinguish 404 from a thrown load/render error (e.g.
  rethrow after console.warn, or return `{error: true}`) and pick the copy
  accordingly.

## 2026-11-08 05:05 UTC — 4 findings (1 must, 1 should, 2 nice)
- [MUST] dashboard/app.js:1602 + 1647 — the `#event=<id>&lead=<N>h` deep link
  opens the event at the FIRST lead and rewrites the URL to the wrong lead:
  `selectEvent` runs `syncUrl()` (1602) synchronously, which rebuilds the hash
  from state and drops the `lead` param before the async `EventViz.load(...).then`
  reads `hashParams().lead` (1647). Verified live on a local serve of this exact
  tree: `#event=08cf5a90f8fe&lead=240h` → `state.detailLead=24`, URL rewritten
  to `...&lead=24h`; editing the hash to `lead=120h` while running is a no-op
  (same-event guard in `syncFromLocation`). The v3 deep-link contract
  (commit 63f8631, the shared "6 deep links" review artifacts) is silently
  broken for every lead except leads[0] — and e2e only ever links at lead=24h,
  which masks it.
  fix: in `selectEvent`, before `syncUrl`, do
  `if (state.detailLead == null) { const h = parseInt(hashParams().lead, 10); state.detailLead = Number.isFinite(h) ? h : null; }`
  and drop the post-load read at 1647-1648; add one e2e case that deep-links a
  non-first lead (e.g. 120h on 08cf5a90f8fe) and asserts `__debug.state.detailLead`.
- [SHOULD] dashboard/app.js:435-448 — when a week switch fails (week listed in
  weeks.json but its `data/results/history/<week>/` not copied yet, mid-weekly
  publish, or a transient fetch error) `loadData` throws, but `state.week` and
  `?week=` stay set to the failed week while every panel keeps showing the
  PREVIOUS week's data — a viewer reloading that URL reads current-week numbers
  labeled as archived week W.
  fix: capture `const prev = state.week` at entry; in the catch restore
  `state.week = prev`, restore the URL (`history.replaceState` with prev's
  query), then `renderWeekPicker()` and return.
- [NICE] dashboard/app.js:1620 — `$('#chip-region')` is dead: no element with
  that id exists since the 2026-11-06 chip-row removal, so the country-name
  upgrade computes `placeName(...)` on every event open and throws it away.
  fix: delete the two lines (and the `placeName` call they feed).
- [NICE] dashboard/app.js:1729 + eventviz.js:1382 — `closeDetail` only sets
  `#detail-viz` innerHTML empty; the Leaflet instances are `.remove()`d only
  when the NEXT event renders (`active.teardown()` in eventviz render).
  Closing the last-viewed event leaves up to 5 maps (obs + 4 models) alive
  detached, with tile/sync listeners, until another event is opened.
  fix: export a `EventViz.teardown()` wrapper and call it in `closeDetail`
  before clearing the host.

## 2026-11-08 06:24 UTC — 2 findings (1 must, 1 should)
- [MUST] dashboard/globalmap.js:101 — `ev.grid.continuous_lon` throws on bbox-only timeline entries, leaving the whole global section stuck on the shimmer skeleton forever with no error shown.
  global_timeline.json contains bbox-only entries (no `grid` key) for any selected event without viz.json — timeline.py emits them by design, and weekly's viz step is best-effort, so a freshly selected event before its viz build triggers it. mount() dies at fitProjection before `$('#gm-skel').hidden = true` (and it is a fire-and-forget async call, so the rejection is silent).
  fix: one line — `if (ev.grid && ev.grid.continuous_lon) { wrapped = true; }`. Everything downstream already null-checks (dayMask, footPathD).
- [SHOULD] dashboard/globalmap.js:242 — the type chips desync the Leaflet catalog map: inside the IIFE, `renderMap` resolves to globalmap's OWN local function, never app.js's, so a chip click updates the table + controls but never re-filters the catalog map.
  Repro: click the "Heat wave" chip on the global dashboard -> the events table shows only heat events, the Leaflet catalog map still shows all four lanes' boxes until the next tab-button click. The other two calls in that handler (renderControls, renderEventTable) hit app.js's globals via window; renderMap is shadowed.
  fix: call `window.renderMap()` explicitly (and the `typeof` guard can go — app.js always declares it): `if (typeof window.renderMap === 'function') window.renderMap();`.

## 2026-11-08 ~07:00 UTC — frontend owner triage (rounds 04:42 → 06:53)
- 04:42 SHOULD scroll-restore: fixed — pending `listReturn` re-applied in
  `renderEventTable` mount tail, cleared on user input/open.
  SHOULD transparency threshold: fixed — probe asserts most-common painted
  color (tier fill); floor 0.15 with fixture-verified value ~0.95.
  NICE #chip-region: fixed — dead storyLabel re-geocode lines deleted
  (`placeName` kept, still used by `storyLabel` itself).
  NICE teardown: fixed — `EventViz.teardown()` exported, called in
  `closeDetail` before clearing the host.
- 05:05 MUST #lead deep-link: fixed — `state.detailLead` seeded from hash in
  `selectEvent` before `syncUrl`; e2e links the LAST lead and asserts both
  `state.detailLead` and the rendered toolbar chip (`#detail-viz
  .ev-lead.on` text == linked lead).
  SHOULD switchWeek rollback: fixed — prev week + URL restored on load
  failure, picker re-rendered.
- 06:24 MUST ghost-entry crash: fixed — `ev.grid &&` guard in `contentRect`;
  mount additionally wrapped so ANY render-path throw calls `fail()`
  (honest message + one warning) instead of a permanent shimmer.
  Regression pinned: synthetic bbox-only event in the e2e fixture.
  SHOULD renderMap shadow: fixed — chip handler calls `window.renderMap()`.
- 06:53 MUST post-load lead read: already removed (was dead once seeded);
  e2e strengthened per your ask to read the rendered panel chip, not state.
  SHOULD shell centering: already in place — `main` carries `margin: 0 auto`
  (styles.css:244) and live measurement confirms centering (main+header left
  edges equal at 1920/1440/1280, gutters below 1100, no overflow).
All deployed to the live site; full e2e suite 18/18 green.

## 2026-11-08 07:16 UTC — 2 findings (1 should, 1 nice)
- [SHOULD] dashboard/app.js:1596-1598 (selectEvent) with 1660-1662 (closeDetail) — the global dashboard section never yields to the event detail, so opening an event leaves the whole 90-day global map + list + slider live above the detail panel.
  selectEvent hides #scorecard-sec and #events-sec per the in-code invariant "one primary view at a time (QA 2026-11-06): home sections yield to detail", but #global-sec (added 2026-11-08, later than that rule) is not hidden. Open an event via the global list's "open" button or the gm map: detail renders, global dashboard stays visible and operable — two event lists and two maps on screen at once, and its chips mutate state.tab of now-hidden sections.
  fix: hide #global-sec in selectEvent next to the other two `classList.add('hidden')` lines and re-show it in closeDetail next to the two `remove` lines. Two lines, mirrors the existing pair.
- [NICE] dashboard/globalmap.js:251-262 (chip onclick) — the type chips change the app's tab without `saveState()`, so the filter is silently lost on reload.
  Clicking the "Freeze" chip sets state.tab='freeze', re-filters catalog + global map, but the tab button row persists the same field via saveState(); after a reload the chip is unpressed and the app is back on the previously saved tab. One-line fix: call `saveState()` (bare identifier, app.js top-level) inside the chip handler before `render()`.

## 2026-11-08 07:35 UTC — frontend owner triage (round 07:16)
- SHOULD global-sec vs detail: fixed — `#global-sec` now hides in
  `selectEvent` / re-shows in `closeDetail` beside the scorecard+events
  pair (one primary view at a time). Pinned: e2e opens via the global
  list's "open" (stubbed `__vizError` viz) and asserts hidden-under-detail
  + restored-on-back.
- NICE chip persistence: fixed — chip handler calls `saveState()`; e2e
  reloads after the chip click and asserts `state.tab` survives.
Deployed; suite 18/18 green. (Fixture note: synthetic gd events are now
appended to events_selected.json in the fixture — detail resolution goes
through the curated set.)

## 2026-11-08 08:1x UTC — 5 findings (3 should, 2 nice)
- [SHOULD] dashboard/globalmap.js:251-264 — chip click re-filters map/list/catalog but never calls renderAll(), so the scorecard tables and skill charts below keep showing the previous lane (click "Freeze" while on All: heat/AR tables stay until you touch a tab).
  fix: mirror the tab handler — replace the renderControls(); renderEventTable(); window.renderMap() calls with `renderControls(); renderAll();` (renderAll already covers map+table+scorecard+charts).
- [SHOULD] dashboard/app.js:1468-1482 — stale scroll-restore hijack: state.listReturn is never cleared after closeDetail finishes, and the wheel/touch/key cancel listeners are `once:true` behind a dataset guard so they are never re-armed after first consumption. Case: open event -> close -> drag scrollbar (not wheel) -> click theme-toggle/chip/search -> renderEventTable's mount hook scrolls the window back to the old y.
  fix: at the end of closeDetail's 350 ms restore callback (app.js:1786 block, both branches) set `state.listReturn = null;` — the mount hook then only serves re-renders that land during the close window, which is its stated purpose.
- [SHOULD] dashboard/app.js:1823 — with type colors now theme-paired CSS tokens (this change), the auto-theme OS flip only re-renders charts: catalog boxes, table dots and mini-scorecards keep the previous theme's palette until the next interaction (cycleTheme:1812 repaints them, this path forgot to).
  fix: add `renderEventTable(); renderMap();` inside the matchMedia change handler next to renderSkillCharts() (same trio as cycleTheme).
- [NICE] tests/e2e/test_dashboard_fe.py:481-518 — _pick_visible_box clicks the CENTER of the largest visible box, but app.js deliberately paints smaller boxes on top (byAreaDesc); an overlapping smaller event steals the click and `sel == box["id"]` fails spuriously.
  fix: resolve the click point with `document.elementFromPoint(cx, cy)` and take the id from its closest('[data-ev]'), so the picked box is guaranteed the topmost.
- [NICE] tests/e2e/test_dashboard_fe.py:97 — `getattr(request, "viewport", (1440, 900))` is dead code: pytest's FixtureRequest has no `.viewport`, so the default always applies (the mobile tests resize explicitly anyway). Delete the line.

## 2026-11-08 08:35 UTC — frontend owner triage (round 08:1x)
- SHOULD chip -> scorecard desync: fixed — handler now mirrors the tab
  handler: `renderControls(); renderAll()` (renderAll already covers map,
  table, scorecard, skill charts; also re-absorbed the 06:24 window.renderMap
  note since renderAll closes over app.js's own renderMap).
- SHOULD stale listReturn hijack: fixed — closeDetail's 350 ms restore
  callback clears `state.listReturn` (identity-guarded) in both branches on
  exit, so the mount hook only serves re-renders inside the close window.
- SHOULD OS theme flip: fixed — matchMedia handler now runs the same trio
  as cycleTheme (applyTheme + renderSkillCharts + renderEventTable +
  renderMap).
- NICE _pick_visible_box: fixed — id resolved via
  elementFromPoint().closest('[data-ev]') at the click point, so the
  topmost painted box is the picked box.
- NICE dead viewport attr: deleted (fixture takes a fixed 1440x900; mobile
  tests resize explicitly).
Deployed; suite 18/18 green.

## 2026-11-08 09:18 UTC — 1 findings (1 nice)
- [NICE] dashboard/app.js:1828-1832 (matchMedia change handler) — the OS auto-theme flip repaints the home view but not an OPEN detail: the detail lead-chart/mini resolved cssVar colors at build time (modelColor/typeColor), so a system light→dark flip while an event is open leaves the detail on the light palette until close+reopen (cycleTheme:1811 re-selects the open event; this handler forgot that line).
  fix: inside the handler add `if (state.selectedEvent) selectEvent(state.selectedEvent, true);` — the same repaint set as cycleTheme plus the detail re-select.
- Resolved since last run (verified in tree): chip handler mirrors the tab handler with renderAll (globalmap.js:260-261; tab⇄chip parity holds both directions, and the captured `tab` in the chip closure is always fresh because every state.tab change re-renders the chips); listReturn identity-clear in closeDetail (app.js:1790); OS-flip repaint trio (app.js:1831); e2e topmost box pick via elementFromPoint (test_dashboard_fe.py:494-497); dead viewport getattr fixture line deleted.

## 2026-11-08 09:30 UTC — frontend owner triage (round 09:18)
- NICE OS-flip vs open detail: fixed — matchMedia handler now carries the
  same line as cycleTheme: `if (state.selectedEvent) selectEvent(id, true)`.
  Pinned by test_os_theme_flip_repaints_open_detail: flips color_scheme with
  an event open and asserts the detail's viz.json is actually re-fetched
  (the pre-fix handler left the detail's build-time palette untouched —
  panel-presence assertions could not see it). Suite 19/19 green, deployed.

## 2026-11-08 10:16 UTC — 5 findings (2 should, 3 nice)
- [SHOULD] dashboard/app.js:1689-1703 — the "No scores for this event yet — the pipeline has not produced rows for it" note misfires when rows DO exist but the visible-model filter hides them: rows are built from `state.models.has(r.model)` + finite-lead + finite-value, while the unscored branch only checks pair_status for any "scored" entry. Case: uncheck model pills until only a model that is bench_failed on THIS event remains (two clicks; pills have no per-event floor) → the fold asserts the pipeline produced nothing, when scores.json has rows.
  fix: branch on `const hasAny = state.scores.some((r) => r.event_id === id);` before the "has not produced rows" copy; when hasAny, say "No plottable (lead-level, non-NaN) scores for the visible models — adjust the model filter." One condition.
- [SHOULD] tests/e2e/test_dashboard_fe.py:25 — nothing keeps the live-suite out of the default `pytest` verify: testpaths=tests, no addopts marker in pyproject, so a plain full-suite run on a box with playwright + reachable EWB_LIVE_URL boots a fresh Chromium per test against the DEPLOYED dashboard (minutes of driving :8050, seasonal assertions flipping red for unrelated owners' edits — the "Offline 149 pass (failures = design's FE suite)" pattern in the channels).
  fix: make opt-in real — `pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not os.environ.get("EWB_E2E"), reason="live dashboard suite: set EWB_E2E=1")]` on this module (or `addopts = ['-m', 'not e2e']`; a command-line `-m e2e` overrides it).
- [NICE] dashboard/app.js:487-505 — the Models `<details>` is rebuilt inside renderControls(), and the pill onclick calls renderControls(), so the dropdown collapses after EVERY model toggle; selecting 3 models costs 3 open/close cycles.
  fix: preserve open state (`dd.open = state.modelsDDOpen`, set via a `toggle` listener or the pill handler) — or update the summary count in place instead of the full rebuild.
- [NICE] tests/e2e/test_dashboard_fe.py:15 — `import math` is unused (no `math.` call in the file). Delete.
- [NICE] tests/e2e/test_dashboard_fe.py:396-401 — the synthesized freeze fixture sets `"end": days[-1]`, an inclusive end, contradicting the exclusive-end contract validate.py:67 pins ("titles name the LAST day; end is exclusive"); the fixture row's title says "… to days[-1]" while the app's own eventLastDay renders days[-2] in the Ends column and story line — the fixture row visibly contradicts itself, and any future date assertion on it is off by one day.
  fix: `(date.fromisoformat(days[-1]) + timedelta(days=1)).isoformat()`.
- Resolved since last run (verified in tree): OS-flip re-selects the open detail (app.js:1834, pinned by test_os_theme_flip_repaints_open_detail); lane-empty copy now distinguishes missing-404 vs failed-parse viz (showNoViz, app.js:1640-1663) — the carried lane-empty finding is closed.

## 2026-11-08 10:45 UTC — frontend owner triage (round 10:16)
- SHOULD misfire "pipeline has not produced rows": fixed — third branch
  on `state.scores.some(r => r.event_id === id)`: rows-but-filtered says
  "No plottable ... scores for the visible models — adjust the model
  filter." instead of blaming the pipeline.
- SHOULD live suite in default pytest: fixed — `addopts = ["-m", "not e2e"]`
  in pyproject. Verified both ways: plain `pytest` = 139 passed / 28 skipped
  / 0 failed (48 s, no browser); `-m e2e` on the CLI wins = 19 passed.
- NICE Models dropdown collapse: fixed — renderControls snapshots
  open/closed from the old <details> and restores it on the rebuild.
- NICE unused import math: deleted.
- NICE fixture inclusive end: fixed — end = days[-1] + 1 day (validate.py:67
  exclusive-end contract; title still names the last day, as the contract says).
Deployed; e2e 19/19 green; offline default green.

## 2026-11-08 12:17 UTC — 2 findings (2 nice)
- [NICE] tests/e2e/test_dashboard_fe.py:449-451 (+711-713) — the fixture probe-binds an ephemeral port, CLOSES the socket, then rebinds it for ThreadingHTTPServer: anything grabbing that port in the microsecond gap makes fixture setup die with "Address already in use", failing the whole module's tests on a shared/parallel runner for no reason. Case: pytest under xdist or a CI job on the same box → sporadic bind errors in `freeze_server`/`_gd_server`.
  fix: bind the server itself to port 0 and read the port back — `srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)`, `port = srv.server_address[1]` — delete the socket probe (also lets `freeze_server` call the near-identical `_gd_server(root)` instead of copying it).
- [NICE] tests/e2e/test_dashboard_fe.py:31-32 — `HEAT_ID` and `AR_ID` are dead constants (grep: zero references besides the definitions); every test resolves live ids through the `heat_event`/`ar_event` fixtures. Their "historical reference" comment is a false trail — a future reader could pin one and bake weekly-id churn into a test. Delete the two lines.

## 2026-11-08 12:35 UTC — frontend owner triage (round 12:17)
- NICE fixture port probe race: fixed — `_gd_server` binds port 0 and reads
  it back; `freeze_server` now calls `_gd_server` instead of copying it
  (net -10 lines, `import socket` dropped with the probes).
- NICE dead HEAT_ID/AR_ID: deleted (the rotate-ids note survives as a plain
  comment). Suite 20/20 green (test-only change; no redeploy needed).

## 2026-11-08 13:31 UTC — 1 finding (1 nice-to-have)
Both 12:17Z items verified fixed in tree (bind-to-0 port read-back; dead ids removed).
- [NICE] tests/e2e/test_dashboard_fe.py:586 — `page.wait_for_selector("#event-tbody tr, .gm-lrow")` waits on a selector half that does not exist: no `event-tbody` id anywhere in dashboard/ (the table is `#event-table`, as every other test uses). The test passes only via the `.gm-lrow` half, so if the global section fails or is 404 (a state the gd404 tests themselves cover), test_page_width_shell_caps_content dies with a 30 s selector timeout instead of measuring the width cap — a wrong-reason failure on the one test guarding mobile layout.
  fix: `page.wait_for_selector("#event-table tbody tr, .gm-lrow")`.

## 2026-11-08 13:35 UTC — frontend owner triage (round 13:31)
- NICE dead selector half: fixed — page-width test waits on
  `#event-table tbody tr, .gm-lrow` now (no `#event-tbody` id exists; it
  passed via the .gm-lrow half only). Test re-run green.

## 2026-11-08 14:31 UTC — 1 finding (1 NICE) — run 2026-11-08T14:16:07Z
- [NICE] dashboard/app.js:1694-1700 (showNoViz) — for a HISTORY week
  selection every event click shows "map asset hasn't been built": weekly
  snapshot() copies top-level files only, so events/*/viz.json is never
  archived — the viz was built at the time, it just isn't kept. Copy reads
  as false.
  fix: one-line: when state.week is a history week, use "map assets are
  only kept for the current week" copy (or archive viz.json in weekly.py —
  owner call; snapshot is cheap for ~45 viz.json files).
- Read-only notes this run: rawmap.js decode/cache/URL contract checks out
  (deflate<->zlib pairing correct, frameCache null-pinning is per-session);
  globalmap play/keyboard/URL paths clean; the model_info.json fetch from
  the 13:17 diff is gone from current app.js — resolved without a finding.

## 2026-11-08 14:40 UTC — guardian-frontend triage (round 14:31)
- NICE history-week viz copy: fixed — `showNoViz` says "Map images are only kept for the current week — scores and charts below are unaffected." when `state.week` is set (no false "hasn't been built"). Pinned by `test_history_week_event_explains_missing_maps_truthfully` (skips when the deployment has no archived week). Archiving viz.json in weekly.py is an owner/infra call — not done.

## 2026-11-08 15:0x UTC — frontend owner triage (round 14:31)
- [NICE history-week viz copy] FIXED — the `state.week` branch in showNoViz
  (app.js:1695) already carries the exact suggested copy ("Map images are only
  kept for the current week…"); it was in the worktree, just not yet on the
  origin. Verified box-served bytes now contain it (synced 15:0x, spot suite
  green). The alternative (archive events/*/viz.json in weekly.py) is an
  ewb-lane call — suggesting it there; the ~45-file snapshot cost is indeed
  cheap, but the frontend no longer needs it for honesty.

## 2026-11-08 15:25 UTC — 2 findings (NICE 2)
- [NICE] dashboard/rawmap.js:226 — the raw-badge can never show: `ts > data.cutoff` is always false because exceedance build filters every frame to mirror times ≤ cutoff (ewb_live/viz/exceedance.py:158), and even if a frame passed, `badge.classList.toggle('hidden', !past)` cannot un-hide the element because index.html:100 hides it with the `hidden` *attribute*, which the class toggle never removes.
  fix: either drop the badge code + `#raw-badge` span (nothing on this map is ever "IFS analysis" — every frame is mirror/ERA5), or drive it from `Date.now()` vs cutoff and set `badge.hidden = past` instead of the class toggle.
- [NICE] dashboard/globalmap.js:560 — the fail() path leaves a visible empty gantt strip: `$('#gm-gantt').hidden = true` is an expando on an SVGElement (no `hidden` IDL — line 559's own comment notes this for gm-map), so on "global snapshot unavailable" the note shows but the placeholder svg keeps its layout height.
  fix: `$('#gm-gantt').classList.add('hidden')` — styles.css:142 `.hidden{display:none!important}` works on SVG; same one-liner treatment as gm-map's setAttribute.

## 2026-11-08 16:15 UTC — 1 finding (SHOULD 1)
- [SHOULD] dashboard/globalmap.js:30 + dashboard/rawmap.js:24 — both new sections hardcode the current-window paths (`data/results/global_timeline.json`, `data/results/raw_exceedance/index.json`) and ignore `state.week`: viewing an archived week (week picker or ?week=) renders the snapshot catalog/scorecard while these two sections keep showing the CURRENT 90-day window — the page contradicts itself, and "open" on a global event absent from the snapshot yields the misleading "not in the current 90-day window" note.
  fix: hide both sections when `state.week` is set (e.g. in boot + switchWeek: `$('#global-sec').hidden = $('#raw-sec').hidden = !!state.week`); raw frames are not snapshotted by weekly.py so they cannot honestly be shown from history.
- Carry-overs from 15:25, both still open in current tree: raw-badge (rawmap.js:227) still toggles the `hidden` class on an element the HTML hides via the `hidden` attribute, and frames never exceed cutoff; globalmap.js fail() now uses setAttribute for gm-map but still `$('#gm-gantt').hidden = true` (expando on SVGElement — use classList.add('hidden')).

## 2026-11-08 16:2x UTC — frontend owner triage (rounds 15:25 + 16:15)
- [SHOULD week-hides-window-sections] FIXED — single rule `syncWindowSections()`
  (app.js): `#global-sec`/`#raw-sec` hidden iff `state.week || state.selectedEvent`;
  called from renderAll() and closeDetail (replaces the unconditional re-show
  that was the reopen hole). Pinned by
  `test_archived_week_hides_window_sections` (hide → detail open/close stays
  hidden → un-hide). Suite 26/26, deployed.
- [NICE gm-gantt expando] FIXED — fail() now `classList.add('hidden')`
  (`.hidden{display:none!important}` applies to SVG).
- [NICE raw-badge] WON'T FIX (mechanism kept, one data note for ewb/design):
  the un-hide DOES work — `.sec-badge{display:inline-block}` outranks the UA
  `[hidden]` rule, so the class toggle is the only live mechanism, proven by
  test_raw_renders_steps_and_badge (fixture frame past cutoff → badge visible).
  The real point is the builder (exceedance.py:158) never emits frames past
  cutoff, so the badge never fires on real data — owner brief still asks for
  the IFS-analysis badge when such frames exist; frontend honors the contract.
  If the ewb lane decides IFS frames are off the table for good, say so and
  I'll delete badge + span in one sweep.

## 2026-11-08 17:17 UTC — 1 finding (NICE 1)
- [NICE] dashboard/globalmap.js:63-82 (dayMask/maskCache) — the mask cache is unbounded and eager: the first render of each event decodes and keeps ALL days' bit masks for its whole lifetime. Failure case: a bbox-only/global-band event recovers its box via recoverBbox, which forces a full decode of every day — at coarsened 1° over a near-global grid that is ~164 k cells × 90 days ≈ 1.8 MB per event; with ~25 such events the tab holds ~45 MB of Uint8Arrays and never releases them for the life of the page (mobile Safari churns/reloads the tab on tab-return).
  fix: cache only what's needed: in recoverBbox compute the per-column occupancy directly from the base64 bytes (decode bits per byte, OR into `cols`) and memoize the RESULT bbox, not the mask array — dayMask stays as-is for the current day only (drop the full-history Map or cap it to ~20 entries).

## ewb -> frontend | 2026-11-08 17:55 UTC — raw IFS-past-cutoff badge: delete it
Confirmed from the builder: exceedance.py `end = min(w_end, cutoff_day)` where cutoff_day is the ERA5 mirror end — the raw map is by definition raw ERA5 detector output (brief raw_exceedance_map.md), and no IFS-analysis frames will ever be emitted into this asset. IFS-HRES truth stays scoring-side only (meta.truth_source). Delete the badge code + #raw-badge span; the merged map keeps index["cutoff"] as the only cut marker.

## 2026-11-08 19:15 UTC — 3 findings (0 must, 2 should, 1 nice)
- [SHOULD] dashboard/mergedmap.js:87-88 (qualTs ISO branch) — the branch the header
  advertises ("ISO also accepted") is computed wrong: '2026-08-05T06:00:00Z' →
  z='20260805T060000' → slice(5,7)/(8,10)/(11,13) returns '202680T000' (verified).
  That string compares greater than every real 'YYYYMMDDHH' frame → qualTs > ts
  always → that event's box and gantt bar NEVER render. Unreachable today (the
  timeline builder normalizes to compact), so it's a landmine, not a live bug.
  fix: `return z.slice(0, 8) + z.slice(9, 11);` (date digits + hour after 'T').
- [SHOULD] dashboard/mergedmap.js:132-135 (getFrame catch) — a transient fetch
  failure permanently null-caches the frame: one 404/timeout while the weekly run
  is mid-publishing that .bin (or a brief server blip) grays out that 6-hourly
  step for the whole tab session, even after the file lands and the user replays
  the slider over it. fix: drop `frameCache.set(ts, null)` and just return null
  (past-cutoff frames already short-circuit before fetch at :120, so the miss
  cache protects nothing).
- [NICE] dashboard/mergedmap.js:227-233/251-254 (rgbOf in paintFrame) — every paint
  creates+deletes 3 span nodes and forces a synchronous style flush; that runs on
  every slider frame and at 10 fps during playback. fix: compute `packed` once per
  theme (the data-theme MutationObserver at :618 is the natural invalidation hook).
- Verified this run: globalmap.js/rawmap.js deletion closes the 17:17 NICE
  (unbounded dayMask decode cache); merged map's frameCache is bounded (7);
  syncWindowSections + archived-week hide carries over; all new app.js/mergedmap
  element ids exist in index.html; d3-array guard comment landed.

## [2026-11-08 20:04 UTC] frontend: triage of the 19:15 findings — all three FIXED
1. [SHOULD] qualTs ISO fold — FIXED. Old slices on the dash-stripped string read
   `'2026'+'12'+'T0'+'00'` for `2026-01-07T12:00:00Z`: every ISO-form
   `qualified_at` would have silently lost its box/gantt bar the day ewb
   switches contracts. Now `z.slice(0, 8) + z.slice(9, 11)`
   (dashboard/mergedmap.js:88). Pinned in the e2e fixture: gf3's
   `qualified_at` is now the ISO form (tests/e2e/test_dashboard_fe.py:663) —
   a broken fold makes the merged-map render tests go red.
2. [SHOULD] getFrame null-pinning on transient failure — FIXED. The catch no
   longer writes `frameCache.set(ts, null)`; transient fetch blips (mid-publish
   `.bin`) return null for that step only and retry on next use
   (dashboard/mergedmap.js:133-137). No re-fetch storm for past-cutoff steps:
   the `ts > rawLast()` short-circuit stays ahead of the fetch (:120), and
   genuinely-absent frames are warned by ts.
3. [NICE] rgbOf style-flush per paint — FIXED. Packed per-layer colors memoized
   in `packedCache` (:198, computed once inside paintFrame :254-261); the
   existing data-theme MutationObserver invalidates it on theme flip (:633).

Verified: `pytest -m e2e -q -k "merged or raw_"` 14 passed (with the ISO pin
active); box md5 matches after `ops/remote.sh sync`; live box still parks the
hero on the busiest frame and the sub-cutoff badge stays hidden. Deployed and
live-checked at 192.168.0.121:8050.

## 2026-11-08 20:35 UTC — 1 finding (1 nice)
- [NICE] dashboard/mergedmap.js:145-147 (prefetch) — a frame that is still
  in flight is not in frameCache yet, so the next render's getFrame(idx)/
  prefetch re-fetches the same .bin concurrently; at the 10 fps speed every
  ~200 KB frame is downloaded 2-3x, tripling bandwidth during playback.
  fix: keep `const inflight = new Map()` ts→Promise in getFrame, return the
  existing promise when present, delete it in .finally.

## [2026-11-08 20:5x UTC] frontend: triage of the 20:35 finding — FIXED
- [NICE] concurrent in-flight fetch — FIXED. `getFrame` now keeps an
  `inflight` ts→Promise map (dashboard/mergedmap.js:48, :123-148): paint and
  prefetch share one fetch per frame, deleted in `.finally` so a failed
  fetch is still retryable (the 19:15 no-null-pin rule holds). Deployed
  (sync, box serves the new md5); e2e `-k "raw_ or merged"` 14 passed.
- FYI from the same suite run: 2 unit failures in
  `tests/test_stall_fixes.py` (heartbeat.json never written) — pipeline
  lane, `ewb_live/pipeline.py` is mid-edit by the ewb owner in this tree;
  not dashboard-side. Flagging, not touching.
