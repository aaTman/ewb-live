# EWB Live

A live-updating extreme-weather benchmark dashboard. Every week it finds the extreme
weather events of the last 90 days (heat waves, freezes, atmospheric rivers) using
ExtremeWeatherBench (EWB) defaults, scores AI weather models and IFS HRES on those
events with EWB's default metrics, and publishes a WeatherBench-style scorecard.

Reference UX: Brightband's operational WeatherBench. The binding contract is
[`SPEC.md`](SPEC.md); agent decision logs live in `notes/`.

## Architecture

```
 AWS Open Data                     public truth
 ────────────                      ────────────
 s3://noaa-oar-mlwp-data           ERA5 ARCO (gs://gcp-public-data-arco-era5)
 (Sierra MLWP: graphcast,          GHCN (station t2m)
  pangu, aifs, aurora, ...)              │
 s3://ecmwf-forecasts (IFS HRES)         │
        │                                │
        ▼                                ▼
 ewb_live/stores/mlwp_icechunk   ewb_live/events/  ← EWB detection defaults
 (VirtualiZarr + Icechunk virtual store,   │        (rolling 90-day window)
  no data copied — refs to S3,             ▼
  FULL archive, updated daily 05:30 UTC)
        │                          ewb_live/scoring/  ← EWB evaluation,
        ▼                          default metrics per event type
 ewb_live/sources/  (open_forecast contract, SPEC §1)          │
        └──────────────► ewb_live/pipeline.py ◄────────────────┘
                         (incremental; caches under data/cache/)
                                    │
                                    ▼
                 data/results/{events,scores,scorecard,meta}.json
                                    │   + history/<YYYY-WW>/ snapshots
                                    ▼
              dashboard/  (static HTML+JS, Plotly/Leaflet via CDN)
              served by python -m ewb_live.serve
```

`ewb_live/weekly.py` orchestrates the whole cycle end-to-end: update the store,
re-detect events over the rolling window, score everything new/changed, rewrite
`data/results/*.json` atomically, and archive a snapshot to `data/results/history/<YYYY-WW>/`.

## Data sources

| Source | What | Where |
|---|---|---|
| Sierra MLWP (AWS Open Data) | AI WP model forecasts (GraphCast, Pangu-Weather, AIFS, Aurora, FourCastNet, …), daily 00Z inits, leads 0–240 h | `s3://noaa-oar-mlwp-data`, virtualized into a local Icechunk store (`data/icechunk/cira_mlwp`) |
| ECMWF open data | IFS HRES deterministic forecasts, 0.25°, GRIB2 byte-range reads | `s3://ecmwf-forecasts` |
| ERA5 ARCO | reanalysis truth for verification | `gs://gcp-public-data-arco-era5` |
| GHCN | station observations (heat-wave t2m where available) | via EWB target definitions |

All access is anonymous; nothing is copied except derived/cache data under `data/cache/`.

## Setup

```bash
uv sync                       # local dev venv (Python 3.11+) — code edits only
```

