## Latest run — 2026-11-08 22:21 UTC (RUN STAMP 2026-11-08T22:17:13Z)
- **Mode:** INCREMENTAL (3 worklist files: dashboard/styles.css,
  ewb_live/scoring/truth_mirror.py, tests/test_stall_fixes.py — all diffed
  against HEAD, read in full). Connected code read: ops/freshness_check.py
  heartbeat reader, pipeline `_heartbeat`/`_run_worker`/`ensure_mirror` call
  site (pipeline.py:506), scoring/truth.py coord-guard call site,
  sources/_net.py with_retries, viz/build.py open_mirror callers,
  tests/test_truth_mirror_coords.py coverage, zarr 3.4.2 setitem probe
  (float64→float32 casts cleanly — regen path safe), HTTPError⊂OSError
  probe, and a full CSS→markup/JS emitter cross-check (every new class/id
  traced; token defs; brace balance 361/361). Unit suite NOT executed
  (static-review rule); test findings verified by reading code. No
  third_party/ or data/ read, no EWB compute.
- **What this change does:** the mirror gains per-20-point heartbeat beats
  during copy (21:34Z SHOULD fix) plus a coord-integrity guard/repair for
  the zarr fill_value=0.0 masking bug (B2), wired into every open_mirror;
  the offline stall-fix test file lands; styles.css gets the merged-map /
  raw-map / guardian-design polish + CLS reservations.
