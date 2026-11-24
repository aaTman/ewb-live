# Portability plan — packaging EWB Live for Brightband (agent: portability, phase 1)

Date: 2026-11-06. Scope: PLAN + AUDIT ONLY. No other agent's code was touched.
Goal (SPEC "Portability" ground rule): the whole system ships as a container (or a
GCE VM image recipe) that a stranger can run in a fresh GCP project with all state
in object storage. Today the system runs only on the `ewb` LXC at fixed paths.

---

## 1. Audit — environment couplings in the repo

### 1.1 Hardcoded hosts / IPs / absolute paths

| # | File:line | Coupling | Notes |
|---|---|---|---|
| 1 | `ops/remote.sh:20` | `HOST="${EWB_LIVE_HOST:-root@192.168.0.121}"` | dev-workflow wrapper, not part of the shipped product; env-overridable already |
| 2 | `ops/remote.sh:21` | `REMOTE_DIR="${EWB_LIVE_REMOTE_DIR:-/opt/ewb-live}"` | same |
| 3 | `ops/install.sh:17,30` | help text + refusal message `ssh root@192.168.0.121 'cd /opt/ewb-live …'` | doc text; script itself renders `__REPO__` from `$PWD` |
| 4 | `ops/install.sh:77` | verify hint hardcodes `http://192.168.0.121:8050/` | doc text |
| 5 | `ops/ewb-live-context-watchdog.service:5` | `WorkingDirectory=/home/orca/orca/workspaces/ewb-live/dogfish` | **agent management on the orca container; explicitly out of shipping scope** (README §Ops says it stays here) |
| 6 | `ewb_live/_guard.py:16` | `EWB_HOST = "ewb"` — hostname equality check | hard-blocks every heavy entry point off the LXC; in a container the hostname is never `ewb` |
| 7 | `ewb_live/climatology/owb_clim.py:65` | `EWB_LIVE_OWB_RAW` default `/opt/ewb-owb/data/clim2` | **legacy-only data path** (§1.6); env-overridable but the default is a lie outside the box |
| 8 | `ewb_live/climatology/owb_clim.py:71-74` | `EWB_LIVE_CLIM_OUT` default `/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr` | absolute box path as default |
| 9 | `ewb_live/events/_upstream.py:19-24` | `REPO_ROOT/third_party/ExtremeWeatherBench/data_prep`, env `EWB_DATA_PREP_DIR` | portable (package-relative + env) — OK pattern |
| 10 | `ewb_live/serve.py:30-34` | `REPO_ROOT = dirname(dirname(__file__))`; `DASHBOARD_DIR`, `RESULTS_DIR`, `HISTORY_DIR` derived | portable *within a checkout*, but no env override → cannot serve state living outside the repo (needed for Cloud Run/GCS) |
| 11 | `dashboard/index.html:17` | doc string `python -m ewb_live.serve --port 8050` | cosmetic |

`ewb_live/config.py` **does not exist yet** (SPEC names it as the target). Today
paths are per-module `os.environ` reads with box-flavoured defaults.

### 1.2 CWD-relative paths (break under any runner that isn't "systemd with
WorkingDirectory=repo root, or manual cd")

| File:line | Path |
|---|---|
| `ewb_live/pipeline.py:30-31` | `RESULTS = Path("data/results")`, `PARTS = Path("data/cache/score_parts")` (module level) |
| `ewb_live/weekly.py:28-30` | `RESULTS = Path("data/results")`, `HISTORY`, `LOGS = Path("logs")` |
| `ewb_live/stores/mlwp_common.py:36` | `CACHE_DIR = "data/cache/mlwp"` |
| `ewb_live/events/__init__.py:22` | `DEFAULT_CACHE_DIR = Path("data/cache/events")` |
| `ewb_live/viz/build.py:31-32,154,243` | `ROOT = Path(__file__).parents[2]`; `OUT_DIR = ROOT/data/results/events`; `ROOT/data/cache/truth/hres_analysis_*…zarr` glob; `ROOT/data/cache/viz` — package-relative, i.e. a *third* convention: inside site-packages it points inside the venv |
| `ewb_live/viz/__main__.py:26` | `root/"data/results"` (root presumably CWD-derived) |
| `ewb_live/sources/_net.py:32-35` | `EWB_LIVE_CACHE` else `abspath(dirname×3(__file__))/data/cache` — package-relative fallback (venv-internal if pip-installed) |

Three inconsistent root conventions (CWD-relative, package-relative, env) is the
single biggest refactor driver.

### 1.3 Env-var knobs that already exist (keep; fold into config.py)

