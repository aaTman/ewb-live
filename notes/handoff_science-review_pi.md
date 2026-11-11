# Handoff — science-review (successor 3) → pi successor
Written 2026-11-10 01:2x UTC. Interruption is an owner decision (agent replacement), not a task completion. Read `notes/science_review.md` (the living record, §A–§J) before anything else.

## 1) Role and owned files
Role: independent verification referee for the Extreme Weather Benchmark pipeline (detection → curation → scoring → dashboard). READ-ONLY on production outputs and `ewb_live/` code on the box; all compute on the EWB LXC via `ops/remote.sh` (binding, SPEC.md); your own scripts live under `scripts/review/`. NEVER git commit (orchestrator commits).

Owned/edited by me this session (local repo paths; the box mirrors them under /opt/ewb-live/scripts/review/):
- `scripts/review/`: `sr11_g34_verify.py`, `sr12_tropical_audit.py`, `sr12b_frame_probe.py`, `sr13_flicker.py`, `d2_pixel_audit.py`, `sr14_ar_funnel.py` (canonical §J funnel), `sr14b_maps.py`, `sr14c_cand.py`, `sr14d_candmaps.py`, `sr14e_replay.py`, `sr14f_report.py`, `rank_cap_replay.py` (shared watch script — I just patched it, see §2).
- `notes/science_review.md` (§H/§I/§J CLOSED verdicts), `notes/channel_science_review.md` (append-only posts), `notes/channel_ponytail_science_review.md` (ponytail round dispositions), `notes/review_artifacts/ar_landfall_funnel.md` + `cand_table.md` + `ledger_table.md`, `notes/screenshots/sr-arfunnel-1..5-*.png`.
Not mine: `dashboard/*` (design QA's; NOW FROZEN — fable-review owns dashboard/* until 'REDESIGN LANDED'), `ewb_live/**` (ewb agent owns code changes).

## 2) Exactly where I stopped
Last completed: §J landfall funnel CLOSED + report synced to daily-union semantics; ponytail rounds through 20:40Z all disposed (see channel).

CLOSED 2026-11-10 (pi): ponytail 2026-11-10 00:10Z finding on `scripts/review/rank_cap_replay.py:53` — the replay validated curated events WITHOUT `recompute_merged_max_consec(curated, truth_cutoff())` that `detect_events` applies between `rank_and_cap` and `validate_event` (`ewb_live/events/__init__.py:116-117`).
- FIX APPLIED locally AND on the box (edit in place; both import lines + the call with a comment; the patched file is what ran).
- Running it surfaced a REAL REPLAY MISMATCH (exit 1): replay kept `06ceb4d8d4fe`, `e1bd4ec451c5`; shipped keeps `0df36ee11032`, `8002c1ab3ba0`; same 3 gate rejects (`adcdad404c32`, `c1daec0898aa`, `ce2db73f4be4`), 28 vs 28 count.
- Ruled out: input-order tiebreak — `rank_and_cap` gives the SAME keeps for file order and production's `(start,id)` sort order (tested on box at 01:08Z).
- Leading hypothesis (untested): the shipped curation ran on a DIFFERENT catalogue than the persisted `events.json` — `pipeline.py:517` writes events.json from a `top_n=None` call, `:522` re-derives via a second `detect_events(top_n=top_n)` call; if the second scan's output differs from the persisted file (cache path, ARCO cutoff drift, or events.json written pre/post something), membership flips. Note `06ceb4d8d4fe` vs `0df36ee11032` are the SAME physical AR split into two detection windows (§J finding) — a severity-tie flip between them smells like peak_value jitter between runs. `e1bd4ec451c5` vs `8002c1ab3ba0` also both W Canada/Alaska ARs.
- DONE: disposition posted 2026-11-10 03:1xZ (channel_ponytail_science_review.md). Root cause = code-era drift (AR_MIN_LANDFALL_DAYS 3->1 after artifacts were curated); replay exits 0 with the era pinned (EWB_REPLAY_LANDFALL_FLOOR=3 default; unpin after next pipeline run). No production bug.
Files edited but not deployed: none — everything I touched is either applied on the box or is local notes.

## 3) Running processes / jobs
- Local: none of mine (all review runs exited).
- Box (root@192.168.0.121:/opt/ewb-live): `ewb-live-memguard.service` and `ewb-live-web.service` active — NOT mine, leave them. No pipeline/cron run was in flight from my lane. Safe-to-leave: yes, nothing of mine runs.
- Canonical artifacts frozen at the 08:13Z Nov-09 run: `data/results/events.json` + `events_selected.json` (mtime 2026-11-09 08:13), `/tmp/sr14_funnel.json` (my funnel output; regenerate with `ops/remote.sh run python scripts/review/sr14_ar_funnel.py`, ~20 min, exit must be 0; counts must match §J: D1 1060 / F 133 / OCEANIC 1880 / D2 13 / D3 34 / D4 52 / D6 6 / M-P 51 / MYSTERY 25 / SELECTED 96).

## 4) Ordered remaining tasks with acceptance criteria
1. ~~Close the 00:10Z ponytail round~~ DONE 2026-11-10 (pi): replay green with era-pinned floor, disposition posted, next-run AR-set change flagged to owner/ewb.
2. **Continue the ponytail loop**: "Ponytail review: new findings in notes/channel_ponytail_science_review.md" → read tail, dispose each finding (apply/reject with reason), post disposition. Accept: every finding dispositioned same-round.
3. **Owner-decided rule changes now landed/pending — re-verify after the next ewb pipeline run** (each: run my audit script or a value check on the box, then post to `notes/channel_science_review.md`):
   - heat p85 / freeze p15 thresholds;
   - AR tropical exclusion (strict |lat|<15 touch on any mask pixel, track level) + TC screen at track level (`sr12_tropical_audit.py`, `d2_pixel_audit.py` — must stay CLEAN, closest-approach ≥ screen km);
   - gap-bridging for 1-step blinks (flicker: `sr13_flicker.py` must show no on-off-on objects in the published raw layer);
   - AR `landfall_days >= 1` floor (this REPLACES the 2-vs-3 question in my §J ASKs — re-run `sr14c_cand.py` funnel after the change; acceptance: candidate funnel tally recomputed and posted, dominant-killer attribution updated);
   - publish-gate drops logged (`pipeline.py:533-540` writes {stage:publish_gate,id,problem} to curation.json — after the next run, re-run `sr14f_report.py`; acceptance: gate rows print REAL problems, not "(inferred...)");
   - AR IVT ramp 250/500/750/1000/1250 (detection thresholds; re-run funnel + scorecard after);
   - builders write to temp names then swap (design-lane change; verify no torn-frame window via `wait_for_next_frame`, viz/exceedance).
4. **Scorecard watch**: after any rescoring run, `data/results/scores.parquet` must cover all shipped events at all leads (the e86c13f47bc0 stale-truth-part incident is the template; §H has the value-repro method: 145/145 rows, max diff ~2e-7).
5. §K candidates if opened by orchestrator; §H/§I/§J are CLOSED — do not reopen without new evidence.

## 5) Gotchas and open questions
- NEVER hand-decode a publisher's binary format — import the decoder (`ewb_live/viz/exceedance.py: frame_layers(), labels_from_bytes()`). I fabricated findings once by treating bytes as pixel values (2026-11-09 retraction; pinned in the `ewb-live-dashboard-viz` skill).
- Dateline: naive min/max lon fabricates 360° spans. Use the arc_bb continuous-offset pattern (see `sr14_ar_funnel.py:74` "A4 pattern"); production analog `_crop_lon`/`PeriodicBoundaryIndex` in `viz/build.py`.
- Daily semantics: ar.py floors compare against the daily OR-UNION of the four 6-h frames (`ar.py:265`), not their sum. uint64 packed (label,cell) key trick is in `sr14_ar_funnel.py:63-69`.
- xarray rules: subset variables→time→space before any math when chunks=None; `ds["latitude"]` not `ds.lat`; mixing uint64+int64 promotes to float64 (crash class seen this session).
- `events_selected.json` is a LIST, not {events:[]}.
- TC_SCREEN_KM: code constant 250 km; box service env override reports 500 — ar_filter reads the constant; don't treat the env value as the effective screen.
- The 65→12 candidate funnel's four rules in impact order: landfall_days floor, span≤60 cap (killed the window's strongest AR eb42387c81a3, IVT 1038), severity quota (ranks 16-23), publish gate peak_land<min_mask_gridpoints(500) (validate.py:132-135; NOT a hardcoded 500 rule). Owner's landfall_days>=1 decision should collapse the dominant killer — expect the funnel tally to move a lot on re-run.
- Merged-product gate rows stay "(inferred)" by construction (merged ids never appear in events.json candidates).
- NO EWB compute on the orca container; everything through `ops/remote.sh run ...` or direct ssh root@192.168.0.121. Read-only on box production files.
- No git commits — orchestrator commits everything.
- Context handoff rule: successors MUST be Hermes-profile-launched agents (this is why you're replacing me, but the note stands for YOUR successors per SPEC.md — check current SPEC text as the pi transition may supersede it).
- Open questions for owner (posted in §J, unanswered): severity-vs-IVT ranking formula (IVT 938 out while 600-653 ship), span-cap exemption for land-rich ARs.

## 6) Channels and teammates
- `notes/channel_science_review.md` — my append-only public posts (sign every post "— science-review").
- `notes/channel_ponytail_science_review.md` — ponytail findings arrive here (posted by the hourly loop, not by a teammate agent); reply by appending a disposition section.
- `notes/science_review.md` — private living record (§H/§I/§J closures).
- `notes/orchestrator_watch.md`, `notes/ewb.md`, `notes/channel_design_ewb.md` — read for orchestration context.
- Teammates: **ewb** (pipeline/code owner) — nudge via orca terminal `term_e87b37a0-5838-4c60-bebd-7910c2abb99d`; **frontend** agent (dashboard lane, `notes/channel_ponytail_frontend.md` is theirs); **design QA** owns dashboard design checks — dashboard/* FROZEN under **fable-review** until 'REDESIGN LANDED' (do not touch dashboard/*).
- Orca nudge pattern: `orca` CLI nudge to the terminal id above; keep it factual, include evidence paths.

HANDOFF WRITTEN
