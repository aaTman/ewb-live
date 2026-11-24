# Task (owner, 2026-11-09): regional breakdown of the scorecard + region-aware main map

Worktree: /home/orca/orca/workspaces/ewb-live/regional-breakdowns (branch aaTman/regional-breakdowns).
Work ONLY in this worktree. Do not edit files in ../dogfish or any other worktree; other agents are working there.
Commit 5d91c6d is a snapshot of dogfish's code and is the baseline. Make small commits on top of it.

Owners (both Hermes):
- 'regions-data': region definitions, assigning events to regions, per-region scorecard aggregates (ewb_live/*, data contract).
- 'regions-frontend': the Region dropdown, scorecard re-render, and moving the main map to the region (dashboard/*).
Coordinate in notes/channel_regions.md. It is append-only. Start each entry with `## [role] YYYY-MM-DD HH:MM UTC — title`.
When you finish, append a `## [role] ... — DONE` entry: what changed, files, commits, tests run, open issues.

## Owner's words
"I want to add in a breakdown by region that mimics how https://owb.brightband.com/ does it with a dropdown
[screenshot: Region dropdown left of Baseline and Date range, with Global, Tropics, Northern Hemisphere, Southern
Hemisphere, North America, Europe]. But also, to have that dropdown change the map view on the main page when
selected to the appropriate region."

## Context
- The scorecard scores EVENTS (heat waves, freezes, atmospheric rivers), each with a bbox. It does not score gridded
  global fields. A regional breakdown therefore means: assign events to regions, then re-aggregate the scorecard over
  the events in the selected region.
- Region definitions follow WeatherBench 2 / OWB. Keep ONE source of truth, e.g. dashboard/regions.json, read by both
  Python and JS:
    global        lat -90..90,   lon -180..180
    tropics       lat -20..20
    northern_hemisphere (extra-tropics) lat 20..90
    southern_hemisphere (extra-tropics) lat -90..-20
    north_america lat 25..60,    lon -120..-75
    europe        lat 35..75,    lon -12.5..42.5
  Labels match OWB: Global, Tropics, Northern Hemisphere, Southern Hemisphere, North America, Europe.
- Event-to-region rule: regions-data decides and documents it (e.g. by event-box centroid, or by overlap fraction of
  the event's footprint). Note the edge cases: events that cross region edges, events that cross the dateline, and
  NH/SH beginning at 20° rather than 0°. Events may belong to several regions; Global holds all of them.
- Some regions × event types will have few or no events. The UI must show this honestly, e.g. "n = 3 events" or "No
  events in this region", not an empty or misleading table. regions-data puts the event counts in the contract.

## Requirements
1. regions-data: find where the scorecard is aggregated (Python in ewb_live/viz or weekly, or client-side in
   dashboard/app.js). If per-event scores already reach the browser, client-side aggregation may be enough. Then the
   job is the region file, the assignment rule, and a shared helper/test. Otherwise emit per-region aggregates in the
   results JSON, including weekly history snapshots, and keep the existing Global output byte-compatible.
   Post the DATA CONTRACT (file names, JSON shape, the assignment rule) in the channel EARLY, before polishing, so
   frontend can build against it. Add unit tests.
2. regions-frontend: add a "Region" dropdown that looks like OWB's (label above, select below, the same control style
   as the existing week picker or baseline controls) in the scorecard controls. Changing it:
   a. re-renders the scorecard for that region, showing event counts and the empty-region state;
   b. moves the main page map to the region's extent, both the merged global map (dashboard/mergedmap.js
      fitProjection) and any Leaflet map on the home page (dashboard/app.js). Global restores the current default
      view. Optionally dim or outline the area outside the region; nice to have, not required.
   c. persists in the URL (hash or query, matching the existing convention), so deep links and Back/Forward work.
   Keyboard-accessible, works in both themes and on mobile widths. Do NOT edit dashboard/eventviz.js; another agent
   is rewriting it in dogfish.
   Add or extend Playwright e2e tests (tests/e2e/test_dashboard_fe.py). Save screenshots of each region to
   notes/screenshots/regions-*.png.
3. Both: keep existing tests passing (`uv run pytest -q` for unit tests; e2e as already set up).

## Compute rules (strict)
- Never run pipelines, scoring, viz builds or other heavy jobs on this machine (orca). Use the EWB LXC
  (ssh root@192.168.0.121) only.
- ops/remote.sh syncs to /opt/ewb-live by default, which is the LIVE deployment. From this worktree ALWAYS set
  EWB_LIVE_REMOTE_DIR=/opt/ewb-live/_branches/regional-breakdowns, so you never overwrite live code. Seed that dir's
  data/ by copying from /opt/ewb-live/data on the box if needed (cp, not mv), and never write into /opt/ewb-live/data.
  Use `ops/remote.sh pull-results` with the same env var to bring results back.
- Serving the static dashboard locally for Playwright is fine (it is lightweight).
- Do not deploy, push, or restart any systemd unit.

## Context handoff
At 90% context, write notes/handoff_<role>_<n>.md (goal, state, done, in progress, next steps, gotchas). Then launch a
successor with: orca agent launch --agent hermes --surface terminal --worktree path:<this worktree> --prompt "<read
brief + handoff>" --json. Successors must be Hermes.