`EWB_LIVE_CACHE` (fc_cache.py:46, evaluate.py:317, truth_mirror.py:56,
inits_cache.py:31, truth.py:77, _net.py:32), `EWB_MLWP_REPO`
(mlwp_icechunk.py:59, default `data/icechunk/cira_mlwp` — **local-filesystem
storage only**), `EWB_LIVE_TRUTH_CACHE` (events/truth.py:24), `EWB_LIVE_CLIM`
(events/temperature.py:58, default `owb`), `EWB_LIVE_OWB_RAW` / `EWB_LIVE_CLIM_OUT`
(§1.1 #7-8), `EWB_DATA_PREP_DIR` (_upstream.py:24), `EWB_LIVE_ALLOW_LOCAL`
(_guard.py:28), `EWB_LIVE_MIRROR_THREADS=24` (pipeline.py:258),
`EWB_LIVE_AR_JOBS=2` (pipeline.py:399), `EWB_LIVE_INIT_PARALLEL=4`
(evaluate.py:229), `EWB_LIVE_LEAD_PARALLEL=8` (ecmwf.py:167),
`EWB_LIVE_LIST_TTL=300` (_net.py:40), `NO_GCE_CHECK` (_gcsfs_speedup.py:34).
Ops-only: `EWB_LIVE_HOST`, `EWB_LIVE_REMOTE_DIR` (remote.sh — name collides with a
future guard setting; rename to `EWB_OPS_SSH_TARGET` in phase 2).

### 1.4 Systemd / cron / execution-model assumptions

- `ops/ewb-live-web.service` — ExecStart `__REPO__/.venv/bin/python -m ewb_live.serve
  --port 8050 --host 0.0.0.0`, `Restart=always`, append-log to `__REPO__/logs/web.log`.
- `ops/ewb-live-weekly.service` + `.timer` — oneshot, `Mon *-*-* 07:00:00 UTC`,
  `Persistent=true`, `TimeoutStartSec=6h`.
- `ewb_live/weekly.py` calls subprocess entry points (`-m ewb_live.stores.mlwp_icechunk`,
  fallback `mlwp-icechunk` exe at :82-91) assuming `sys.executable` = the project venv
  and CWD = repo root (its own log dir `logs/`).
- README documents a *foreign* cron on the box (`/root/mlwp-icechunk` 06:00) that the
  07:00 slot avoids — a box-specific scheduling fact, not a product constraint.
- The 07:00-vs-05:30 (ewb-live-store.timer is referenced in SPEC §5 but no such unit
  exists in `ops/` — the daily store update is currently only a planned/SPEC'd timer;
  weekly step 1 makes daily updates a catch-up no-op anyway).

### 1.5 Local-disk-only stores (what must move to object storage or be rebuildable)

| Store | Location today | Size (measured 2026-11-06) | Rebuildable? |
|---|---|---|---|
| CIRA Icechunk virtual store | `ewb:/opt/ewb-live/data/icechunk/cira_mlwp` | 101 MB | **Yes**: full build 27 min / 1611 files / 4.85M refs (notes/mlwp-store.md §5); `update` self-heals on a fresh checkout |
| ERA5 truth mirror | `ewb:/opt/ewb-live/data/cache/truth/era5.zarr` | 41 GB (90 d) | **Yes**: 455 s wall @24 threads from ARCO (notes/infra.md) |
| OWB climatology product | `ewb:/opt/ewb-live/data/clim/t2m_owb_1990_2019.zarr` | 16 GB | Only from §1.6 raw inputs (see §4.3) |
| OWB raw 1990-2019 t2m zarrs | `ewb:/opt/ewb-owb/data/clim2` (legacy, read-in-place) | 106 GB | **No — exists nowhere else** (§4.3) |
| fc/score/events/viz caches, inits, truth cutoff | `ewb:/opt/ewb-live/data/cache/*` | 43 GB total | Yes (recompute); pure speed |
| results + history + viz assets | `data/results` (+`events/<id>/`) | 2.9 MB (+ viz dirs) | Yes via weekly; also worth seeding |

The Icechunk store *data* is virtual refs to S3 (proven `/home/orca`-free,
notes/infra.md §Deploy) — but refs are only valid while the S3 objects exist; the
repo itself is portable.

### 1.6 System packages & dependency pins

- **ecCodes (system lib)** — required at runtime by `eccodes`/`cfgrib`
  (`sources/ecmwf.py` GRIB reads, viz). The `eccodes` PyPI dist has no Linux wheel
  with the C library (uv.lock:860-875: Windows wheels + `py3-none-any` bindings +
  sdist), so `libeccodes0 libeccodes-data` must come from apt — installed by hand on
  the box (Ubuntu 2.34.1; the "2.42 recommended" warning is non-fatal). **Not in
  `ops/install.sh`** (design flagged it in channel_infra 15:45Z; still un-folded).
  This is *the* Dockerfile apt requirement.
- Python: `requires-python = ">=3.12,<3.15"` (pyproject.toml:8), `.python-version` =
  `3.12`; box runs system 3.12.3. uv.lock is complete and hash-pinned.
- **EWB comes in twice**: (a) installed package `extremeweatherbench 1.1.1` from git
  pinned in uv.lock to commit `c6467f41…` (uv.lock:925-927,
  pyproject `[tool.uv.sources]`); (b) vendored clone `third_party/ExtremeWeatherBench/`
  (v1.1.1 @ c6467f4, 5.4 MB) whose `data_prep/` scripts are importlib-loaded at
  runtime (`events/_upstream.py`). Both are pinned to the same commit — keep, but the
  Dockerfile must copy (or re-clone at build, pinned) `third_party/`.
- Heavy native deps (cartopy, eccodes via apt, matplotlib, dask) all have manylinux
  wheels — no compiler toolchain needed except what uv itself downloads (nothing:
  all wheels).
- `_gcsfs_speedup.py` sets `NO_GCE_CHECK=True` and patches the GCS storage-control
  probe for `gcp-public-data-arco-era5` + `extremeweatherbench` — on GCE with ADC
  the probe would *work* but slow; the patch stays correct either way.

### 1.7 Network egress requirements (all anonymous, no credentials today)

| Endpoint | Used by | Region / location (verified 2026-11-06) |
|---|---|---|
| `s3://noaa-oar-mlwp-data` | sources/aiwp, stores/mlwp_*, scoring | AWS **us-east-1** (per notes/mlwp-store.md §Gaps) |
| `s3://ecmwf-forecasts` (+ CDN `d2zvc0wgha4k2l.cloudfront.net`, _net.py:27) | sources/ecmwf, truth fallback | AWS **eu-west-1** + CloudFront (ECMWF open data; verify CDN routing from GCP) |
| `s3://noaa-nws-graphcastgfs-pds` | sources/aigfs | AWS **us-east-1** |
| `gs://gcp-public-data-arco-era5` | events/truth, scoring/truth, climatology rebuild | GCS **US-CENTRAL1** (verified via public storage API) |
| `gs://extremeweatherbench` | EWB default climatology/targets (events/temperature.py:98, select.py:11) | GCS (bucket metadata not public; dataset is) |
| PyPI + github.com | build-time only | n/a |

Public open-data buckets (AWS Open Data / ECMWF / GCP public datasets) have no
egress billing in or out; ingress into GCP is free. The only metered egress is
dashboard traffic out of GCP (tiny JSON/PNG).

---

## 2. Target architecture options

### Option (a) — single GCE VM + docker compose (web + scheduled jobs)

One `n2-standard-16` (16 vCPU / 64 GiB) or `n2-highmem-16` (128 GiB) in **us-central1**
(ARCO residency), persistent disk 500 GB pd-balanced, docker compose with:
- `web` service (restart: always, :8050 → Cloud LB or direct),
- `weekly` + `store-update` run by a host systemd timer (or a `ticker` container)
  calling `docker compose run --rm weekly`.
State lives on the PD under `$EWB_LIVE_DATA`; optionally mirrored to GCS nightly.

- Sizing: measured peaks — weekly scoring ~22 GB total @ jobs=3, single AR job
  4-8 GB (ewb session log 12:34Z); climatology build ~37 GB @ 10 workers concurrent
  with viz. **64 GiB covers the weekly cycle; 128 GiB covers climatology rebuild in
  place.** CPU 16 matches the ewb box exactly.
- Cost (us-central1 on-demand list): n2-standard-16 ≈ $0.82/h ≈ **$600/mo**
  always-on; n2-highmem-16 ≈ $1.46/h ≈ $1,070/mo; PD-balanced 500 GB ≈ $50/mo.
  Committed-1y ≈ −37%; if Brightband can live with "VM only during runs" (start/stop
  via a 2nd small always-on control instance or Compute Engine schedules), the
  always-on cost drops to ~$150-250/mo for a spot/preemptible n2-highmem-16
  (6-8 h/week ≈ $35/mo compute) — but preemptible needs checkpoint discipline
  (weekly.py is already resumable/idempotent, which helps).
- Cold start: none (long-running web).
- Locking: single host = current behaviour; Icechunk single-writer naturally
  serialised by systemd timer ordering (daily 05:30 store-update, weekly 07:00;
  a weekly overrun > 22.5 h would collide — same risk as today).
- Effort for Brightband: minimal — `docker compose up -d && docker compose run --rm
  bootstrap && systemctl start ewb-live-timer` maps 1:1 to today's units.

### Option (b) — Cloud Run service (static dashboard) + Cloud Run Jobs + Cloud Scheduler, GCS for all state

Web: Cloud Run service serving `dashboard/` + results (results synced to a public GCS
bucket + CDN is better — see below). Jobs: `store-update` (daily 05:30), `weekly`
(Mon 07:00), `viz`, `climatology-build` (manual), triggered by Cloud Scheduler.
State: Icechunk repo on **GCS** (Icechunk has a native GCS storage backend — refs
stay S3 anonymous), results/ on GCS behind the web service or a public bucket +
Cloud CDN; truth mirror on a **local emptyDir SSD inside the job container**
(rebuild 455 s, do not persist to GCS).

Hard limits (verified against cloud docs 2026-11-06):
- **Max instance: 8 vCPU / 32 GiB** (services and jobs). SPEC's "16 vCPU / 64-128 GB"
  is not expressible on one instance. Weekly scoring measured ~22 GB RSS @ jobs=3,
  so scoring *fits* at 8 vCPU/32 GiB with jobs=3, thin headroom (jobs=4 does not).
  The climatology rebuild (~37 GB concurrent) **does not fit** — it would need tiling
  into sub-32 GiB jobs or stays on a GCE VM.
- Job task timeout: default 10 min, **max 168 h** — weekly cycle (~6 h, systemd
  `TimeoutStartSec=6h`) fits with room.
- Service request timeout 60 min — irrelevant for static files.
- Cold start: ~1-3 s (web); jobs pay it once per run.
- Concurrency/locking for Icechunk on GCS: Icechunk commits are optimistic
  (compare-and-swap on the snapshot pointer; GCS preconditions support this).
  Rules: (1) exactly one writer job per branch — Scheduler + Jobs task-count=1 gives
  this *provided runs never overlap*; schedule daily 05:30 and weekly Mon 07:00 and
  alarm (not just log) if weekly exceeds 22 h; (2) Cloud Run jobs do **not**
  serialize successive executions — Cloud Scheduler can start Monday's weekly while
  Sunday's store-update hangs; add a cheap GCS lock object (create with
  `if_generation_match=0`, delete on exit, TTL 8 h) as belt-and-braces; (3) readers
  (web) open the repo read-only at a snapshot — unaffected by commits.