**Where code runs:** no EWB compute on this container. Edit here, execute on the
dedicated `ewb` LXC (`/opt/ewb-live`) through `ops/remote.sh` (SPEC §"Where code
runs"). Heavy entry points are host-guarded (`ewb_live/_guard.py`).

## Running each step (on the `ewb` box, via the wrapper)

```bash
# 1. update the MLWP Icechunk virtual store (full archive, idempotent, one commit/run)
#    normally run by ewb-live-store.timer daily 05:30 UTC; --days N narrows the plan
./ops/remote.sh run python -m ewb_live.stores.mlwp_icechunk update

#    1b. reference-generate the whole archive into a staging repo, then promote it
#        (resumable: rerun after a crash, ingested inits are skipped)
./ops/remote.sh bg store-backfill python -m ewb_live.stores.mlwp_icechunk backfill --swap
./ops/remote.sh logs store-backfill -f

#    1c. expire old snapshots + garbage-collect committed metadata (runs from update)
./ops/remote.sh run python -m ewb_live.stores.mlwp_icechunk maintenance

# 2. detect + score the last 90 days -> data/results/*.json (incremental, cached)
./ops/remote.sh run python -m ewb_live.pipeline --days 90

# 3. serve the dashboard — normally run by ewb-live-web.service; see below

# one-shot weekly cycle (steps 1–3 + history snapshot; log: logs/weekly-<date>.log)
./ops/remote.sh bg weekly python -m ewb_live.weekly

# long jobs detached, then follow the log / bring results back for review
./ops/remote.sh logs weekly -f
./ops/remote.sh pull-results

# tests (network-marked tests hit S3/GCS; run on the box; skip with -m "not network")
./ops/remote.sh run python -m pytest -m "not network"
```

## Weekly cycle & ops

All EWB services run on the `ewb` LXC as **system** units (root). The dashboard
refreshes **Mondays 07:00 UTC** (SPEC §5); the MLWP store refreshes **daily at
05:30 UTC**. (The legacy `/root/mlwp-icechunk` store and its 06:00 cron are
disabled — read-only reference, superseded by this repo's store.) Install from
this worktree, on the box:

```bash
./ops/remote.sh sync
ssh root@192.168.0.121 'cd /opt/ewb-live && ./ops/install.sh'   # installs /etc/systemd/system units
```

- `ewb-live-web.service` — dashboard at **http://192.168.0.121:8050/** (`0.0.0.0:8050`,
  `Restart=always`, logs `/opt/ewb-live/logs/web.log`).
- `ewb-live-weekly.timer` → `ewb-live-weekly.service` — `python -m ewb_live.weekly`,
  Mondays 07:00 UTC, `Persistent=true` (catches up missed runs), logs `logs/weekly.log`.
- `ewb-live-store.timer` → `ewb-live-store.service` — `python -m
  ewb_live.stores.mlwp_icechunk update`, daily 05:30 UTC, `Persistent=true`,
  `TimeoutStartSec=3h`, logs `logs/store.log`. The weekly cycle's own store update
  step is then a no-op catch-up; both are flock-guarded so they never race.
- The local `ewb-live-context-watchdog.service` on this container is agent
  management, not EWB compute — it stays.
- The local `ewb-live-ponytail-review.timer` (systemd **--user**, daily (09:15 UTC),
  `Persistent=true`) runs the Ponytail code review: one Hermes agent audits
  changed code (full-repo sweep once a day) and files findings to code owners
  in `notes/channel_ponytail_<owner>.md`. Agent tooling, static review only —
  no EWB compute, never commits. Start/stop/status commands and design:
  [`notes/ponytail.md`](notes/ponytail.md); brief:
  `notes/briefs/ponytail_review.md`; driver: `ops/ponytail_review.py`; logs
  `logs/ponytail/`.

```bash
systemctl --user status ewb-live-ponytail-review.timer   # status / next run
systemctl --user start ewb-live-ponytail-review.service  # review once now
```

Useful checks:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.121:8050/   # 200 (from anywhere on the LAN)
./ops/remote.sh status                                                # units, timer, ports, disk, logs
ssh root@192.168.0.121 'journalctl -u ewb-live-weekly.service -n 50'  # last weekly run
```

### Public URL (https://ewb.espr.ai)

Public serving is **Cloudflare Tunnel → local nginx → ewb_live.serve**. No
inbound ports are opened on the box; the connector only dials out to
Cloudflare over 443. The dashboard port 8050 stays internal; the nginx front
on `:8080` adds edge caching (JSON 5 min / history snapshots 30 d / images
12 h, served stale during the weekly atomic rewrite) and security headers
(`nosniff`, `SAMEORIGIN`, Referrer-Policy, HSTS when proxied). Config
sources: `ops/public/` (nginx vhost, security snippet, cloudflared ingress,
tunnel unit) — all portable via `EWB_PUBLIC_HOSTNAME` / `EWB_TUNNEL_NAME` /
`EWB_WEB_PORT` / `EWB_PROXY_PORT` / `EWB_PROXY_LISTEN`.

Live wiring (owner decision 2026-11-07, option C): the tunnel connector runs
on CT108 (shared with `fronts.espr.ai`), so the front binds the LAN
interface — re-render with `EWB_PROXY_LISTEN=0.0.0.0` (loopback-only default
otherwise).

```bash
./ops/remote.sh sync
ssh root@192.168.0.121 'cd /opt/ewb-live && EWB_PROXY_LISTEN=0.0.0.0 ./ops/setup-public.sh install'
ssh root@192.168.0.121 'cd /opt/ewb-live && ./ops/setup-public.sh status'    # front + tunnel health
```

One-time owner authorisation (see `notes/infra_domain.md` § OWNER ACTION
NEEDED — Cloudflare API token or a browser click on `cloudflared tunnel
login`), then:
```bash
ssh root@192.168.0.121 'cd /opt/ewb-live && ./ops/setup-public.sh finish-tunnel'
```

`finish-tunnel` creates the tunnel, repoints the `ewb.espr.ai` DNS record at
it, and enables `ewb-live-tunnel.service`.

> **Status (2026-11-07): LIVE at https://ewb.espr.ai** via option C. Open
> item: the tunnel's public-hostname URL currently points at **:8050** (app
> direct) instead of **:8080** (nginx front), so edge caching + security
> headers are bypassed — owner to switch the URL in the Cloudflare
> dashboard; verify with `curl -sI https://ewb.espr.ai/data/results/meta.json
| grep -i x-ewb-cache`.

## Dashboard

Single static page (`dashboard/`, no build step): weekly-cycle header line
(`Week YYYY-WW · updated … · next update …`) with a week picker over archived
snapshots, scorecard heat-map (relative skill vs IFS HRES, `n events` tooltips),
skill-vs-lead charts, event map + filterable catalog, per-event detail charts.
JSON endpoints served by `ewb_live.serve`:

- `/data/results/<file>.json` — current results (per-file fallback to `sample/`)
- `/data/results/weeks.json` — available archived weeks
- `/data/results/history/<YYYY-WW>/<file>.json` — week snapshot (no fallback)

Screenshots of the current UI: [`notes/screenshots/`](notes/screenshots/).
