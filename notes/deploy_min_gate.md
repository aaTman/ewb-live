# Deploy: consecutive-day paint gate (heat 3 / freeze 1) — 2026-11-10 13:01Z

## Why the bug was still live
The gate was implemented and committed **in this worktree only** (`9d5bd32`,
`44a9bbd`). The deploy source (`../dogfish`) and the box
(`root@192.168.0.121:/opt/ewb-live`) still carried the pre-fix renderer
(`mergedmap.js` md5 `06c3363c…`, zero occurrences of `minDays`), so the live map
kept painting 1–2 day heat points and flashing them at 00Z.

## What was ported (dogfish + box, 13:01:28Z)
`dashboard/mergedmap.js` was re-derived from **dogfish's current file** (it has
the AR IVT-ramp layer this branch lacks — a blind copy of the branch file would
have reverted it), then the same gate hunks applied:

* `LAYERS[]` gains `min` (heat 3, freeze 1); `minDays(L)` prefers the published
  `index.dur.min` and falls back to the built-in.
* `durV2` accepts any rev string starting with `v2`.
* paint loop: `done = v2 ? r & 127 : r`; `done < minDays(L) → continue`;
  the provisional bit is a 2px checkerboard to the next shade on an
  already-painted cell — it never paints a cell on its own.
* legend gradient starts at the first paintable bin (`rampFrom`, clamped).
* `app.js` criteria text, `index.html` map caption, `exceedance.py`
  `dur_block()` (publishes `dur.min.heat = MIN_CONSECUTIVE_DAYS` read from
  upstream, `dur.min.freeze = FREEZE_PAINT_MIN = 1`) + docstrings.

Deployed by **narrow scp of those 4 files** (not `ops/remote.sh sync`, which
would have pushed other lanes' unrelated WIP). dogfish carries the same change
uncommitted, per the lane convention; this branch is the durable record.

## Verification
* dogfish suites on the patched tree: `tests/test_raw_exceedance.py` 12 passed;
  `tests/e2e/test_dashboard_fe.py` **42 passed** (incl. the AR IVT legend and
  duration-chip contract tests).
* real published frames, same day (2026-11-01), rendered locally: pre-fix JS
  painted heat 20454/16658/14522/13624 px across 00/06/12/18Z (**1.50x** 00Z
  excess); gated JS 3759/3759/3756/3759 (**1.00x**).
* live site after deploy (`/tmp/livecheck.py`, https://ewb.espr.ai, day
  2026-11-02): heat 1690/1690/1689/1690 px = **1.001x**, no page errors; freeze
  865/869/868/868 (moves by design — day-1 paint floor + provisional bit).
* public `mergedmap.js` md5 = dogfish = box = `67e3f36d…`.

## Rollback
* box: `/root/rollback-20261110T130128Z/{dashboard,ewb_live/viz}` → copy back.
* local copies: `notes/rollback_v2/` (box + dogfish pre-deploy md5s:
  `06c3363c…` mergedmap, `9dec6bd6…` app, `950a5c6f…` index.html,
  `78dc166b…` exceedance).

## Follow-up
`index.json` still has `dur: {rev: v2, seed}` without `min`, so the renderer is
running on its built-in defaults (identical numbers). The next exceedance build
picks up `dur_block()` and publishes `dur.min`; nothing needs to run now.