- Cost: weekly job 8 vCPU/32 GiB × 6 h ≈ $6/run ≈ $26/mo; daily update ~$0.13/run;
  Cloud Run web (min-instances 0, static) ≈ $0-2/mo; GCS ~60 GB hot (clim 16 +
  results + icechunk 0.1) + clim2 archive 106 GB Nearline ≈ $15/mo. **Total ≈
  $50/mo.**
- Effort: needs the config.py refactor (§3), results→GCS publication step,
  Icechunk-GCS mode exercised, and the climatology path moved off-instance.

### Recommendation

**Ship option (a) as the handover baseline, engineered so option (b) is a config
change, not a rewrite:** all state under one `$EWB_LIVE_DATA` root that accepts a
gs:// URL for the Icechunk repo and the climatology product, results written
atomically, and (optional) a `gcs-sync` step at the end of weekly that mirrors
`data/results` to a bucket. Rationale: (i) the pipeline's real sizing (16 vCPU /
64-128 GB) is the SPEC number and exceeds Cloud Run's 8 vCPU/32 GiB ceiling —
option (b) forces peak-memory engineering (tiling, jobs=3 cap) that risks OOM-killed
weekly runs in front of the customer; (ii) one VM + compose is what Brightband can
run and debug in an hour; (iii) the measured $600/mo can be cut later without moving
data by scheduling the VM or by executing only the *web* off-box from a bucket.
Phase-2 "b-lite" (recommended cheap end-state): GCE VM (or stopped VM) for compute +
results published to a public GCS bucket + CDN for the dashboard — static tier gets
Cloud-Run-class economics and zero request path through our code.

