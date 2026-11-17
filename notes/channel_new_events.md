# Channel — new event types (cold_snap, tropical_cyclone, flood, midlat_cyclone)

Append-only. Entry header: `## [role] YYYY-MM-DD HH:MM UTC — title`.

## [integrator] 2026-11-17 21:12 UTC — kickoff: dev-site precedent found, merge order set

Replacement session (the first integrator stalled with zero commits). Working in
`/home/orca/orca/workspaces/ewb-live/ne-integration` on `aaTman/ne-integration`
off main `e492a52`. Nothing merged yet; this entry is the plan + Step 1 findings.

### Step 1 — what the "dev frontfinder" precedent actually is (found on the box)

* `/opt/ewb-events-dev` and `/opt/ewb-events-live` are two **separate code+data
  trees** of the events lane (frontfinder era). The dev tree is an rsync of a
  worktree with `.venv`, `third_party` and `data` symlinked to production — the
  pattern documented in `notes/temperature_area_rule.md:94-103`. Isolated code,
  shared read-only data.
* The newer, closer precedent is `/opt/ewb-preview-{colors,nav,flood}`: a full
  tree copy with its **own `data/results`**, served by a **throwaway
  `python -m ewb_live.serve --port 80NN`** (`/opt/ewb-preview-flood` symlinks
  `.venv` + `data/{imerg,clim,cache}` to the live ones and keeps its own
  `data/results`; per `ne-flood-detect` channel entry). Ports used so far:
  8061/8062/8063, all stopped now — 8051-8069 are free (verified with `ss -ltnp`:
  only 8050 = live web, 8080 = nginx, 8000 = legacy dashboard).
* **The public entry is currently dark.** `/etc/cloudflared/ewb-live.yml` exists
  but has no `ewb-live.json` credentials and no `cert.pem` anywhere;
  `ewb-live-tunnel.service` is **inactive**, and the nginx front
  (`/etc/nginx/conf.d/ewb-live.conf`) proxies only `ewb.espr.ai → 127.0.0.1:8080 →
  :8050`. So `https://ewb.espr.ai` is not being served by this box right now —
  creating a `dev.` hostname would need the owner's Cloudflare authorisation
  (`cloudflared tunnel login` or an API token) which I must not invent.

**Chosen /dev/ pattern (reuses the preview-tree precedent, zero Cloudflare change):**
separate tree `/opt/ewb-live-dev` with its **own** `data/results` (never a
symlink to live results), `.venv`/`third_party`/heavy read-only stores
(`data/cache/truth`, `data/icechunk`, `data/imerg`, `data/clim`) symlinked from
`/opt/ewb-live` to avoid re-downloading, served by a dedicated systemd unit
`ewb-live-dev-web.service` on **:8051**. Isolation needs no new env var: every
path in this codebase is either `serve.py`'s `REPO_ROOT`-relative or `data/`-
relative to CWD, so a separate tree with a real (non-symlinked) `data/results`
writes only inside `/opt/ewb-live-dev`. Owner reviews at
`http://192.168.0.121:8051/`. Live tree `/opt/ewb-live` and its units
(`ewb-live-web`, `-weekly`, `-pipeline`, `-store`, `-freshness`) are untouched.
I will additionally add an **opt-in** `/dev/` location + dev-hostname ingress to
`ops/setup-public.sh` templates so that when the owner authorises the tunnel the
dev site rides the same public entry — that is a template-only change today, it
touches no running config.
  * Why a port rather than a `/dev/` URL path today: `ewb_live/serve.py`
    (`translate_path`) and the dashboard's asset/fetch paths are all
    root-absolute; a subpath would need `sub_filter` rewriting of JS. A dev
    hostname (or a LAN port) is the honest version of the same thing.

### Step 2 — merge order (from main e492a52, full test suite after each)

1. `aaTman/ne-ar-debug` (AR fixes f8abd17)
2. `aaTman/ne-subsets` (MLWP virtual-store-only + subsets cache removal — **dev
   only, must NOT be promoted to live**, per the update)
3. `aaTman/ne-coldsnap` · 4. `aaTman/ne-tc` · 5. `aaTman/ne-flood-clim`
6. `aaTman/ne-flood-imerg` · 7. `aaTman/ne-mlc-impl` (mlc-research is its
ancestor) · 8. `aaTman/ne-flood-detect` (DONE per the update: 766 raw / 30
published, already contains flood-clim + flood-imerg merges — so 5/6 may dedupe,
which is fine; the merge will show it).

Freeze patch: `<=0C at all four steps` is already committed on
`ne-coldsnap`/`ne-tc` (commits `a2b6991`/`9ba935a`); I'll take the first merge's
version and let the rest dedupe, rather than applying `freeze_le0c.patch`
separately (same content → merge dedupes, per COMMON.md).

### Status / next

* Checking out merges now; committing each merge as a separate commit so a bad
  merge is one revert, not a bisect.
* Box state: `/` at **51% (235 G free)** — disk is not a constraint anymore.
  Noted: `ewb-live-precipclim.service` is in **failed** state on the box
  (someone else's transient unit — I will not touch it, just flag it).
* Then Step 3: build dev data on the LXC only (IBTrACS pull, IMERG 90-day store
  reuse, freeze w/ <=0C, cold_snap, AR lane, mlc driver, flood), Step 4 e2e +
  screenshots, Step 5 report.

— integrator
