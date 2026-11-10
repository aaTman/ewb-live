# ewb.espr.ai redesign — working state (fable-review)

Owner request 2026-11-10: make the site significantly more intuitive (controls
must sit on what they change); take over / pause dashboard-worktree agents.

## Task list
- [x] 1. Coordination: orchestrator ack'd (no frontend successor; dashboard/* frozen
      for design/ar-ramp/ewb); opencode frontend (term_7365c1a0-…) told to stop.
- [x] 2. Explore area: Equal Earth map + event list side by side; type filter above
      both; Leaflet catalog map removed.
- [x] 3. Type filter drives map layers + boxes + timeline + list + scorecard
      (scorecard echoes "X only · show all types").
- [x] 4. List rows: "Heat wave · Iran", dates, days, peak; hover = outline on
      map + timeline; "on map now"; map selection marks the row.
- [x] 5. Timeline card: play / step / current time / speed, scrubber, lanes.
- [x] 6. Scorecard toolbar (models, lead time, metrics) inside the scorecard.
- [x] 7. Detail view: "← All events", home-only chrome hidden.
- [x] 8. Rebased on deployed dur-rev-v2 bytes; ported opencode's verdict strip +
      legend scale.
- [x] 9. Full e2e green on local preview (scripts/dev_preview.py, :8061;
      EWB_LIVE_URL=http://127.0.0.1:8061 EWB_E2E_BROWSERS=chromium pytest -m e2e tests/e2e).
- [x] 10. Copy dashboard/{index.html,app.js,styles.css,mergedmap.js} + tests/e2e/*.py
      into dogfish (check dogfish files still == my merge base first:
      app.js 138ba2ba, styles 1fed06f7, mergedmap 832d38fe).
- [x] 11. Deploy: single-file scp to root@192.168.0.121:/opt/ewb-live/dashboard/
      (keep rollback copies), verify live + guardian AR test.
- [x] 12. Post REDESIGN LANDED in dogfish notes/channel_design_frontend.md; report to owner.

## Leftover lane items (not part of the redesign; from handoffs)
getFrame .run/.ivt byteLength check; bridged-AR lighter fill; IVT_BINS from
index.ivt.bins; AR hover swallowed inside event boxes; dE of --dur-ar-4/5.

REDESIGN LANDED 2026-11-10 04:25Z; owner informed in the session.