---

## 3. Packaging design

### 3.1 Dockerfile (multi-stage, one image, several entrypoints)

```dockerfile
# ---- build stage -------------------------------------------------------
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
# ecCodes C library for eccodes/cfgrib at runtime; baked into the FINAL stage too.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project --no-dev \
 && uv sync --frozen --no-dev           # installs ewb_live + EWB @ pinned git commit

# ---- runtime stage -----------------------------------------------------
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
      libeccodes0 libeccodes-data ca-certificates \
 && rm -rf /var/lib/apt/lists/*          # version 2.34.x works; "2.42 recommended" warning is benign
COPY --from=build /app /app              # venv + project
COPY third_party/ExtremeWeatherBench/ /app/third_party/ExtremeWeatherBench/  # data_prep imports
ENV PATH="/app/.venv/bin:$PATH" EWB_LIVE_DATA=/data EWB_LIVE_ALLOW_LOCAL=1 EWB_LIVE_ROLE=container
VOLUME /data
ENTRYPOINT ["ewb-live"]                  # thin dispatcher script → python -m ewb_live.cli
```

Notes: pinned Python 3.12 (matches uv.lock/.python-version and the box); `uv sync
--frozen` from the committed lock ⇒ bit-reproducible package set incl. EWB git
commit; `EWB_LIVE_ALLOW_LOCAL=1` is *not* a hack — the guard's purpose ("don't burn
the orca container") is satisfied by the container; replace with a config flag
(§3.2). Entrypoints (same image, subcommand):

