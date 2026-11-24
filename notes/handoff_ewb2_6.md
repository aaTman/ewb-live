# Handoff — ewb successor 5 → 6 (2026-11-09 ~11:05Z)

ALL OPEN ITEMS CLOSED. Scoring lane and raw layer both verified green.

- Scores: 28 events / 26,565 rows; e86c13f47bc0 re-scored after purge
  (purge_event_parts.py now drops scores.parquet rows — the `done` set is
  row-derived, pipeline.py:600-610). AR@48 n=12. science-review §I FINAL
  PASS, no open items (their LOO attribution corrected; ours: e86c).
- Raw layer: LAYER_REV v5, 336 frames rebuilt ~09-11Z (v4 was contaminated
  by the 03:51Z durbackfill collision: 51 tracks inside the TC buffer).
  Gate: `./ops/remote.sh run python scripts/review/d2_pixel_audit.py` →
  exit 0 "D2 CLEAN: min 503.4 km". (v4 measured 0.0 km (LALA) by the inline
  predecessor of that script before rebuild; v4 frames no longer exist.)
- Suite: 205 passed (offline). Uncommitted local work awaits orchestrator
  commit (never commit here).
- Known minor wart (not fixed): zero-row events re-spawn jobs every run
  (no parts written → pending forever). Cosmetic cost only.
- test_tc_screen.py still asserts 250.0 km (harmless; screen is 500).
- Handoff conventions: SPEC.md ground; ops/remote.sh only; channels in
  notes/channel_*.md.
