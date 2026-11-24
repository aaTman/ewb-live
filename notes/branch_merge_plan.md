# Branch convergence plan — everything live, one source of truth (2026-11-10, deploy-pi)

Goal: every branch's work present on https://ewb.espr.ai, with the deployed
state reproducible from exactly one git branch (`aaTman/live-extreme-weather-
dashboard` in the dogfish worktree). Execution owner: deploy-pi or a successor
pi; decision gates call out the human/agent owners.

## State of record (verified 2026-11-10 ~12:30Z)

Deploy model: `ops/remote.sh` rsyncs the tree it is *run from* to
`ewb:/opt/ewb-live` (no `--delete`; data/ + logs/ never synced). Multiple
worktrees syncing = last-writer-wins; this is how the 05:01Z regions deploy
was silently clobbered (~11:14Z) and why boxes accumulate orphan files.

| Branch | Position | Content in live (dogfish HEAD `2a4d1c0`)? | Action |
|---|---|---|---|
| `aaTman/live-extreme-weather-dashboard` | `2a4d1c0` = deployed code | IS the live state | source of truth |
| `aaTman/regions-integration` | merged via `2a4d1c0` | yes (live side was newer) | retire |
| `aaTman/fable-review` | ancestor of merged branch | yes | retire |
| `aaTman/regional-breakdowns` (old) | 12 commits "ahead" | content-subsumed: its unique fixes (serve 204, `--map-dim` veil + singular count, stale-`#region=` drop) verified present in live `serve.py`/`mergedmap.js`/`app.js` | port-review only, then retire (do NOT git-merge; superseded history) |
| `aaTman/diurnal-cycle-check` | tip `44a9bbd`, ACTIVE (moved during this review: `9d5bd32` min-gate, then "freeze from day 1, heat 3-day gate") | dur rev v2 live (content, via deploy); the min-gate commits are NOT live | **GATE A** below, then merge |
| `aaTman/bug-catcher`, `aaTman/improve-individual-event-view` | at `f4c87a4` (initial), clean | nothing of their own | re-point at HEAD (or delete/recreate) when their owners resume |
| `master` (main project worktree) | 7 behind dogfish | n/a | fast-forward dogfish→master at the end |
| `backup/pre-squash-20261108` | backup | — | keep as-is, untouched |

Dirty worktrees (uncommitted work to capture, Phase 1): dogfish (~17 files,
live agents editing right now), `regional-breakdowns` (16 files incl.
`tests/test_pipeline_complete.py` — orphan that broke box pytest collection),
`diurnal-cycle-check` (untracked notes/reports only; its exceedance.py edit
was committed upstream).