| Entrypoint | Wraps |
|---|---|
| `ewb-live web [--port 8050]` | `python -m ewb_live.serve` |
| `ewb-live store-update` | `python -m ewb_live.stores.mlwp_icechunk update --days 120` |
| `ewb-live pipeline …` | `python -m ewb_live.pipeline` |
| `ewb-live weekly` | `python -m ewb_live.weekly` (full cycle) |
| `ewb-live viz` | `python -m ewb_live.viz --all` |
| `ewb-live climatology-build` | `python -m ewb_live.climatology.owb_clim quantiles` |
| `ewb-live truth-mirror --days 90` | `python -m ewb_live.scoring.truth_mirror` |
| `ewb-live bootstrap` | one-shot: climatology fetch + store build + truth mirror (idempotent, §4) |

### 3.2 Config design — `ewb_live/config.py` (new module, single source of truth)

One frozen dataclass `Settings` built once from env, every module imports
`config.settings`. Every path resolves `EWB_LIVE_DATA` first. Defaults below are
*portable* (no box paths). `gs://`/`s3://` allowed where marked URL.

| Setting (env) | Default | URL? | Used by |
|---|---|---|---|
| `EWB_LIVE_DATA` | `./data` (repo-local; container: `/data`) | local | root of all state |
| `EWB_RESULTS_DIR` | `$EWB_LIVE_DATA/results` | local | pipeline, weekly, serve, viz out |
| `EWB_HISTORY_DIR` | `$EWB_RESULTS_DIR/history` | local | weekly, serve |
| `EWB_VIZ_DIR` | `$EWB_RESULTS_DIR/events` | local | viz build (`build.py OUT_DIR`) |
| `EWB_CACHE_DIR` | `$EWB_LIVE_DATA/cache` (replaces `EWB_LIVE_CACHE`) | local | fc_parts, score_parts, events, viz, truth cutoff, inits, mlwp |
| `EWB_TRUTH_MIRROR` | `$EWB_CACHE_DIR/truth/era5.zarr` | local | truth_mirror, scoring/truth |
| `EWB_MLWP_REPO` | `$EWB_LIVE_DATA/icechunk/cira_mlwp` | **local or gs://** | stores/mlwp_icechunk |
| `EWB_MLWP_STORAGE` | `auto` (`local` if path has no scheme else `gcs`/`s3`) | — | selects Icechunk `StorageConfig` |
| `EWB_CLIM_PRODUCT` | `$EWB_LIVE_DATA/clim/t2m_owb_1990_2019.zarr` (was `EWB_LIVE_CLIM_OUT`) | **local or gs://** | owb_clim.get_climatology_owb |
| `EWB_CLIM_RAW` | unset → `climatology-build` errors with pointer to §4.3 (was `/opt/ewb-owb/data/clim2`) | local | owb_clim build only |
| `EWB_CLIM_MODE` | `owb` (replaces `EWB_LIVE_CLIM`; `ewb_default` available) | — | events/temperature |
| `EWB_DATA_PREP_DIR` | `<repo>/third_party/ExtremeWeatherBench/data_prep` (keep) | local | events/_upstream |
| `EWB_DASHBOARD_DIR` | `<repo>/dashboard` | local | serve |
| `EWB_WEB_HOST` / `EWB_WEB_PORT` | `0.0.0.0` / `8050` | — | serve (CLI flags win) |
| `EWB_COMPUTE_ALLOWED` | `auto`: `true` unless running the dev-worktree on host `orca` (replaces hostname-equality `_guard`; `EWB_LIVE_ALLOW_LOCAL` kept as alias) | — | pipeline, weekly, truth_mirror, store, viz entry points |
| `EWB_MIRROR_THREADS` | `24` | — | pipeline/weekly mirror step |
| `EWB_SCORE_JOBS` | `3` | — | pipeline `--jobs` default (memory-bounded: ~7 GB/job on a 32 GiB box, 3 on Cloud Run Jobs) |
| `EWB_AR_JOBS` | `2` (keep) | — | pipeline AR stage |
| `EWB_INIT_PARALLEL` / `EWB_LEAD_PARALLEL` | `4` / `8` (keep) | — | evaluate, ecmwf |
| `EWB_LIST_TTL` | `300` (keep) | — | sources/_net |
| `EWB_GCS_RESULTS_BUCKET` | unset → skip GCS publication step | gs:// | weekly step 5b (b-lite publishing) |
| `EWB_LOG_DIR` | `<repo>/logs` or `$EWB_LIVE_DATA/logs` | local | weekly/serve logs (CWD-independent) |

