# channel: make-nicer (append-only)

## pi-nav 2026-11-11T02:38Z — ask: the #raw-layers chip row lives in your files (complaint 3)
Complaint 3 = two sets of heat/freeze/AR toggles: `#type-tabs` ("Show: All / Heat waves /
Freezes / Atmospheric rivers", app.js, in `.explore-head`) and `#raw-layers` ("MAP LAYERS:
Heat — days > P85 / Freeze — days < P15 / AR — IVT magnitude", mergedmap.js, under the map).
They are the same control: choosing a Show tab already calls `MergedMap.onTabChange →
layersForTab()` and turns exactly that layer on, so the chip row is the redundant second set.
Orchestrator assigned the removal to me; keeping the tab set means nothing is lost (event-list
filtering, counts, `#layers=` hash state and `localStorage.tab` all already ride the tabs).

I own index.html / app.js / styles.css only, so **I need you to take mergedmap.js**:

1. delete `function renderLayerButtons()` (~l.791-820);
2. delete its four call sites, keeping everything else on those lines:
   - `applyUrl()` — the bare `renderLayerButtons();` in the `'layers' in params` branch
     (`render()` at the end repaints + `renderLegend()`);
   - `_build()` — the bare `renderLayerButtons();` before `const boot = hashParams();`;
   - `_build()` — `if (!('layers' in boot) && pageTab() !== 'all') { layersForTab(pageTab()); renderLayerButtons(); }`
     → keep `layersForTab(pageTab())`, drop the button call;
   - `MergedMap.onTabChange` → keep `layersForTab(tab); writeUrl(false);`.
3. keep the `on` state, `LAYERS`, `urlLayers()` and the `'layers' in params` branch as they are —
   `#layers=h` deep links must keep working, they just have no widget any more.

I will delete the `.map-legend` row + `#raw-layers` markup, the `.gm-chips/.gm-chip*/.map-legend`
CSS and app.js has nothing to remove (the tabs are not chips). Until your commit lands I will NOT
remove the markup, so neither of us ships a broken page — say GO and I land mine in the same pass.

Two follow-ups in your area, not blocking:
- `@media (max-width:699px) .lg-unit { display:none }` is justified in the comment by "the chips
  under the map name the lanes + thresholds". With the chips gone the phone legend loses the units
  ("consecutive days > P85" etc.) — worth folding the unit back in.
- the `#gm-legend` comment says "the layer chips below carry the long tips"; that hint is stale.

Reply here with GO / mergedmap.js done and I'll land markup + CSS. — pi-nav

## orchestrator 2026-11-11T02:45Z — ruling on #raw-layers
pi-nav's analysis is accepted: `#type-tabs` stays, the `#raw-layers` chip row goes.
pi-colors: you own mergedmap.js, so please make pi-nav's steps 1-3 above (remove renderLayerButtons + its 4 call sites, keep `on`/LAYERS/urlLayers/#layers= deep links) in your next commit, on its own, with a message starting "mergedmap: drop #raw-layers chips". Also take the two follow-ups: put the unit text back in the phone legend, and fix the stale "layer chips below" comment. The legend is yours anyway. Post "GO pi-nav" here once it's committed.
pi-nav: until GO, keep working on the side nav, then land the markup + CSS removal.

## pi-nav 2026-11-11T02:58Z — nav work committed; #raw-layers removal staged locally, waiting on GO
Side nav (complaint 2) is committed: `a9f2371` (index.html/app.js/styles.css only). Headless
probe: 0 console errors, no horizontal scroll at 320/390/768/1024/1280/1440/1920, every rail link
scrolls its section (phones now stop 60 px below the topbar instead of under it), active section
follows scroll, tab order + focus rings + drawer focus trap verified.

Complaint 3 is edited in my worktree (markup + CSS only) and pre-validated against a
*preview-only* patched copy of mergedmap.js on the box (`/opt/ewb-preview-nav`, port 8062 —
I never touched /opt/ewb-live): tabs alone still drive event-list rows 28/15/1/12, `MergedMap
._dbg().on` h/f/a, `#layers=` in the hash, and the legend still renders all three lanes.
Zero console errors. I am holding the commit until you post GO, so HEAD never ships a page
whose `#raw-layers` node is missing while mergedmap.js still writes to it.

FYI when you cut mergedmap.js: the comment at mergedmap.js:53 ("raw is still null when
renderLayerButtons first runs") goes stale with the function — `minDays()` itself stays
(paint + legend both use it). Also `.gm-chip*`, `.gm-chips` and `.map-legend` CSS is now gone
from styles.css; `globalmap.js`/`rawmap.js` still build `#gm-chips`/`#raw-layers` chips but
`index.html` loads neither file, so nothing at runtime reaches for them. — pi-nav

## orchestrator 2026-11-11T03:10Z — review of pi-nav a9f2371 + removal shots (NOT accepted yet)
Good: the chip row is gone cleanly, the drawer works, and the active state and grouping read well. Fix these while you wait for GO:
1. The "Light" item in the rail is ambiguous: it looks like a nav destination and doesn't say whether it is the current state or the action. Make it a clear theme control (sun/moon icon + switch, or a segmented Light/Dark/Auto control) and move it out of the link list into the rail footer, next to the date select.
2. The "current week" select in the rail footer has no label, so nobody knows what it controls. Give it a small visible label (whatever it actually scopes, e.g. "Scorecard window") and make sure the "updated …" line under it is aligned and styled consistently with it.
3. On phones the topbar says "EWB Live" and the hero h1 says "EWB Live" right below it. Drop the duplication: either hide the h1 on phones and keep the subtitle, or make the topbar brand minimal.
4. Re-shoot desktop and phone in both light and dark after the fixes and copy the final set into notes/shots/pi-nav/ in the worktree (the brief asked for that, and it's empty right now).

## pi-colors 2026-11-11T03:12Z — GO pi-nav + complaint 1 DONE
GO — mergedmap.js `renderLayerButtons` + its 4 call sites are gone in my commit d473c55
("mergedmap: drop #raw-layers chips"); `on`/`LAYERS`/`urlLayers()`/`#layers=` deep links ride
along untouched (verified: `#layers=h` still paints and filters the legend to the heat lane).
Your markup + CSS removal (8c6edf9) and the legend rework compose cleanly — my legend strip
below the map now carries the lane units, so the phone media query keeps them visible
(pi-nav follow-up 1), and the stale "layer chips below" comments are gone (follow-up 2).

Complaint 1 (map colour scale) — done, commits d473c55 (mergedmap chip removal) + ca5bc93
("map colour scale: ..."). What changed, all three meanings of the complaint:
1. OVERLAY: `#gm-legend` no longer rides inside the map (it covered ~20% of it bottom-left,
   ~40% on phones). It is a fixed-height (48px) slim strip directly BELOW the map, one row;
   at 1440 all three lanes fit exactly (measured scrollWidth == clientWidth), on phones it
   horizontally scrolls and NEVER wraps. Height is reserved while empty (`:empty` keeps the
   box, only paints chrome once it has content; `renderLegend` never sets `hidden`), so the
   strip appearing/changing (layers, frame, Region) moves nothing below it.
2. BASEMAP: fills go 255 alpha only for AR (its colour IS the class, e2e pixel-exact rule);
   heat/freeze use ONE constant alpha (200) instead of per-bin alpha ramps, and coastlines
   are now re-stroked ABOVE the raster in paintFrame, so coasts read through every fill.
3. UNEVEN: `--dur-*` tokens replaced with ColorBrewer sequential ramps sampled at equal
   lightness steps — heat YlOrRd (warm), freeze Blues (cool), AR Greens (distinct from both);
   monotonic L* (darker = more days / higher IVT) in BOTH themes (the old dark AR ramp ran
   the other direction and old heat bins were ~indistinguishable, alpha did the work).
   Bins/labels unchanged and still match the data: heat 3-7+ days, freeze 1-3+ days, AR 250
   →1250+ kg m-1 s-1 in equal 250 steps. Lane units shortened to "days > P85"/"days < P15"
   to fit the strip; full wording stays in the group tooltips.
Shots (before/after, 1440x900 + 390x844, light + dark): notes/shots/pi-colors/. Preview:
:8061 on the box (/opt/ewb-preview-colors). Console clean in all four viewports.
Open, for the orchestrator: `tests/e2e/test_dashboard_fe.py`
`test_legend_lives_in_the_map_and_never_moves_the_page` + `..._moves_nothing` assert the OLD
contract (legend absolute inside `#raw-map-wrap`, pointer-events:none) and now fail by design;
`test_dur_chip_labels_gradient_and_tips` drives the removed `#raw-layers` chips. Nobody owns
tests/ — say the word and I'll update them to the new contract, or assign them.
Also `globalmap.js`/`rawmap.js` are dead code for legend purposes (not loaded by index.html);
I left their internals untouched. — pi-colors

## orchestrator 2026-11-11T03:25Z — review of pi-colors ca5bc93 (NOT accepted yet)
Big improvement on desktop. Moving the legend out of the map and stroking coasts above the fills were the right calls. Remaining:
1. Phone legend: horizontal scroll hides Freeze and AR completely (shot-phone-390-after.png shows "FREEZE 1 2 3…" cut off and no AR at all). Nobody will discover the scroll. On narrow screens stack one lane per row (heat / freeze / AR = 3 rows) with the height reserved so nothing jumps. No horizontal scroll at any width from 320 up.
2. Uneven label spacing: in the AR group "750 1000 1250+" crowd together, and so do "7+ today" for heat. Make every swatch the same width, with its label centered under it and a consistent gap. Wide labels set the column width, not the other way round. The legend should look as even as the scale now is.
3. AR fill: the pale 250 bin still paints broad full-opacity sheets over the oceans. Use the same constant alpha as heat/freeze (or close to it) so the graticule and coasts read through there too, and update whichever e2e pixel assertion depends on alpha 255. Keep AR clearly distinct from heat and freeze.
4. Tests: you own updating tests/e2e for the new contracts (legend strip below the map, no #raw-layers chips, Show tabs as the only type toggle). Rewrite the three failing tests to assert the new behaviour rather than deleting them. Then run the full test suite on the box against your preview and post pass/fail counts. pi-nav's uncommitted nav fixes may land in between, so rerun after they do.
Re-shoot desktop and phone in light and dark into notes/shots/pi-colors/ (name them after2) and post DONE again.

## pi-nav 2026-11-11T03:30Z — DONE (complaints 2 + 3 + review 03:10Z items 1-4)
GO received and landed. My commits: `a9f2371` nav, `8c6edf9` #raw-layers markup+CSS,
`31711fe` review fixes 1-3, `980b0be` screenshots. Nothing of mine is uncommitted; I have not
touched mergedmap.js, the legend, or tests/ (your `M tests/e2e/*` and `M dashboard/mergedmap.js`
in the shared worktree are pi-colors' WIP — my last probe run included that WIP and was green).

**Complaint 3 — one type toggle, not two.** `#type-tabs` (the Show row above the map it filters)
stays; the "MAP LAYERS" chip row is gone: markup (`.map-legend` + `#raw-layers`), CSS
(`.map-legend`, `.gm-chips`, `.gm-chip`, `.gm-chip[aria-pressed]`) and JS (`renderLayerButtons`
+ 4 call sites, by pi-colors in d473c55). Verified the survivor drives everything the removed
set did, in all four viewports, by driving the tabs and reading state back: event rows
28/15/1/12 with the right per-type dots, `MergedMap._dbg().on` = h/f/a, hash gains
`#layers=h|f|a`, `localStorage.tab` persists, the legend still paints all three lanes.
`#layers=` deep links still restore (no widget writes it any more).

**Complaint 2 — the side nav.** Active section now follows the scroll (probe line a quarter of
the viewport down, under the sticky phone bar; frozen during a click-jump so the marker does not
walk the page), `aria-current` on the mark; the rail reads as an indexed list (ON THIS PAGE /
Events / Scorecard / rule / About) on one 34px-2px-14px rhythm with every hairline full-bleed;
selected = the app's own alabaster + ink key-line (was a tint wash with an orange icon); hover,
:active and :focus-visible on every nav control; collapsed rail keeps accessible names
(clip-hidden label, not `display:none`) plus tooltips; phone drawer: 300px, 44px rows, slide-in +
scrim fade under `prefers-reduced-motion`, focus moves in on open and back to the hamburger on
close, Tab is trapped while it is open; sections get `scroll-margin-top: 60px` so a jump lands
below the topbar instead of under it; skip-to-content link (click intercepted — a real `#anchor`
would wipe the app hash). Review 1-3: theme is now a labelled Auto/Light/Dark segmented control
in the footer (with `setThemePref`, `cycleTheme` gone, Auto's tooltip names the resolved theme),
the week select got a visible "Week" label that hides with the control when there are no
snapshots, footer items share the rail's 22px text column (measured, all six at one left edge),
and the phone topbar carries the mark only — the wordmark is `sr-only`, so the banner keeps its
name and the H1 is said once.

**Verification (headless chromium on the box, port 8062, /opt/ewb-preview-nav):**
`nav_probe.py` in that dir runs 12 phases per viewport (env, jumps, active, keyboard, theme,
about, tabs, full, railcollapse, drawer, detail) — result for desktop+phone × light+dark:
**0 console errors, 0 warnings, no horizontal page scroll at 320/390/768/1024/1280/1440/1920**,
every jump link lands on its section (desktop top=0, phone top=60 under a 52px bar) and marks it,
Enter on a rail link jumps, skip link keeps `#map=…` and focuses `#main-content`, ✕/Esc/scrim
close the drawer and return focus to the hamburger, theme persists across reload, and an open
event sheds the section links (rail = About + settings only, no orphan caption/rule).

**Shots:** `notes/shots/pi-nav/` — `before-*` (baseline ecb4421 on :8063) vs `after-*`
(this branch on :8062), same data, same day, 1x, theme pinned in localStorage; README.md
explains each frame. Two things inside the frames are pi-colors' work, not mine (the legend
rework, and the missing chip row that is complaint 3).

**Open / your call**
1. If you would rather the phone keep the wordmark in the top bar and drop the H1, that is a
   one-line change — I judged the H1 (the document's only H1) the thing to keep visible.
2. `#type-tabs` is a survivor of complaint 3 and still uses `role="tablist"`/`role="tab"` with
   no `aria-controls` and no arrow-key navigation (APG tabs). I left the ARIA alone as out of
   scope; say the word and I'll retag it as a group of `aria-pressed` toggle buttons.
3. `globalmap.js` / `rawmap.js` still build `#raw-layers`/`#gm-chips` and are now dead weight
   (index.html loads neither). Not mine to delete; they will never reach the removed node.
4. The skip link sits over the rail head when focused (covers the brand tile). Cosmetic; can
   move to the content column if you want it out of the rail.
5. Box state I own: :8062 = this branch's preview, :8063 = a throwaway copy of the baseline
   tree at /opt/ewb-preview-nav/base used for the `before-*` shots (kill with
   `pkill -f "ewb_live.serve --port 8063"` when the review is done). — pi-nav

## orchestrator 2026-11-11T03:40Z — pi-nav review: accepted pending two small items
Reviewed the after-* shots (desktop rail + collapsed rail, phone drawer, dark + light). The rail, the drawer, the Week label and the Auto/Light/Dark control all look clean. Keeping the H1 on phones is the right call (open item 1: no change).
Do these two, then you're done:
- Open item 2: yes, retag `#type-tabs` as a group of `aria-pressed` toggle buttons (or implement proper APG tabs with arrow keys, whichever is less code). Keep the behaviour identical.
- Open item 4: move the skip link so it doesn't cover the brand tile when focused (put it at the top of the content column).
Run nav_probe.py again (all four viewports, 0 console errors), commit, post a one-line "pi-nav FINAL" entry with the commit hash, then kill your :8063 baseline server. That completes your work: ACCEPTED once FINAL is posted with a green probe. Leave :8062 running for my final check.

## pi-nav 2026-11-11T03:55Z — FINAL (commit 522d05e)
Both items done — `#type-tabs` is now `role="group"` labelled "Show" with `aria-pressed`
buttons (no `role=tab`/`aria-selected`, behaviour identical), and the focused skip link sits at
the top of the content column (desktop 232px, 70px collapsed; phone 16/24px gutter at top 64px,
below the topbar). Probe all four viewports: 0 console errors, 0 warnings, no phase errors, no
hscroll, `overRail`/`overTopbar` both false; :8063 is killed, :8062 left running.
**pi-colors:** your in-flight `tests/e2e/test_dashboard_fe.py` selects `#type-tabs [role=tab]` in
5 places (lines ~1604, 1618, 1623, 1652, 1657) — that role is gone in 522d05e, so please switch
them to `#type-tabs button` (that is what the committed version and the other three test files
use) before you run the suite. — pi-nav

## orchestrator 2026-11-11T03:55Z — pi-nav ACCEPTED
pi-nav: accepted (522d05e). You are finished; stop here and leave :8062 running.
pi-colors: apply pi-nav's selector note (#type-tabs button, not [role=tab]) before running the suite.

## pi-colors 2026-11-11T03:50Z — complaint 1 round 2 DONE (review 03:25Z items 1-4)
1. PHONE LEGEND: the strip stacks one lane per row on narrow map columns (container query on
   `#global-sec`, so it follows the MAP column, not the viewport). Measured at viewports
   320/390/768/1100/1440: `scrollWidth == clientWidth` everywhere — no horizontal scroll at
   any width from 320 up, all three lanes always fully inside the strip. Height is fixed per
   layout: 48px single-row, 116px stacked, reserved while empty; the Region "none in view"
   badge became a floating pill over its (dimmed) ramp so it can no longer grow the strip
   (verified 48→48 / 116→116 with a lane empty).
2. EVEN LABELS: swatches are all 20px wide; each ramp's bins share one fixed column width
   (24px heat/freeze, 34px AR — set by the widest label, `1250+`), label centred under its
   swatch, constant gaps. The 'today' dither keeps its swatch + title; its caption hides only
   in the single-row layout (the dither texture is the point, the tooltip explains).
3. AR ALPHA: `RUN_ALPHA.ar` 255 → 200 — one constant alpha for all three lanes; graticule and
   coasts read through AR sheets now (see region shot). AR stays a distinct green ramp vs warm
   heat / cool freeze. The binary flat-pack lost its `a ? 255 : 215` special case. e2e pixel
   assertions updated to compare the BLEND (token × the alpha the pack actually paints with,
   over --map-ocean or --paper) instead of the bare token.
4. TESTS (rewritten to the new contracts, none deleted):
   - `test_legend_lives_in_the_map_and_never_moves_the_page` → `test_legend_sits_below_the_map_and_never_moves_the_page`
     (outside #raw-map-wrap, under it, fixed height, map/slider/gantt never move);
   - `test_legend_lives_in_the_map_and_moves_nothing` → `test_legend_never_hides_a_lane_behind_a_scroll`
     (no h-scroll, lanes fully inside, at 320/390/768/1100/1440; stacked ⇒ one lane per row);
   - `test_dur_chip_labels_gradient_and_tips` → `test_show_tabs_are_the_only_layer_toggle`
     (chips gone, tabs are the widget, `#layers=` recorded, legend carries stops + long tips);
   - `test_raw_layer_toggles_and_url` rewritten tab-driven (same `#layers=` deep-link contract);
   - `test_controls_live_in_the_section_they_change` asserts the chip row is ABSENT;
   - `test_legend_adapts_to_layers_frame_and_region`: chip clicks → Show-tab clicks;
   - theme tests → `#theme-ctl button[data-pref]` (pi-nav's segmented control), type-tab
     selectors → `#type-tabs button` per pi-nav 03:52Z;
   - guardian seam-tiling scan moved to open ~108°W — the coastline overstroke above fills is
     now the design, the scan must measure tiling, not coasts;
   - `test_ar_layer_visible_on_live_frames` is data-driven now (picks a qualifying AR frame
     from the run's own data; the hard-coded Oct-3 12Z went stale this run — that AR qualifies
     Oct-4 06Z);
   - `test_ar_ramp_paints_green_pixels` drove a frame PAST the fixture cutoff (nothing paints
     there) — fails on baseline ecb4421 too; now drives 2025110606.
   SUITE ON THE BOX vs :8061 — e2e: **130 passed, 3 skipped, 0 failed**
   (`EWB_LIVE_URL=http://127.0.0.1:8061 pytest -m e2e`). Unit/default: 243 passed, 4 skipped,
   14 failed — all 14 are pipeline tests (ar_filter/events_unit/raw_exceedance/seam) and fail
   IDENTICALLY on baseline ecb4421 in the same venv (upstream env issue; this branch never
   touched ewb_live/).
Commits this round: 6873e35 (AR alpha + e2e contracts), 87e2440 (none-pill + green-pixel test)
+ this shots/channel commit. NB: my styles.css legend edits went into the tree while pi-nav's
522d05e committed dashboard/styles.css wholesale, so 522d05e's diff includes them (content is
as reviewed; `git log -p dashboard/styles.css` shows my block authored across ca5bc93/6873e35/
87e2440-adjacent edits). Shots: notes/shots/pi-colors/shot-*-{after2}.png + shot-region-pill-after2.png.
Preview :8061 is current. Waiting for review. — pi-colors

## orchestrator 2026-11-11T04:15Z — pi-colors round 2 review: two items, then ACCEPTED
The phone stack, even label columns, AR alpha and test work are all good. The suite result (e2e 130/3/0, and the 14 unit failures pre-existing on baseline) is accepted.
1. Desktop single-row legend: the three groups now run into each other. "…7+ today FREEZE" and "…3+ today kg m⁻¹s⁻¹" read as one continuous string (shot-desktop-1440-after2.png). Add clear separation between groups: a larger gap (≥ 20px) plus a thin vertical hairline divider in the border colour. Make sure each group's unit caption ("days < P15", "kg m⁻¹ s⁻¹") sits under its own title and never touches the previous group's last label. It must still fit at 1440 without scroll. If it doesn't, drop the 'today' caption first, as you already do.
2. Dark theme lightness direction: in dark mode the most extreme bins (heat 7+, AR 1250+) are the DARKEST, so they recede into the dark map, which is backwards on a dark background. In the dark theme only, reverse the lightness ramp (low bins dim and closer to the background, high bins brightest and most saturated) for heat, freeze and AR. Light theme stays as it is. Keep legend swatches = painted colours, and update any e2e assertion that depends on it.
Re-shoot desktop light + dark and phone dark as after3, rerun `pytest -m e2e` against :8061, and post "pi-colors FINAL" with the counts and commits. If it's green and both items are visibly fixed, that's ACCEPTED.

## pi-colors 2026-11-11T04:27Z — pi-colors FINAL (review 04:15Z items 1-2)
1. ROW-STRIP SEPARATION: groups sit 20px apart with a 1px hairline divider in
   `--border` centred in each gap (`.lg-group + .lg-group::before`, absolutely
   positioned — zero height/width cost). Bin columns 24→22px keep the three lanes
   + dividers inside the 696px map column at 1440 without scroll (measured
   scrollWidth == clientWidth == 696; the 'today' caption stays dropped in row
   mode as before, its swatch + tooltip carry it). Unit captions sit under their
   own titles, clear of the previous lane's last label.
2. DARK DIRECTION: dark `--dur-*` ramps re-sampled in the OPPOSITE lightness
   direction (ColorBrewer descending): heat L* 31→88 (deep crimson → gold),
   freeze 26→84 (navy → sky), AR 30→87 (dark green → pale green) — equal L*
   steps, low bins near the dark background, high bins brightest and most
   saturated. Light theme untouched. Legend swatches read the same tokens as the
   canvas, so they stay painted colours; the only e2e dependency was the lane
   tips ('Darkest = 7+ days') — now 'darkest in light, brightest in dark', with
   the tip assertions updated in the same commit.
Shots after3 (desktop light+dark, phone light+dark) in notes/shots/pi-colors/.
Suite rerun against :8061: `pytest -m e2e` → 130 passed, 3 skipped, 0 failed
(unit/default suite unchanged: 243 passed / 4 skipped / 14 pre-existing baseline
env failures, untouched by this branch).
Commits: d39f1d6 (this) + 58548b5's channel/shots trail; preview :8061 current.
— pi-colors

## orchestrator 2026-11-11T04:35Z — pi-colors ACCEPTED
pi-colors: accepted (d39f1d6). You are finished; stop here and leave :8061 running.

## orchestrator 2026-11-11T11:16Z — DEPLOYED live
Owner said "Deploy". Copied dashboard/{index.html,app.js,styles.css,mergedmap.js} + tests/e2e/{test_ar_ramp_simple,test_dashboard_fe,test_guardian_fe}.py into /opt/ewb-live (checksums match HEAD). Live tree was unchanged since the ecb4421 snapshot, so nothing was clobbered. No service restart needed (static files, no-cache headers).
Verified: e2e against live :8050 → 130 passed, 3 skipped; https://ewb.espr.ai serves the new markup.
Rollback: ssh root@192.168.0.121 'cd /opt/ewb-live && tar -xf backups/pre-make-nicer-20261111T111609Z/files.tar'
Preview servers :8061/:8062 stopped (dirs /opt/ewb-preview-{colors,nav} left in place).
