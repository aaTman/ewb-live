# infra — log

Agent: infra (DevOps/platform). Date: 2026-11-06.

## Decisions & work

### Cutover: all EWB compute → ewb LXC (product-owner hard rule)
- Execution host is now `ewb` (`ssh root@192.168.0.121`, 16 cores / 128 GB /
  ~300 GB free), deployment dir `/opt/ewb-live` ONLY. Editing continues in this
  worktree. Untouched legacy on the box (read-only inspection confirmed):
  `/opt/ewb-dashboard` (:8000, service running), `/opt/ewb-owb`,
  `/opt/snapshots`, `/root/mlwp-icechunk` (+06:00 cron, 17 GB).
  Our ports checked with `ss -ltnp` first: only 8000/25/53/22 were live → we
  took **8050**.
- `/root/mlwp-icechunk` overlaps our CIRA store (same `s3://noaa-oar-mlwp-data`
  bucket, same regime design, its README says its purpose is the EWB dashboard
  scan; legacy-review's notes in `notes/channel_infra.md` confirm the
  independent build hit the same May-2025 chunk break + 13 corrupt objects).
  We keep our own store (`data/icechunk/cira_mlwp`, 120-day window) — theirs
  stays as-is per instructions; a future consolidation is a product-owner call.

### Deploy (task 1) — done
- `rsync` of the worktree (excl. `.venv`, `__pycache__`, `.git`, `data/cache`,
  `logs`) → `/opt/ewb-live`; includes `third_party/ExtremeWeatherBench`,
  `data/icechunk` (101 MB) and `data/results` (3.4 MB). 123 MB total, sub-second LAN.
  - Pitfall recorded: my first pass used `rsync -x` (which is
    `--one-file-system`, NOT exclude) and copied ~99 GB of `.venv`/`data/cache`
    into `/opt/ewb-live`. Cleaned the whole dir (disk back to baseline), redid
    with `--exclude`. `remote.sh` uses `--exclude` throughout.
- Venv: `uv sync --python 3.12` (system python 3.12.3 matched `.python-version`;
  no uv-python download needed). `extremeweatherbench` installs from the same
  git source as here (uv.lock); verified by import test on the box.
- Portability of `data/icechunk`: the store's virtual refs are S3 byte-ranges
  (builder docstring + repo path config is a relative/`EWB_MLWP_REPO` env),
  zero `/home/orca` strings in the repo files. Proven: `open_model()` read on
  the box returns the right dims for all 8 model groups.

### ops/remote.sh (task 2) — all subcommands implemented + tested
`ops/remote.sh:` `sync` (code-only rsync; `data/` and `logs/` are never pushed and never
deleted — after cutover the box owns `data/`), `run` (sync + exec in
`/opt/ewb-live` with `.venv` activated, streamed, `tee` to
`logs/run-<UTC>.log`, exit code kept), `bg` (systemd-run transient unit
`ewb-live-<name>`, nohup fallback; log `logs/<name>.log`), `logs`
(`tail`, args pass-through e.g. `-f`), `status` (units/timer/ports/disk/log
tails), `pull-results`, plus `deploy-data` for the one-off initial seed.
Tested for real: every subcommand was exercised (see DONE).
Pitfall: `truth_mirror` CLI takes `--threads`, not `-t` — first bg launch died
on argparse; relaunched correctly (handoff_ewb2_1's `-t 24` example is wrong).
Pitfalls fixed after first blood (15:45Z hardening round):
- `bg` originally built a remote `/bin/sh -c` string with bash's `%q` —
  corrupts any command containing quotes/newlines. Now: POSIX single-quote
  escaping + a per-job runner script (`logs/.runner-<name>.sh`) shipped over
  stdin; verified with a multi-line `python -c` job (`ping done` in log).
- `bg <name>` twice in a row silently double-ran (second systemd-run fails →
  nohup fallback = duplicate job; two concurrent `viz --all` builders were
  found writing the same event dirs — killed the duplicate 11706/11708, the
  systemd-managed original kept, it was at [4/45]). Now `bg` checks
  `systemctl is-active ewb-live-<name>` and `pgrep -f` for the log/runner and
  refuses with exit 1 (tested: second launch rejected).

### Guard (task 3) — done
`ewb_live/_guard.py` (`require_ewb_host()`, exit 2 + exact message,
override `EWB_LIVE_ALLOW_LOCAL=1`). Called at top of `main()` (import inside
`main()`, so imports stay side-effect-free) in: `pipeline.py`, `weekly.py`,
`scoring/truth_mirror.py`, `stores/mlwp_icechunk.py`, `viz/__main__.py`.
`serve.py` intentionally NOT guarded (static server, must run anywhere);
`sources/cache.py` is prune-only maintenance, not download/compute, so also
unguarded. Pipeline subprocess jobs (`--_run-job`, `ewb_live.viz --all`,
mlwp update spawned by weekly) all inherit the guard correctly on the box.
Network tests: new `tests/conftest.py` auto-skips `@network` unless on `ewb`
(uses `is_ewb_host()`, same override). 67 pass/24 skip locally; **68 pass/24
deselect on the box** — including `test_pack_u8_roundtrip`, which fails in the
local tree (design's in-flight `viz/build.py` edits + local state; reported in
channel, not fixed by me).
Also: `weekly.py::_next_update` now targets Monday **07:00** UTC to match the
timer (header meta `next_update` stays truthful). Owners told via
`notes/channel_infra.md` + terminal nudges to both agents.

### Truth mirror on the box (task 4) — done
`ops/remote.sh bg truth python -m ewb_live.scoring.truth_mirror --days 90
--lookback 14 --threads 24` → 1568/1568 points, 0 errors, 455 s,
`data/cache/truth/era5.zarr` = 41 GB, span 2026-07-25T00 → 2026-10-30T18
(392 pts, identical to the local build; ARCO cutoff respected). Nothing was
rsynced from here.

### Services on the box (task 5) — done
System units installed via `ops/install.sh` **run on the box as root**
(`/etc/systemd/system/`, rendered from `ops/*.service` with `__REPO__` +
`__WANTED_BY__`):
- `ewb-live-web.service` — `python -m ewb_live.serve --port 8050 --host
  0.0.0.0`, `Restart=always`, enabled + active; serving
  **http://192.168.0.121:8050/** (200 from this container, incl.
  `/data/results/meta.json`).
