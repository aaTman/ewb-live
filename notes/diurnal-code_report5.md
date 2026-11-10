# Round 5 report — dur rev v2 deploy (2026-11-10)

## What shipped
Commit `29609e9` (branch `aaTman/diurnal-cycle-check`): heat/freeze global map paints
**consecutive COMPLETED UTC days** (`.run` sidecar rev v2: low 7 bits = completed-day
streak capped at RUN_CAP=127, bit 7 = today's provisional run). Kills the diurnal
sawtooth where mid-day frames reset or inflated the streak count.

## Deploy path (who did what)
- deploy-pi executed the deploy 01:20–02:51 UTC (its own channel entries): saved rollback
  snapshots, 3-way merged the six files into **dogfish's working copy** (no commit, no
  push), `ops/remote.sh sync`, force-rebuilt the exceedance lane on ewb
  (`viz.exceedance --force`, log `logs/durv2-rebuild.log`, 336 frames indexed), and fixed
  one real crash found during the rebuild: `_run_day` raised on `seed=None` for
  never-satisfied cells — now returns 0 (box md5 `5a4822f3`, same fix applied to
  dogfish and this worktree).
- This session was re-launched on round 5 after the handoff, found the deploy already
  applied, did **not** re-merge or re-sync, and independently verified (below).

## Verification (independent, on ewb + public URL)
- `index.json` local **and** public (`https://ewb.espr.ai/data/results/raw_exceedance/index.json`):
  `dur = {rev: v2, seed: 2026072500}`, `layer_rev = v7` preserved, 336 frames.
- Served JS: `curl https://ewb.espr.ai/mergedmap.js` md5 == dogfish working copy
  (`832d38fe…`). All six lane files on ewb == dogfish (remote.sh run rsyncs code each run).
- **Invariant on real data** (the acceptance check): for all 84 mirror days, the
  completed-field (`r & 0x7F`) painted-cell count is identical across the day's
  00/06/12/18Z frames — heat and freeze, 0 violations. Raw `r != 0` counts vary within a
  day only through the provisional bit (today's partial rule decaying as hours fail) —
  by design; the JS dithers prov cells.
- Tests on ewb: `pytest tests/test_raw_exceedance.py tests/test_viz_unit.py` → 20 passed.
  `node --check` clean on mergedmap.js + app.js. (`test_dashboard_fe.py` is an untracked
  local FE harness, excluded from sync — not runnable on the box; JS parity md5 used instead.)
- Build finished; no `viz.exceedance` process left; no `ewb-live-*` unit mid-run at sync time.

## Agent coordination
- Pause targets from deploy-pi's round: term_5202a158, term_7ad6ac24, term_e87b37a0,
  term_bd4274e6. This session posted **done** to the three still-live terminals
  (term_e87b37a0 no longer exists). Summary appended to `notes/channel_diurnal.md`.
- term_55602a39 / term_b61ce383 (original instruction targets) no longer exist.
- succ-7's design gap (builder could paint v2 bins over v1 sidecars): not live anymore —
  the force rebuild rewrote every `.run` to v2, so no mixed state exists; the rev guard
  stays queued in the design lane as defense-in-depth.

## Rollback recipe
Snapshots (pre-merge dogfish state, md5-verified against deploy-pi's 01:20 post) in
`notes/rollback_v1/` (mirror layout under `dashboard/ ewb_live/viz/ tests/`):

| file | md5 |
|---|---|
| dashboard/mergedmap.js | 3fee88a4683fd4454bdef164b43b635d |
| dashboard/app.js | 181cc0db0b7282c78d8b393fe3e981ee |
| dashboard/index.html | 1a79a169b89f40274ca29caf8445334a |
| dashboard/styles.css | 6d94e4ad0512e987229eb4b9f8568664 |
| ewb_live/viz/exceedance.py | 7432aca7c5e2a7e19158b327564caa82 |
| tests/test_raw_exceedance.py | bdbf26928829e09ea2582abb848f4146 |

1. Copy the six files over their dogfish paths (do not commit; other agents own the tree).
   Caveat: these snapshots predate the `_run_day(None)` crash fix — if rolling back the
   builder, re-apply that one-line guard (`if seed is None: return 0`) or the rebuild crashes.
2. `bash /home/orca/orca/workspaces/ewb-live/dogfish/ops/remote.sh sync`
3. Rebuild sidecars back to v1: `bash ops/remote.sh bg rebuild-exceedance-v1 \
   .venv/bin/python -m ewb_live.viz.exceedance --force` (logged under logs/).
4. Confirm `index.json` dur.rev is gone/v1 and JS md5 reverts; the v1 sidecars must be
   regenerated together with the v1 JS (mixed revs paint wrong bins).

## Constraints honored
No git commit or push in dogfish; climatology and event detection untouched; no other
projects on ewb touched; sidecar regeneration done via remote.sh bg (logged).
