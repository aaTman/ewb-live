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