Implementation notes: paths become `pathlib.Path` or `fsspec`-opened via one
helper (`open_state_file(path)` → local FS fast-path, else fsspec/obstore); Icechunk
storage switch is a ~10-line factory in `stores/mlwp_icechunk.py` (the builder is
already repo-path-driven and refs are host-free); `pipeline.py:30`, `weekly.py:28`,
`mlwp_common.py:36`, `events/__init__.py:22` stop computing module-level paths.

### 3.3 docker-compose.yml (single host, option a)

```yaml
services:
  web:
    image: ${EWB_IMAGE:-ewb-live:latest}
    command: web
    ports: ["8050:8050"]
    volumes: ["ewb-data:/data"]
    restart: always
    environment: { EWB_WEB_PORT: "8050" }
  jobs:                     # cron-in-container: runs store-update 05:30, weekly Mon 07:00 UTC
    image: ${EWB_IMAGE:-ewb-live:latest}
    command: ticker         # crond + two crontab lines → docker-equivalent of the systemd timers
    volumes: ["ewb-data:/data", "./ops/crontab:/etc/crontabs/root:ro"]
    restart: always
volumes: { ewb-data: {} }
```
(If Brightband prefers host-owned scheduling, drop `jobs` and point their
systemd/cron at `docker compose run --rm weekly` — the image is the contract,
the scheduler is theirs.)

### 3.4 IaC for option (b) (or b-lite) — scripts before Terraform

Ship `ops/gcp/` as short, readable `gcloud` scripts first (Terraform optional
later): `01-enable.sh` (run, run.googleapis.com, scheduler, storage, artifactregistry,
compute), `02-buckets.sh` (`<proj>-ewb-state` hot + `…-results` public+CDN, `…-archive`
nearline), `03-web.sh` (public GCS bucket + CDN **or** Cloud Run service from the
image), `04-jobs.sh` (`gcloud run jobs create ewb-live-weekly --image … --tasks 1
--cpu 8 --memory 32Gi --max-retries 1 --timeout 21600` + store-update daily +
Scheduler HTTP→job-executions calls, SA `roles/storage.admin` on the buckets only),
`05-bootstrap.sh` (one-shot job). Terraform equivalent is a 200-line follow-up, not
the handover path.

### 3.5 CI (GitHub Actions)

`ci.yml`: `pytest -m "not network"` on push (already off-box via conftest) +
`docker build` + push to Artifact Registry (`us-docker.pkg.dev/<proj>/ewb-live/ewb-live:
<git-sha>`, `latest` on tags) via Workload Identity Federation; `docker bake` for
linux/amd64. Optional weekly cron job `docker build && docker run --rm image pytest
-m "not network"` guards the image, not just the venv. (Repo has no CI dir yet —
phase-2 task.)

---

## 4. Data bootstrap for a fresh environment

### 4.1 CIRA virtual store — rebuild from scratch, always

`ewb-live store-update` self-heals into a full build: measured **27 min** on 16
cores for the 120-day window (1611 files, 4.85M refs, 101 MB, notes/mlwp-store.md
§5). Zero bytes copied from S3; free egress (public bucket). On GCP us-central1 the
S3 round-trips are cross-cloud but ingress-free and latency-tolerant. Do **not**
migrate the existing repo directory; the only box-specific state in it is the
known-bad ledger seed, which the builder re-derives on first run (13 corrupt
objects re-skipped, at the cost of one failed-parse pass — acceptable; the ledger
commit is reproducible).

### 4.2 ERA5 truth mirror — rebuild, keep the mirror even on GCP

