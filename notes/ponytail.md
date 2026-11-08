# Ponytail hourly code review (setup by agent `ponytail-setup`)

## What Ponytail is (research, 2026-11-07)

- **What**: [ponytail.dev](https://ponytail.dev/) / [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) — a prompt-pack "lazy senior dev" for AI coding agents. It is one ruleset (`skills/ponytail/SKILL.md`, compact version `AGENTS.md`) plus five task skills. It writes less code by reusing what exists (the "ladder": need it? → already in repo? → stdlib? → native? → installed dep? → one line? → minimal working version), and is explicitly *not* lazy about validation, error handling, security, accessibility, or tests for risky logic.
- **Review-relevant skills** (what our hourly agent uses):
  - `ponytail-review` — reviews a *change* like the dev who gets paged: bugs, security, load, missing tests, speed, lean. Reads the connected code, not just the diff. Every finding needs a concrete failure case.
  - `ponytail-audit` — same checks for the *whole repo*, mapped first (entry points, data flow, expected load), findings ranked most-important-first.
- **License / cost**: MIT, free, no API, no telemetry, no config file required. Cost is only the LLM tokens of whichever agent runs it (our hourly reviewer is one Hermes session; incremental runs are cheap, the daily full sweep is the expensive one, bounded by deadlines: 45 min incremental / 110 min sweep).
- **Hermes support**: official — INSTALL.md has a "Hermes Agent" section. The Hermes plugin injects the active mode before each LLM turn, registers the six skills as `ponytail:<skill>` and the `/ponytail*` slash commands.

## Install (done on this container, 2026-11-07)

Documented path `hermes plugins install DietrichGebert/ponytail --enable` fails here with
`plugin.json declares an unsupported or missing Agent Plugins schema` — a known Hermes
install-check bug (fix pending upstream, PR NousResearch/hermes-agent#125937). The vendor-documented
fallback works and is what we use:

```bash
git clone https://github.com/DietrichGebert/ponytail.git ~/.hermes/plugins/ponytail
hermes plugins enable ponytail
```

Verified: `hermes plugins doctor ponytail` OK (runtime discovery, import, 2 hooks); a real
one-shot session (`hermes -z ...`) lists `ponytail:ponytail, ponytail:ponytail-review,
ponytail:ponytail-audit, ponytail:ponytail-debt, ponytail:ponytail-gain, ponytail:ponytail-help`.
Plugin version 5.3.3. No skill flag is needed on launch — plugin skills load automatically.

## The hourly loop (agent tooling on THIS container — not EWB compute)

```
ewb-live-ponytail-review.timer (systemd --user, hourly at :15, Persistent=true)
  └─ ewb-live-ponytail-review.service (oneshot, flock -n ⇒ one review at a time)
       └─ ops/ponytail_review.py
            1. mode: FULL SWEEP once per UTC day (ponytail-audit); otherwise
               incremental = code files with mtime newer than the last run's
               start, in ewb_live/, dashboard/, ops/, scripts/, tests/ only
               (never third_party/ or data/; repo convention is no commits,
               so mtime is the change signal — the daily sweep re-covers all)
            2. scope → notes/.ponytail_worklist.txt
            3. close previous run's Orca terminal (state keeps the handle)
            4. launch ONE review agent:
               orca agent launch --agent hermes --surface terminal \
                 --prompt "$(cat notes/briefs/ponytail_review.md)" --json
            5. waits for notes/.ponytail_done.txt to bear this run's RUN STAMP
               (the agent writes it as its last step, after report+channels);
               idle alone does NOT count; deadlines: 45 min incremental,
               110 min full sweep (a timed-out run does not advance state)
            6. closes the terminal (no hourly pile-up); updates state
```

- State: `notes/.ponytail_state.json` (cutoff = each run's start, so edits made *during* a
  run are reviewed by the next one; `last_full_sweep_utc` drives the daily sweep).
- Logs: `logs/ponytail/<UTC-timestamp>.log` per run (+ journactl).
- Review agent behavior (full contract in `notes/briefs/ponytail_review.md`): findings get
  severity + file:line + proposed patch, are mapped to owners via SPEC/roster, appended to
  `notes/channel_ponytail_<owner>.md`, and owners are nudged with
  `orca terminal send --terminal <handle> ...`. **Owners apply fixes; the reviewer edits
  nothing** except trivially safe formatting/typo fixes after an owner ack in the channel.
  Offline owners are listed in `notes/ponytail_report.md` for the orchestrator.
- Safety: never commits; never runs EWB compute (static review only, on this container).

## Start / stop / status (run as user `orca` on this container)

```bash
# install / update the units (after editing ops/ewb-live-ponytail-review.*)
mkdir -p ~/.config/systemd/user
cp ops/ewb-live-ponytail-review.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload

systemctl --user enable --now ewb-live-ponytail-review.timer   # start
systemctl --user disable --now ewb-live-ponytail-review.timer  # stop
systemctl --user status ewb-live-ponytail-review.timer         # status / next run
systemctl --user list-timers ewb-live-ponytail-review.timer
systemctl --user start ewb-live-ponytail-review.service        # run once now
journalctl --user -u ewb-live-ponytail-review.service -n 50    # recent runs
ls -t logs/ponytail/ | head                                    # per-run logs
```

## Status log

- 2026-11-07 21:4x — researched, installed (manual-clone fallback), verified in session.
- 2026-11-07 21:49 — first end-to-end run (manual, full sweep). Plumbing worked
  (launch/idle-detect/close/state) but the reviewer went idle mid-run while
  waiting on `delegate_task` children and the terminal was closed before any
  output. Fixes: reviewer must work alone; completion is a **marker file**
  contract (`notes/.ponytail_done.txt` bearing the run stamp), not TUI idle;
  a timed-out run no longer advances state; full sweeps get a 75-min deadline;
  incremental scope = mtime-since-last-run only (git-dirty union re-reviewed
  the same ~40 files every hour).
- 2026-11-07 23:49 — second full sweep (manual, fixed driver): finished
  `done-marker` at 00:32Z. 5 findings (2 must / 2 should / 1 nice) across
  ewb + design; channels `notes/channel_ponytail_{ewb,design}.md` created;
  both owners nudged and live. Design applied both its fixes within 20 min of
  the nudge (`viz/build.py:381` arg-swap, `app.js` p85/p15 copy); ewb working
  through its must-fix. This is the loop's acceptance evidence.
- 2026-11-08 00:5x — timer enabled (`enable --now`); hourly at :15 + jitter,
  Persistent. First scheduled tick fired 01:16:15Z — new UTC day ⇒ correctly
  chose FULL SWEEP (second daily sweep), auditing the code owners pushed after
  the first. Both first-sweep must-fixes verified applied by owners
  (`pipeline.py:762`, `viz/build.py:381` + app.js copy).
- 2026-11-08 02:32 — the 01:16 scheduled sweep hit the 75-min timeout (repo
  grew: climatology/ + fc_cache + more since the 43-min first sweep). State
  correctly did NOT advance (cutoff/sweep untouched); the 02:15 tick's queued
  activation started a retry sweep immediately at 02:32 (systemd queues one
  job; flock still prevents overlap). Full-sweep deadline raised to 110 min,
  TimeoutStartSec to 120 min.
- 2026-11-08 03:47 — the queued 03:15 tick started right after the 02:32
  timeout and completed `done-marker` in 11 min (most code already audited by
  the two earlier sweeps; it focused the pass on changed files + first full
  JS read). 4 new findings (1 must / 2 should / 1 nice); new owner channel
  `channel_ponytail_infra.md` (ops/ findings routed + infra nudged). No stray
  review terminals. Loop now autonomous: hourly incremental, daily full sweep,
  state advances only on marker completion.

## DONE

