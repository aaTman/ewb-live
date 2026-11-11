# Distinct task (owner, 2026-11-09): event detail view → ONE map card with model overlays + day slider

Owners: design (Hermes, role 'design-ev') specs; frontend (Hermes, role 'frontend-ev') implements.
Coordinate in notes/channel_event_view.md (append-only, prefix entries `## [role] YYYY-MM-DD HH:MM UTC — title`).
Tell the guardians in notes/channel_guardians.md which files you touch.

## Owner's words
"The individual event view spawns a new map card whenever a new model is selected. This should be changed to
only show up in one card and allow users to change the color and opacity of the model representation (e.g., if a
user wants fourcastnet and pangu on top of IFS HRES but fourcastnet is orange and 50% opacity, and pangu is pink and
20% opacity). It should also mimic the approach of the main map on the front page, showing consecutive days with a
slider instead of whatever is currently being shown (which is confusing, as it stands right now)."

Current state (owner screenshot): one "Observed · ERA5" card plus one extra card per selected model (ifs_hres,
graphcast_gfs, ...), each with chips "T+24 h", "max 7d" and footers such as "max 7d · Δ +0 d vs obs · footprint 5904
(obs 5127)". The owner finds the "max 7d" representation confusing. Code: dashboard/eventviz.js (renderMaps,
makePanel, buildModelPanels, picker), dashboard/styles.css.

## Requirements
1. ONE map card per event. Observed (ERA5) is the base layer. Every selected model is drawn as an overlay layer on the
   same Leaflet map, not as a separate card. Keep fullscreen, zoom, event box and graticule.
2. Per-layer controls, including observed: visibility toggle, color picker and opacity slider (0–100 %). Defaults: give
   each model a distinct color from the existing scorecard palette (modelBarColor) and an opacity that stays readable
   when 2–4 layers stack, e.g. 50 %. Controls must be keyboard-accessible and labeled, and work in both themes.
   The legend shows each layer with its current color and opacity.
3. Persist layer state (models, colors, opacities, visibility) in the URL hash so links and Back/Forward reproduce
   the view. Existing event deep links must still open.
4. Replace the "max 7d" snapshot with the FRONT-PAGE APPROACH: per-cell consecutive-day duration (graded ramp; same
   definition, ramp tokens --dur-heat-1..5 / --dur-freeze-1..5 and legend wording as dashboard/mergedmap.js, see
   notes/channel_consecutive_days.md "## Contract"). Add a time slider over the event window with play/pause,
   step buttons and a "Valid time" readout, styled like the front-page slider (#raw-slider). For models, the slider
   moves through valid time at the selected lead. Keep the lead selector. Make sure the obs-vs-model relationship
   (same valid time) is obvious. Overlaid models need a readable rendering, e.g. the model's chosen color with duration
   mapped to alpha/lightness, or outline contours. design decides this and documents it in the channel.
   ARs: use the equivalent per-step mask. design decides whether duration applies there.
5. Data: design checks whether the per-event viz assets already contain per-step (daily or 6-hourly) obs and model
   grids. If they don't, spec the minimal asset change. Builds run ONLY on the EWB LXC via ops/remote.sh, never on orca.
   Keep payloads small (lazy-load per step or pack like the front-page .run frames).
6. Remove the confusing footer text. If a compact per-model summary (footprint vs obs, timing offset) is still useful,
   show it once in the layer list, in plain words.
7. Tests (Playwright, tests/e2e): a single card for N selected models; color/opacity changes repaint (pixel probe);
   URL round-trip; slider/play and readout; both themes; mobile 390px; no console errors. All existing e2e green.
8. Deploy with ops/remote.sh sync and verify byte parity on the box (see skill ewb-live-dashboard-viz). Post
   `## [time] frontend-ev: EVENT VIEW READY` with desktop + mobile screenshots in notes/screenshots/ once design has QA'd.

## Rules
- Repo: /home/orca/orca/workspaces/ewb-live/dogfish. Read SPEC.md, notes/kt_frontend.md, notes/kt_design.md,
  notes/owner_refs/*, notes/channel_consecutive_days.md first.
- NEVER git commit, push or `git add`. Don't touch unrelated uncommitted changes. Other agents are editing
  dashboard/mergedmap.js and dashboard/styles.css right now. Don't edit mergedmap.js. In styles.css, add or modify only
  event-view rules (.ev3-*), and re-read the file right before each edit.
- All EWB compute ONLY on root@192.168.0.121 via ops/remote.sh.
- Public site hygiene per SPEC: run scripts/public_scrub_check.sh before declaring done.
- Context handoff: at 90% context write notes/handoff_<role>_<n>.md and launch a successor:
  orca agent launch --agent pi --surface terminal --prompt "You are '<role>' (successor). Read notes/briefs/event_view_overlay.md, then notes/handoff_<role>_<n>.md, and continue." --json