- **Findings:** 7 new — 0 must, 2 should, 5 nice.
  - ewb 3: **SHOULD** truth_mirror beat also fires from the standalone CLI →
    masks the wedge alarm (freshness ignores the beat's `pid`; fix = MainPID
    gate in freshness_check, cross-lane with infra). **SHOULD** the
    test_stall_fixes 404 test is vacuous — HTTPError is an OSError, so
    _net.with_retries retries 404 through the whole backoff ladder and the
    assert passes either way. **NICE** the fc fixture writes fake RESTART
    lines into the real logs/freshness.log.
  - design 4 **NICE** (all lean/dead-code): unreachable `.tall-note`
    display:block at ≤480 (killed by line 713's !important), dead first
    `.gm-chip[aria-pressed=true]` rule + orphan `--accent-soft` token,
    duplicated `.gm-halo`, empty `@media (max-width:620px)` block.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_design.md.
- **Owners nudged:** ewb (term_e87b37a0), design (term_f3c16ead) — both live.
- **Owners offline:** none.
- **Findings resolved since last run:** 4/4 — verified by reading the files:
  both MUST test-path items (test_stall_fixes now patches
  pl.RESULTS=tmp_path/"results" and writes the fixture heartbeat at
  results.parent), the mirror-beat SHOULD (implemented at
  truth_mirror.py:188-194 — and it spawned the pid-masking SHOULD above),
  and design's dead `.gm-chip-n` rule (grep 0 hits).
- **Open totals:** 7 (ewb 0M/2S/1N; design 0M/0S/4N; frontend/mlwp-store/
  infra 0).

## History
## 2026-11-08 21:34 UTC — previous latest-run summary — 2026-11-08 21:34 UTC (RUN STAMP 2026-11-08T21:16:46Z)
- **Mode:** INCREMENTAL (6 worklist files: dashboard/{index.html,mergedmap.js,
  styles.css}, ewb_live/pipeline.py, scripts/review/sr11_g34_verify.py,
  tests/e2e/test_dashboard_fe.py — all diffed against HEAD and read since the
  20:16 run). Connected code read: pipeline `_heartbeat` call sites +
  ops/freshness_check.py reader (path/jobs_done contract), truth_mirror.py
  (no beats in mirror phase — new finding), `_sel_thresholds` vs
  `_mode_clim` ewb_default branch (float(None) unreachable: default mode is
  owb, ewb_default table has both quantiles set), mergedmap inflight/cache/
  paintFrame races (self-heal via renderSeq — no finding), sidecar tmp
  naming vs fold-in glob vs sweep glob (isolated: `.status.tmp.<pid>`
  matches `*.tmp.*`, never `*.status.json`), serve.py JSON-only route +
  public_scrub_check both modes, e2e→tree selector cross-check (0 absent),
  CSS var light/dark definitions (0 missing), bootstrap perf measured:
  3696 scorecard cells ≈ 0.8 s in one pass (fine at publish cadence).
  Unit suite EXECUTED: **176 passed, 2 failed, 28 skipped** (failures = the
  2 MUSTs below). No third_party/ or data/ read, no EWB compute.
- **What this change does:** sr11 verify script gains the `_r()` finite
  guard; sidecar orphan tmps get pid-named + pair_sidecar swept; mergedmap
  gains the in-flight fetch map (one fetch per frame shared by
  paint+prefetch); merged/raw index.html+css settled; test_dashboard_fe
  merged/raw sections pin slider, badge, boxes, deep links.
- **Findings:** 3 new — 2 must, 1 should, 1 nice (ewb 2M/1S; design 0/0/1N).
  - ewb **MUST**×2 tests/test_stall_fixes.py — heartbeat move to
    RESULTS.parent left the two pinned tests reading/writing the old
    `results/` location → FileNotFoundError + a false freshness restart;
    repo gate `pytest` is red (2 failed).
  - ewb **SHOULD** pipeline.py:483/506 — no heartbeat beats during
    `ensure_mirror` + detect; a fresh box with a >90-min first sync is
    restarted every freshness tick (alarm prevents the stall's cure).
  - design **NICE** styles.css — dead `.gm-chip-n` rule (no emitter left).
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_design.md.
- **Owners nudged:** ewb (term_e87b37a0), design (term_f3c16ead) — both live.
- **Owners offline:** none needed (frontend live but no findings this run;
  infra/mlwp-store no findings).
- **Findings resolved since last run:** 3/3 — verified in tree: ewb SHOULD
  sr11 NaN now `_r()` finite-guard at sr11_g34_verify.py:49 and used at
  every float-out; ewb NICE sidecar orphan tmp now `.tmp.<pid>` +
  `_sweep_orphan_temps` globs `pair_sidecar/*.tmp.*` (pipeline.py:413-414,
  :1084); frontend NICE concurrent frame fetch now shared via `inflight`
  ts→Promise map, deleted in `.finally` (mergedmap.js:48,126-145).
- **Open totals:** 3 (ewb 2M/1S/0N; design 0/0/1N; frontend/mlwp-store/
  infra 0) — plus raw-badge NICE held at owner won't-fix.
- **Not checked:** e2e suites not executed here (frontend lane ran
  `-k "raw_ or merged"` = 14 passed at 20:5x); styles.css not
  line-reviewed (visual lane; var + dead-class checks pass);
  freshness_check needs systemd — logic reviewed only; the mirror-gap
  restart claim is a timing argument, not a live-fire test; bootstrap
  timing measured on synthetic 55k-row frames.


## 2026-11-08 16:28 UTC — previous latest-run summary — 2026-11-08 16:28 UTC (RUN STAMP 2026-11-08T16:15:52Z)
- **Mode:** INCREMENTAL (16 worklist files: dashboard/{app.js,globalmap.js,index.html,rawmap.js};
  ewb_live events/{ar,select,tc_screen}.py, pipeline.py, scoring/truth.py, sources/_transform.py;
  scripts/public_scrub_check.sh; tests test_{ar_tropical_filter,dateline,public_scrub,select_noverlap,tc_screen}.py +
  e2e/test_guardian_fe.py). Connected code read: evaluate.py status/`_note` flow, serve.py
  allowlist + nested-JSON rules, weekly.py quarantine/flow, viz/exceedance.py cutoff filter,
  public_reason() call sites, select.py floor/merge + owb_clim probe path, truth.py cache-key +
  crop path, test_pair_status.py/test_stall_fixes.py coverage. Unit suites EXECUTED (fakes only,
  no EWB compute): 75 passed (select_noverlap, ar_tc_guard, tc_screen, ar_tropical_filter,
  dateline, public_scrub, pipeline_unit, pair_status, stall_fixes). No third_party/ or data/ read.
- **What the change does:** backend hardening lands — pair_status sidecar contract with fold-in +
  public_reason publish sanitize, heartbeat/blocker-sig restart loop, orphan-tmp sweep,
  --publish-only mode; AR ocean-start/TC-screen rules + IBTrACS loader; select percentile-rank
  conflict resolution; truth cache key gains bbox + coord-integrity guard; dateline normalize
  on crop; scrub gate two-mode; dashboard gets GlobalDash (Equal-Earth 90-day timeline + Gantt +
  URL state) and RawDash (6-hourly raw-exceedance canvas map, 4 planes, LUT paint, zlib .bin).
- **Findings:** 4 new — 1 must, 3 should, 0 nice.
  - ewb 3: **MUST** evaluate.py:434/438 raw `{exc}` reasons persist unsanitized in
    data/results/pair_sidecar/** → scrub gate quarantines/fails deploys on any transient
    path-bearing error (public_reason only applied at publish, pipeline.py:637).
    **SHOULD** pipeline.py:620 sidecar fold sorts via p.stat() outside try →
    quarantine/concurrent-publish race crashes run() pre-scoring. **SHOULD**
    pipeline.py:619-629 sidecars never pruned → unbounded fold-in growth.
  - frontend 1: **SHOULD** globalmap.js:30/rawmap.js:24 hardcode current-window data paths,
    ignore state.week → archived-week view mixes snapshot catalog with current-window maps
    (+ misleading not-in-window note on open). Fix: hide #global-sec/#raw-sec when state.week set.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_frontend.md.
- **Owners nudged:** ewb (term_dede3bb6), frontend (term_5ef9b2ad) — both live in
  `orca terminal list --json`.
- **Owners offline:** none.
- **Findings resolved since last run:** 0/3 — verified still open in tree: d3-array tag still
  present with no guard comment (design SHOULD, 15:25); raw-badge class-toggle/cutoff logic
  unchanged; globalmap fail() fixed for gm-map (setAttribute) but gm-gantt still uses the
  `.hidden` expando (frontend NICE, 15:25 — partial fix noted in channel).
- **Open totals:** 7 (ewb 1M/2S/0N; frontend 0M/1S/2N; design 0M/1S/0N; infra/mlwp-store 0).
- **Not checked:** runtime rendering of GlobalDash/RawDash (no serving/compute from this
  container); eventviz.js + styles.css big diffs (not in worklist — skimmed only for the
  ids/classes the new JS touches, all present); raw_exceedance frame bytes vs producer
  round-trip (contract read both sides, not decoded live); e2e suite not executed (needs
  served data).

2026-2026-11-08 12:17 UTC (RUN STAMP 2026-11-08T12:17:15Z)
- **Mode:** INCREMENTAL (files: ewb_live/pipeline.py — mtime 11:24, the
  owner's 11:23Z-fixes batch; tests/e2e/test_dashboard_fe.py — mtime 12:14,
  new global-dashboard/catalog-box/theme tests). pipeline.py read in full
  with connected code re-checked: events/types.py make_event_id (id =
  type+start-date+rounded bbox — END excluded), events/temperature.py
  _mode_clim/climatology_meta (per-publish cost light, no perf finding),
  scoring/evaluate.py status vocabulary + build_forecast read-guard scope
  (fc_cache CACHE_MODELS vs lazy sources/aiwp.py open_dataset), ops unit
  PrivateTmp grep, pyproject e2e gating. e2e file read in full; selector/
  state contracts are owner-pinned by design.
- **What the change does:** pipeline.py = the three ponytail fixes from the
  11:23Z batch applied (pid-suffixed temps, untruncated last_sig blocker
  sig, mtime-ordered sidecar fold-in). e2e = regression suite added for the
  global dashboard, map-box click + back map-restore, graceful viz 404,
  #lead deep-link at a non-first lead, OS theme flip, and mobile width caps.
- **Findings:** 6 new — 1 must, 1 should, 4 nice.
  - ewb (pipeline.py) 4: **MUST** pair_status entries are keyed by event id
    only; when a fingerprint or truth change drops cached scores, a stale
    TERMINAL/documented entry permanently suppresses the rescore AND still
    counts as documented-zero → silent permanent hole while
    full_run_complete=true (D1's "35 stale terminal pairs" was this class;
    the vocabulary fix left the keying). **SHOULD** evaluate.py:428
    catch-all marks load-time (lazy-zarr ds.load) read failures as terminal
    "unsupported", defeating the D1 retriable contract. NICE /tmp/ewb_jobs_*
    never reaped after a mid-pool kill (no PrivateTmp, no sweep). NICE dead
    `import subprocess, sys` in run().
  - frontend (e2e) 2 NICE: fixture probe-binds then rebinds an ephemeral
    port (TOCTOU → bind flakes; bind the server to :0 instead); dead
    HEAT_ID/AR_ID constants.
  - design/infra/mlwp-store: nothing with a concrete failure case this pass.
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_frontend.md.
- **Owners nudged:** ewb (term_dede3bb6…) and frontend (term_5ef9b2ad…) —
  both live, sends accepted.
- **Owners offline:** none.
- **Findings resolved since last run:** 3/3, verified in tree —
  pid-suffixed temps (:606 pair_status, :628 scores.parquet, plus :53
  pid+tid in _atomic_write; all orphans match the *.tmp.* sweep glob);
  untruncated blocker sig (last_sig stashed in _write_outputs :657,
  compared at :932 — meta's [:50] list no longer feeds the sig); sidecar
  fold-in sorted by st_mtime (:590, newest chunk-tagged entry wins).
- **Open totals:** 6 (ewb 4: 1M/1S/2N; frontend 2: 0M/0S/2N; design/infra/
  mlwp-store 0).
- **Not checked:** live data/ snapshots behind the fixture existence
  assumptions (off-limits); runtime behavior of the new #gm-*/globalmap
  selectors (e2e runs on the box only; owner's own note says e2e 19/19
  green); viz.json lead-key shape behind test_lead_deep_link's int() cast.
2026-11-08 11:23 UTC (RUN STAMP 2026-11-08T11:17:15Z)
- **Mode:** INCREMENTAL (files: dashboard/app.js, ewb_live/pipeline.py,
  tests/e2e/test_dashboard_fe.py, tests/test_pipeline_unit.py). app.js +
  pipeline.py read in full with connected callers (scoring/evaluate.py
  status_out + read_error contract, truth_source_for_event,
  events/temperature._mode_clim, ops/freshness_check.py, pyproject pytest
  gating, index.html/eventviz.js/globalmap.js hooks for every new DOM id —
  all present, teardown/onModels/GlobalDash.* all exist).
  tests/e2e/test_dashboard_fe.py was touched (mtime 10:30) but has **no
  content change vs HEAD** — verified, nothing to review there.
- **What the change does:** frontend deep-link/navigation rework
  (pushState Back-to-list, lead deep-link, pair_status-driven n/a notes,
  lazy Plotly, empty-lane copy, keyboard-accessible tooltips) plus the
  pipeline resilience batch's follow-up fixes (publish-only force guard,
  blocker-sig restart stop, orphan-part re-merge, scorecard common-subset
  skill + bootstrap CIs).
- **Findings:** 3 new — 1 should, 2 nice, all ewb (pipeline.py):
  - SHOULD: fixed-name temps for scores.parquet/pair_status.json
    (:604/:624) race across processes when ops runs --publish-only while
    the unit scores → torn file published → next run crash-loops;
    fix = pid-suffixed temps (already swept by *.tmp.*).
  - NICE: blocker sig reads meta's always-absent top-level failed_jobs +
    a [:50]-truncated blocker list (:926-927) — a >50-pair blocker set can
    churn in its tail and exit 0 as "permanent".
  - NICE: sidecar fold-in merges alphabetically (:588-590), so stale
    chunk-tagged sidecars (never deleted) overwrite a newer untagged one
    after a mid-run kill; fix = sort by mtime.
  - frontend/design/infra/mlwp-store: nothing with a concrete failure
    case this pass (new DOM ids all present in index.html; esc() used at
    every data-fed innerHTML site; removed renderVerdict/renderLegend/
    eventTruth/fmtDateTime have zero remaining refs incl. e2e).
- **Channels appended:** channel_ponytail_ewb.md.
- **Owners nudged:** ewb (term_dede3bb6…) — live, send accepted.
- **Owners offline:** none.
- **Findings resolved since last run:** 8/8 verified fixed in tree —
  ewb 3 (force=False :432; _record_blocker_sig :379/:928;
  _merge_orphan_parts :388/:563, all unit-pinned by the new
  test_pipeline_unit tests) and frontend 5 (buildCharts filter-aware note;
  pyproject addopts -m "not e2e" :40; dropdown open-state snapshot;
  import math deleted; freeze fixture exclusive end :403).
- **Open totals:** 3 (ewb 3: 0M/1S/2N; frontend/design/infra/mlwp-store 0).
2026-11-08 05:03 UTC (RUN STAMP 2026-11-08T04:16:43Z)
- **Mode:** INCREMENTAL (re-run of the 04:16 worklist after its done-marker;
  files: dashboard/eventviz.js, ewb_live/pipeline.py, ewb_live/weekly.py,
  ops/install.sh). Connected code read: scripts/public_scrub_check.sh,
  ewb_live/serve.py, dashboard/app.js (picker/detail hooks), ops units +
  freshness_check.py, stores/mlwp_icechunk.py (flock claim verified real),
  scoring/evaluate.py + truth_mirror.py signatures, viz/build.py (packbits
  format, antimeridian lattice, schema-3 fields), events/temperature.py
  _mode_clim, index.html load order.
- **eventviz.js: clean this pass.** schema-3 bit-unpack matches the builder
  byte-for-byte; ?data= whitelist is server-enforced (serve.py MINI_DIRS);
  openTooltip arg order fixed (prev run's live must, re-verified in tree);
  crossing-window continuous box is consistent with the builder's eastward
  lattice; every data-fed innerHTML site escapes or uses numeric-only
  fragments; new bar/ruler CSS exists in styles.css. No finding with a
  concrete failure case.
- **ops/install.sh: clean.** all 9 UNITS files exist in ops/, deploy gate
  wired, uninstall loops the full list.
- **Findings:** 5 new — 1 must, 2 should, 2 nice, all ewb (pipeline.py x4,
  weekly.py x1). Headline: weekly._leak_gate quarantines leaked files as
  *.quarantined *inside* data/results where the gate's own grep rescans
  them — one leak makes every later weekly fail pre-snapshot forever and
  blocks install.sh redeploys.
- **Channels appended:** channel_ponytail_ewb.md. frontend/design/infra/mlwp-store:
  no new findings, no appends.
- **Owners nudged:** ewb (term_dede3bb6…) — live, send accepted.
- **Owners offline:** none (all roster handles live).
- **Findings resolved since last run:** 3 of 4 open — ewb _net 429 retry
  (fixed + test test_stall_fixes.py:155); frontend scroll-restore (mount-path
  pending-restore, app.js:1456-1465); frontend e2e transparency bound
  (relaxed to 0.25 with rationale). Still open: frontend lane-empty copy
  conflating 404 with renderer crash (app.js:1636, carried).
- **Open totals:** 6 (ewb 5: 1M/2S/2N; frontend 1: 1N).
2026-11-08 03:47 UTC (RUN STAMP 2026-11-08T03:47:52Z)
- **Mode:** FULL SWEEP (worklist fired 03:47, after an incremental pass at
  ~02:35 on the same findings set — that pass covered the owner-modified
  files: pipeline.py, truth.py, hres.py, api.py, ecmwf.py, viz/build.py,
  weekly.py, serve.py, app.js, eventviz.js, install.sh, freshness_check.py,
  public_scrub_check.sh + test, nginx public front. The sweep then extended
  to tests/ (test_stall_fixes, test_pair_status, test_mini_snapshot, e2e
  structure, conftest network gating — all sane) and a re-audit of the
  in-flight eventviz.js edit (schema-3 unpack, occFit antimeridian math,
  ?data= whitelist: clean; app.js uses a 4-name exact whitelist).
- **Not read / thin (honest gaps):** scripts/mlwp_* one-off survey scripts
  and scripts/review/* (disposable QA artifacts), styles.css (CSS only),
  tests marked `network` (body-level skim only; they run on the box).
  third_party/ and data/ off-limits per rule. ewb_live/{events,scoring,
  sources,stores} modules unchanged since the 02:05 full-sweep reads —
  those reads still stand (verified via mtime < 03:47).
- **Findings:** 4 new — 1 must, 2 should, 1 nice.
  - infra: 1 must (install.sh uninstall leaves the now-enabled
    pipeline/memguard/freshness units running and enabled — reboot resumes
    scoring after "uninstall").
  - ewb: 2 should (heartbeat beat stops before the serial retry lane so
    freshness restarts a live >90-min retry mid-run; weekly's scrub gate runs
    after publish + history snapshot so a failed gate is already public at
    two URL families), 1 nice (dead update_truth_mirror in weekly.py).
  - design / frontend / mlwp-store: no findings with a concrete failure case
    this run (JS XSS audit clean: every data-fed innerHTML site goes
    through esc(); viz guards schema-2/3 and NaN payloads).
- **Channels appended:** channel_ponytail_ewb.md, channel_ponytail_infra.md.
- **Owners nudged:** ewb (term_dede3bb6…) and infra-domain (term_987fee64…) —
  both live, sends accepted. No new findings for design/frontend/mlwp-store,
  no nudges for them.
- **Findings resolved since last run:** 6/6 open items verified fixed in
  tree by reading the code — scoring/truth.py + hres.py bbox-aware cache key
  + zombie-zarr cleanup (MUST), pipeline atomic scores.parquet /
  pair_status / meta / truth_index via tmp+os.replace (MUST), api.py partial
  subset-cache now tmp+replace and rmtree-on-open-failure (MUST, fixed
  02:59), ecmwf.py surface branch now passes valid_times (SHOULD),
  viz/build.py bbox match on candidate scan (SHOULD, fixed), perf
  failed_jobs now separated via pair_coverage.failed_jobs_after_retry
  (NICE, addressed). Frontend carryovers from the 21:49 run (app.js
  double-encoded syncUrl + dead lead branch) verified fixed in the 02:45
  owner edit.
- **Open totals:** ewb 3 (0M/2S/1N), infra 1 (1M/0S/0N), design 0, frontend
  0, mlwp-store 0. Total 4 (1M/2S/1N).
### 2026-11-07 23:49 UTC (finished 2026-11-08 00:1x UTC) — FULL SWEEP (RUN STAMP 2026-11-07T23:49:16Z)
- Read in full: pipeline.py, weekly.py, serve.py, viz/build.py,
  events/{types,__init__,select,temperature,ar,validate,truth}.py,
  scoring/{evaluate,truth,truth_mirror}.py, sources/api.py, dashboard/app.js.
  Partial: eventviz.js (pack/unpack paths), freshness_check.py,
  ponytail_review.py, remote.sh, build_mini_results.py headers,
  mlwp_icechunk.py structure. Not read: styles.css, most tests/, ops probe_*
  one-offs, aigfs/aiwp/ecmwf internals, third_party/ and data/.
- Findings: 2 must, 2 should, 1 nice (5 total): ewb 3 (1M pipeline.py:762
  tuple unpack, 1S select.py:124 stale severity cap, 1N TTL docstring);
  design 2 (1M viz arg swap, 1S p95/p05 copy).
- Channels created (first run); owners ewb + design nudged, both live.
- All 5 findings verified fixed in tree on the 2026-11-08T02:05Z run.
