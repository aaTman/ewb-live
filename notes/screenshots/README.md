# /dev/ review-site screenshots — 2026-16:00-ish 2026-11-17 (integrator)

Produced by `scripts/dev_review_shots.py --url http://192.168.0.121:8051`
(Playwright: every tab by click, first event of each lane opened, About dialog;
desktop 1440x900 + phone 390x844, light + dark). **0 console errors.**

`report.json` is the machine-readable result (per-tab row counts, the UI's own
count label, the honest empty/not-scored notes, and the event-detail probe:
`tvTrack` = TrackViz rendered a cyclone track, `vizTags` = what the detail viz
host contains).

Committed here: the desktop tab views, the About dialog and the cyclone/track
event pages. The full PNG set (42 MB, incl. phone + dark variants) stayed
out of git: regenerate with the command above.

Verified from this pass:
* tabs: heat_wave 15, freeze 5, cold_snap 6, atmospheric_river 13,
  tropical_cyclone 44, midlat_cyclone 0 (lane defect — see
  notes/channel_new_events.md 22:55), flood 40.
* TrackViz renders for tropical_cyclone (`tvTrack: true`, 4 viz nodes:
  head/map/legend/note); EventViz (`div.ev3-root`) renders for heat/freeze.
* unscored lanes say why: "Not scored — 44 storms tracked from IBTrACS best
  tracks…", "Not scored — 40 floods tracked from IMERG…", and the new generic
  "Not scored in this data release — 6 published events…" for cold_snap (no
  score rows in a build that re-published live scores).
* open item: cold_snap / AR / flood event pages show an empty viz host
  (`div.lane-empty`) — the raster lanes were still being rebuilt by
  `viz --all --force` at capture time, and the flood/mlc lanes have no per-event
  viz builder at all, so their detail panel needs a sentence instead of a blank.
