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
