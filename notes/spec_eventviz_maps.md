# Spec: eventviz.js v3 — real-map event visuals (owner feedback 2026-11-06)

Owner verdict on AR detail: "I have no idea where this atmospheric river is."
Align hard with Neil Gordon's Weather Explorer mapping (MIT,
github.com/tehoro/weather-explorer-web): real geographic basemap, smooth
semi-transparent field overlay, context that makes the continent obvious.

## Hard requirements
1. Every panel (observed + each model-at-lead) is a **Leaflet map** (already
   vendored: dashboard/vendor/leaflet.js + leaflet.css; app.js loads them).
   Theme: dark → CARTO dark tiles `https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png`
   subdomains abcd, attribution '&copy; OpenStreetMap contributors &copy; CARTO';
   light → CARTO light_all. CARTO not reachable → fall back to OSM standard
   tiles (tile error event swaps layer once). No API keys.
2. Field overlay: draw the grid to an offscreen canvas at a 2× smoothed
   upsample (bilinear, then browser smoothing on the image),
   `L.imageOverlay(dataURL, bounds, {opacity: 0.72, interactive: false})`.
   bounds = grid extent (cell centres + half cell). Missing/null cells →
   transparent. Ramps stay as today (heat RdYlBu-style, freeze Blues, AR
   magma); heat/freeze ramp maps consec 1..max; transparent below 1.
3. View: fit the EVENT BOX padded by ×1.9 (so the continent reads), maxZoom 6,
   world copy. Event box drawn as dashed `L.rectangle`, weight 1, dash '4 3'.
4. Sync: all maps of one event share view — on user `moveend`, copy bounds to
   all sibling maps (guard flag to avoid echo). Wheel/zoom on any panel moves
   all. Also a small `[⤢]` per-panel button to zoom that map fullscreen
   (position:fixed overlay; Esc closes).
5. Graticule: dashed 5° lat/lon lines (weight 0.5, opacity 0.25) across the
   current view, redrawn on moveend.
6. AR overlap: IVT ramp overlay + observed mask **contour** (white line,
   weight 1.5) + that model's forecast mask **contour** (accent dashed line).
   NO recoloring of IVT for hit/miss/FA. A per-event toggle chip
   `[show overlap fill]` adds alpha-0.18 fills instead (default off).
   Contours: marching squares on the 0/1 mask (adapt the idea; MIT credit in
   the file header).
7. Colorbar: one shared horizontal colorbar per event viz under the controls
   (ramp + numeric ticks + units: days / kg m⁻¹ s⁻¹), plus threshold value
   note from viz.threshold_note (italic, ink-muted) for heat/freeze.
8. Lead selector + day stepper stay. 'no data at this lead' tiles are HIDDEN;
   instead one line under the panel row: `no forecast at T+24 h: name(reason),
   name(reason) …` (reason = viz.models[m][lead].status minus 'ok'), plus
   models fully absent from viz.models listed as `unavailable (not built)`.
9. Panels: CSS `repeat(auto-fill, minmax(340px, 1fr))`, map height 300px
   (observed panel 340px); no blank gap: observed panel is first cell of the
   grid, nothing under it. Panel header: model name + `T+{lead} h` chip +
   area/max_consec stat chip.
10. API stays EXACTLY: `window.EventViz.load(id)` (Promise<viz|null>, v1+v2
    schemas, u8 unpack already written) and `EventViz.render(host, viz, ev)`;
    add `EventViz.setLead?` NO — lead is internal state; render() re-call on
    theme flip must still work (idempotent teardown: remove host content,
    invalidateSize on all created maps).
11. Palette: respect DESIGN-OWB tokens when present (CSS vars --paper,
    --border, --ink-secondary, --accent etc.; light theme) — read via
    getComputedStyle; fall back to current css() helper values.

## Constraints
- Vanilla JS, no new libraries. `node --check` must pass.
- Keep u8 unpackViz/v1 compat code exactly as is.
- Owns ONLY dashboard/eventviz.js. Do not touch app.js/index.html/styles.css.
- Maps must be created lazily (render) and removed on re-render
  (m.remove() for each) to avoid leaks.
- Leaflet usage inside the IIFE: guard `if (!window.L) → simple canvas
  fallback` (current renderers) so the module degrades without Leaflet.

## Test loop (offline, allowed on orca)
Serve repo root: .venv/bin/python -m ewb_live.serve --port 8074 (static;
already serves /geo/ + dashboard + data/results nested events).
Existing v2 assets on box — copy back first is NOT your job; test with
data/results/events/31b6bf4a1a44 (heat, v1), d7f17e199dbf (AR, v1),
a5e694627968 (freeze, v1) which exist locally.
Playwright (.venv): open http://127.0.0.1:8074/?event=<id> (app.js deep link
renders detail + viz), assert: ≥1 canvas with painted pixels inside
#detail-viz, basemap tile imgs requested (any of tile.openstreetmap.org /
cartocdn), .leaflet-container count == panel count + 1 (app map), no console
errors, model-with-status tiles hidden when status!=ok (mock by editing a
copy of viz.json under data/results/events/zz-test/ — create index entry not
needed; app fetches by event id only).
Screenshots to notes/screenshots/eventviz-v3-<event>-{dark,light}.png.