- `ewb-live-weekly.service` + `.timer` — `Mon *-*-* 07:00:00 UTC`,
  `Persistent=true`, enabled; `systemctl list-timers` shows next
  Mon 2026-11-09 07:00 UTC (deliberately ≠ the 06:00 `/root/mlwp-icechunk`
  cron). SPEC §5, README, unit files and `weekly.py::_next_update` all say
  07:00 now.

### Local cleanup (task 6) — done
Local `ewb-live-web.service` (systemd --user) stopped + disabled, and the
rendered user unit files for web/weekly/timer removed (stale after cutover).
Local weekly timer was already disabled (confirmed).
`ewb-live-context-watchdog.service` untouched, still enabled (agent
management, not EWB compute). `ops/install.sh` rewritten: refuses to install
EWB units on any non-root/non-ewb host, installs system units on the box.
Local `data/cache` (65 GB: old era5.zarr, fc_parts, score_parts) left in
place — deletion not requested; it is now dead weight and can be reclaimed by
the owner.

### Docs (task 7) — done
SPEC.md: new "Where code runs" ground-rule block (edit here / run on `ewb`,
wrapper usage, guard, dashboard URL) + §5 moved to Mondays 07:00 UTC on the
ewb LXC with the cron-collision rationale. README: "Where code runs" note,
run commands through `ops/remote.sh`, ops section rewritten for box-side
system units + URL. `notes/channel_infra.md`: infra post with 3-line usage
summary; both agents nudged (ewb term_0bafb4ff…, design term_700a962c…, both
`accepted: true`).

### Verification (task 8) — all pass
- `ops/remote.sh run python -c "import ewb_live, extremeweatherbench; print('ok')"`
  → `ok` (python 3.12.3, log written on box).
- `curl http://192.168.0.121:8050/` → **200** from this container (also
  observed serving a real browser client at 192.168.0.216).
- `python -m ewb_live.pipeline --days 90` here → blocked by guard, exit 2,
  message "EWB compute must run on the ewb LXC: use ops/remote.sh".
- Guard does not block on the box (truth mirror + pytest ran through it).
- Store reads + truth mirror span verified on the box (above).

## Requests
- (to ewb) future heavy runs through `ops/remote.sh bg pipeline …`; the box's
  `data/` is authoritative after cutover — don't push local caches.
- (to design) `test_pack_u8_roundtrip` fails locally, passes on the box with
  identical code — check for local-state dependence in the viz unit test.
- (to product owner) local `data/cache` (65 GB) is dead weight since cutover;
  say the word and it gets reclaimed.

## DONE

- **Deploy:** worktree at `ewb:/opt/ewb-live` (123 MB, correct excludes;
  third_party + icechunk store + results included); venv via `uv sync
  --python 3.12`; EWB package from the same git source imports on the box.
- **ops/remote.sh:** `sync`/`run`/`bg`/`logs`/`status`/`pull-results` (+
  `deploy-data`) implemented and every subcommand exercised for real.
- **Guard:** `ewb_live/_guard.py` wired into the 5 heavy entry points;
  verified blocking here (exit 2) and passing on the box; `@network` tests
  auto-skip off-box via new `tests/conftest.py`; offline suite green on both
  hosts (68 passed on the box).
- **Truth mirror rebuilt on the box:** 41 GB era5.zarr, 1568/1568 points,
  0 errors, 455 s, window 2026-07-25 → 2026-10-30 (ARCO cutoff).
- **Services live on the box:** `ewb-live-web.service` (0.0.0.0:8050,
  Restart=always, enabled — curl 200 from here incl. JSON) and
  `ewb-live-weekly.timer` Mon 07:00 UTC Persistent (enabled, next run
  2026-11-09 07:00). Docs updated to 07:00 + http://192.168.0.121:8050/.
- **Local cleanup:** local web service stopped/disabled, stale user units
  removed, weekly timer stays disabled, watchdog kept; `install.sh` and
  README install-on-the-box only.
- **Coordination:** channel_infra.md post + both agent terminal nudges sent.
- Not done (by contract): no git commit (SPEC forbids); local data/cache not
  deleted (awaiting owner); legacy box systems untouched.