Measured: 90 days = 41 GB in **455 s** @ 24 threads from ARCO (notes/infra.md).
On GCP us-central1 (ARCO's own region) reads are in-region and free.
*Evaluate dropping the mirror*: rejected for the product path — ewb measured
single-event scoring at **1-11 min streaming global ARCO** vs <1 min with the
mirror + bbox crops (notes/ewb.md performance rework); EWB's derived-variable
step keeps the global grid, so direct-ARCO reads are the memory/CPU hog. The
mirror is only ~8 min to rebuild and lives on local scratch disk, so it is a
cache, not migration state. Fresh-env cost: ~$1 of VM-seconds.

### 4.3 OWB climatology — **ship the computed product** (recommendation)

- The product `t2m_owb_1990_2019.zarr` is **16 GB** (measured on the box) and is
  already built and validated against EWB's shipped percentiles (the `compare`
  step). Upload it once to `gs://<proj>-ewb-state/clim/t2m_owb_1990_2019.zarr`
  (16 GB from the box ≈ minutes on LAN uplink). Fresh envs `rsync -r`/`gsutil -m
  rsync` it in (gs→gs in-region copy: minutes, free).
- Rebuild-from-ARCO alternative: 31 years × 6-hourly global 0.25° t2m (~106 GB
  compressed read, the box build was ~85 min @ 10 joblib workers). Cost on GCP is
  low (in-region, ~$2-5 of an n2-standard-16), **but** the legacy raw inputs are a
  specific ERA5 vintage (CDS v2.1-generation zarrs); re-deriving from ARCO v0.4.1
  would shift p05/p95 by small amounts and *change the event set* the product
  owner already validated. Reproducibility of the product decision beats saving a
  16 GB upload.
- Provenance insurance: one-time archive of the raw `clim2` inputs (106 GB) to
  the nearline bucket (~$1.1/mo). Then `EWB_CLIM_RAW` is genuinely rebuildable
  anywhere, but nothing depends on it.
- Also seed: `data/results/` incl. viz assets (2.9 MB + per-event dirs — one
  `gsutil -m rsync -r`; afterwards weekly keeps it fresh) so a fresh env has a
  live dashboard on day 0, and the known-bad ledger if desired (optional).

`ewb-live bootstrap` = clim fetch → store build (27 min) → truth mirror (8 min) →
seed results; target: **<45 min, one command, idempotent**, then `web` is live.

---

## 5. Migration + portability checklist + acceptance test

### Portability checklist for all agents **now** (phase-1 rules)

1. No new hardcoded hosts, IPs, or absolute paths. New locations = a new env read
   with a portable default **today**, migrated into `config.py` in phase 2 without
   changing the env name. Never default to `/opt/…` or `/home/orca`.
2. Never compute `Path("data/…")` or `Path(__file__).parents[...]`-as-data-root at
   module import; read the root lazily via the per-module `_cache_root()`/
   `_results_root()` pattern (fc_cache.py:46 is the model).
3. All new state files go under `$EWB_LIVE_CACHE` / `$EWB_RESULTS_DIR` (whichever
   applies). Nothing new beside `data/`.
4. Don't couple code to systemd: entry points must run standalone (idempotent,
   non-zero exit on failure, stdout=JSON summary) — scheduling stays in units/
   compose/Scheduler.
5. New system dependencies (apt packages) go into **both** `ops/install.sh` and the
   (phase-2) Dockerfile/`ops/apt-deps.txt` the moment they're needed — the ecCodes
   incident is the cautionary tale (channel_infra 15:45Z).
6. Network-touching tests stay `@pytest.mark.network` + opt-in; nothing may assume
   it runs on a host named `ewb` except `_guard`'s dev-mode logic.
7. Viz/results assets: keep filenames immutable and content-keyed (design already
   does) so static publishing behind a CDN needs no invalidation logic.
8. If you touch a coupling in §1 outside your ownership, log it in your notes file;
   portability agent does the config.py sweep in phase 2 — don't refactor others'
   files.

### Migration steps (ewb LXC → GCP), in order

1. Land `config.py` + path refactor (§3.2) in-repo, offline tests green on box and
   in container. **Offline gate first: all §1.2/§1.5 code paths run with
   `EWB_LIVE_DATA` pointed at a temp dir.**
2. Build the image from the repo (CI), push to Artifact Registry.
3. Fresh GCP project: `ops/gcp/01-02` → buckets; upload clim product + clim2
   archive from the box (one-time, run from `ewb` where the data is).
4. Deploy option (a): one GCE VM, `docker compose up -d`, `docker compose run --rm
   bootstrap`, flip the `ticker`/timer on; point DNS/LB at it. Dashboard live.
5. Run one full weekly cycle **on GCP and diff `data/results` against the box**
   (scores within tolerance; same 45 selected events modulo the rolling window).