Box-only artifacts (never `rsync --delete`!): `_branches/regional-breakdowns/`
(parallel deploy tree), `rollback_pre_regions*/`, `rollback_pre_redesign*/`
(agents' box-side rollback copies), `dashboard/{regions.json,geo,tools,vendor,
favicon.svg,og-image.png,robots.txt}` (generated/asset files not in any tree).

## Phase 0 — freeze (15 min)

1. Post the freeze window on all channels + `orca terminal send` to every
   live agent: for ~45 min, **no `ops/remote.sh sync|run|bg` from any worktree
   except dogfish, and no edits to the six shared files** while a merge runs.
2. Quiet window on the box: no `ewb-live-weekly` (Mon 07:00Z) / `ewb-live-store`
   (05:30Z) / pipeline units active; no `ewb_live.viz.exceedance` running
   (`systemctl list-units 'ewb-live-*'; pgrep -af exceedance`).

## Phase 1 — capture every worktree (nothing stays uncommitted)

3. dogfish: `git add -A && git commit` = fresh checkpoint of the live tree
   (agents may keep editing; note the md5s of the 6 shared files first).
4. regional-breakdowns worktree: commit its 16 dirty files on
   `aaTman/regions-integration` (or a `wip/regions-leftovers` branch) with a
   message naming deploy-pi capture. Do not merge yet — review first:
   `git diff HEAD..wip-branch -- <file>` per file; anything genuinely new
   (beyond the subsumed content above) cherry-picks; the rest is dropped when
   the branch retires.
5. diurnal-cycle-check worktree: commit untracked notes/reports on its branch.
   Its code is already in commits (`9d5bd32`, `44a9bbd`).

## Phase 2 — decision gate A: the 3-day paint gate (OWNER: user + diurnal/design)

`44a9bbd`/`9d5bd32` change what the map paints (heat only from day ≥
`dur.min`=3, freeze from day 1, `dur.min` published in index.json from the
detector's own `MIN_CONSECUTIVE_DAYS`; dither only on qualifying cells;
v1/v2 fallback handled). Encoding of `.run` bytes is UNCHANGED — **no lane
rebuild needed**, only an index.json touch (next incremental exceedance run,
~15 min AR pass, or the weekly tail does it free).

- A1 approve → Phase 3 merges it AND the live look changes (this supersedes
  the "paint from 1+" behavior deployed 02:48Z — user must confirm).
- A2 hold → merge the branch but resolve shared-file conflicts to the live
  (ours) side so live behavior is untouched; min-gate stays branch-only for
  its owner to land later. Default if no answer within 24 h: A2 (live wins),
  recorded on the channel.

## Phase 3 — merge diurnal-cycle-check into dogfish (the only real merge)

6. `git merge --no-ff aaTman/diurnal-cycle-check` in dogfish. Expected
   conflicts: `dashboard/{mergedmap.js,app.js,index.html,styles.css}`,
   `ewb_live/viz/exceedance.py`, `tests/{test_raw_exceedance.py,e2e/
   test_dashboard_fe.py}`, notes/channels (union).
   Rules: keep dogfish's newer agent evolutions (03:43Z mergedmap tweaks,
   small-n dot in styles.css, `dur` v2 decode); add the min-gate delta ONLY
   per Gate A; union the channel/apppend-only notes; keep the
   `_daily_runs` None-init fix (29609e9 lacks it).
7. Re-verify the v2 property after resolution: `grep runCount|durV2|dur.min`
   in mergedmap.js; exceedance still `DUR_REV="v2"`, LAYER_REV v7, SCHEMA 3.

## Phase 4 — test, deploy, verify (gates before anything reaches users)

8. On ewb (never locally): full unit suite
   `ops/remote.sh run pytest -q --ignore=tests/e2e` — expect baseline
   254±flaky(`test_sources_network`), and `node --check` dashboard js.
9. e2e from the container against the deployed box (playwright lives here):
   `EWB_E2E_BROWSERS=chromium .venv/bin/python -m pytest -m e2e tests/e2e -q`
   — expect ≥ 122 passed / 3 skipped (baseline).
10. If Gate A approved: after deploy, refresh the index
    `ops/remote.sh bg durmin-index python -m ewb_live.viz.exceedance`
    (incremental; frames complete → only index rewrite incl. `dur.min`;
    confirm `dur.min` appears and `.run` md5s unchanged — encoding invariant).
11. Deploy: last `ops/remote.sh sync` from dogfish, then verify —
    - public fetches **cache-busted** (`?v=ts`; ewb.espr.ai nginx lags minutes):
      app.js/mergedmap.js md5 == dogfish; `index.json` dur.rev=v2 (+min if A1);
    - painted-cell invariants on one day (00/06/12/18Z solid-plane equality,
      or under A1: painted-set equality at each hour and nothing painted
      below dur.min on heat);
    - e2e green against the public URL: `EWB_LIVE_URL=https://ewb.espr.ai` run.
12. Box hygiene (surgical only): `rsync -azn -i --delete …` dry-run; delete
    individually only files that shadow repo paths and are stale (e.g. any
    re-appearing `tests/test_pipeline_complete.py`). Never blanket `--delete`;
    the box-root artifacts listed above are protected live work.

## Phase 5 — converge the topology (so this doesn't recur)

13. `git merge --ff-only aaTman/live-extreme-weather-dashboard` into `master`
    in the main project worktree (/home/orca/orca/projects/ewb-live).
14. Retire branches/worktrees: remove `regional-breakdowns` + `fable-review`
    worktrees and delete `aaTman/regions-integration`, `aaTman/fable-review`,
    `aaTman/regional-breakdowns` (all merged/subsumed — `git branch -d` only,
    never `-D` unless reviewed). Re-point `bug-catcher` /
    `improve-individual-event-view` at the merged HEAD for their next task.
    diurnal-cycle-check worktree stays until its owner lands or drops the
    min-gate (Gate A outcome recorded).
15. Single-deploy-source rule, posted to channels and every agent terminal:
    only dogfish runs `ops/remote.sh sync|run|bg`; feature branches land via
    merges into the dogfish branch; worktrees get recreated from it after.
    (Optional guard, small PR: make non-dogfish `remote.sh` require
    `EWB_ALLOW_SECONDARY_SYNC=1`.)

## Rollback

Each phase is a commit on the dogfish branch: `git reset --hard <prev>` +
`ops/remote.sh sync` returns the site to that phase (data semantics unchanged
throughout Phase 5; Phase 3/4 data-level rollback = the 02:48Z recipe in
`deploy-pi_report.md`, rollback_v1 + forced rebuild — keep rollback_v1 until
this plan completes).

## Definition of done

- ewb.espr.ai (cache-busted) byte-matches dogfish HEAD; every branch above is
  merged, retired, or explicitly parked with an owner+deadline (Gate A).
- Unit 254±2 and e2e ≥122 pass against the deployed site.
- `dur.rev=v2` data + painted-cell invariants hold; no orphan shadow files on
  the box; `git log master..aaTman/live…` empty.
- Only dogfish has synced since the freeze ended (verify: box file mtimes +
  `logs/run-*.log` provenance).