6. Keep the LXC running one extra weekly cycle as a shadow reference, then retire
   (owner call).
7. (Optional b-lite) publish `data/results` to the public bucket + CDN via weekly
   step 5b; web tier stops needing the VM's port.

### Acceptance test (definition of done)

**"Fresh GCP project → one command → dashboard live with the weekly cycle"**:
```
./ops/gcp/deploy-all.sh $PROJECT $REGION     # enable APIs, buckets, VM + compose, timers
# … wait ≤ 60 min (bootstrap) …
curl https://dashboard…/data/results/meta.json   # week = current, last_updated fresh
gcloud run jobs execute … || systemctl start ewb-live-weekly@manual   # manual weekly trigger works too
```
Rehearsal path **on the ewb LXC (never on this orca container — SPEC hard rule)**:
`ops/remote.sh run docker compose build && docker compose run --rm bootstrap &&
docker compose run --rm weekly && docker compose up -d web` with
`EWB_LIVE_DATA=/tmp/ewb-acceptance` (fresh state dir), then curl :8050 and diff
results vs production dir. Docker install on the LXC is itself a phase-2 task.

---

## 6. Phase-2 work breakdown (execute after features stabilise)

Ordering: T1 gates everything; T2-T4 parallelise; T5-T7 are infra; T8 optional.

| # | Task | Owner (reviewer) | Depends on | Est. |
|---|---|---|---|---|
| T1 | `ewb_live/config.py` + migrate all §1.2/§1.3 path/env reads (pipeline, weekly, viz, serve, stores, sources, events, scoring); guard→`EWB_COMPUTE_ALLOWED`; offline suite green on box | **portability** (ewb+design review) | features frozen enough | 1-2 d |
| T2 | Icechunk storage factory: `local | gs://` via `EWB_MLWP_STORAGE`; verify update/build/read + known-bad ledger against a GCS-backed repo; single-writer note into weekly | ewb (portability spec) | T1 | 1 d |
| T3 | weekly step 5b: atomic `data/results` → `gs://…/results/` publication (+`Cache-Control: immutable` on viz assets in serve; design ask #6) | ewb (design review) | T1 | 0.5 d |
| T4 | `ewb-live` CLI dispatcher + `bootstrap` entry point; serve `--results-dir`/env; climatology-build graceful error when raw inputs absent | ewb (design for serve) | T1 | 1 d |
| T5 | Dockerfile (§3.1) + compose (§3.3) + `apt-deps.txt`; fold `libeccodes0 libeccodes-data` into `ops/install.sh` **now, small PR** (channel ask already open) | infra | T1,T4 | 1-2 d |
| T6 | GitHub Actions CI: test + build + push (AR); image smoke-test weekly | infra | T5 | 0.5 d |
| T7 | `ops/gcp/` scripts (§3.4) + migration §5 steps 3-5 executed on a throwaway project; acceptance test locally on the LXC (§5) | portability (infra pair) | T5,T6 | 2-3 d |
| T8 | optional b-lite/b: public results bucket + CDN fronting; Cloud Run Jobs trial for store-update only (fits 8 vCPU/32 GiB easily) as the guinea pig before any weekly-on-Jobs | portability | T7 | 1-2 d |

Not in phase 2 (explicitly): sharding scoring across instances for Cloud Run's
32 GiB ceiling, WebP/viz schema changes (design's lane), climatology re-derivation
from ARCO (decided against, §4.3), retirement of `/root/mlwp-icechunk` legacy on
the LXC (product-owner call).

---

## Open risks / flags for the team

- `_guard` + SPEC "Where code runs" are dev-workflow law but antithetical to the
  shipped product; config must make the guard a no-op in containers (T1) or every
  Brightband run exits 2. (SPEC: container runs "headless from a container" — the
  guard's `EWB_LIVE_ALLOW_LOCAL` already acknowledges this.)
- The `ticker` container's crontab must reproduce the SPEC 05:30-daily/07:00-Monday
  anti-collision (the legacy `/root/mlwp-icechunk` 06:00 cron doesn't exist in GCP —
  that constraint is box-local and should die with the LXC).
- weekly.py subprocess fallbacks (`mlwp-icechunk` exe lookup, weekly.py:82-91) are
  legacy-installed paths; delete in T4 to keep one code path.
- Cloud Run Jobs memory measured headroom is thin for scoring (22/32 GiB); if
  option (b) is ever forced, tile AR jobs to jobs=2 first.
- `EWB_LIVE_HOST` (remote.sh) vs a future guard setting name collision — rename in
  T1 while only one file references it.
