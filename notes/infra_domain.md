# infra-domain — public serving for https://ewb.espr.ai

Agent: infra-domain (DevOps). Date: 2026-11-07.
Goal: serve the EWB Live dashboard (ewb LXC :8050) at https://ewb.espr.ai with valid TLS.

## OWNER ACTION NEEDED

Everything short of Cloudflare authorisation is installed and verified
(details below). One of these three options — all the rest is mine.

**Option A (recommended): a scoped API token, one paste.**
1. Open https://dash.cloudflare.com/profile/api-tokens → **Create Token** →
   **Custom token**, with:
   - Permissions: `Account / Cloudflare Tunnel / Edit` **and** `Zone / DNS / Edit`
   - Zone resources: `Included / Specific zone / espr.ai`
2. On the ewb box (`ssh root@192.168.0.121`), save it without pasting it into chat:
   `nano /root/.ewb-cf-token` → one line `CLOUDFLARE_API_TOKEN=<paste token>` → Ctrl+X, then
   `chmod 600 /root/.ewb-cf-token`
3. Tell me it's there. I run `finish-tunnel` (creates tunnel `ewb-live`,
   repoints the existing `ewb.espr.ai` record at it, enables the unit). The
   token never enters this chat or the repo.

**Option B: browser click, no token paste.** I start `cloudflared tunnel
login` on the box and give you the URL; you open it and authorise the
`espr.ai` zone. Caveat: the final redirect goes to `localhost:8479` **on the
box**, so you'd need to copy the failed redirect URL from your browser
address bar and run `curl -s "<that-url>"` on the box — doable but fiddly.

**Option C: reuse the existing tunnel (zero new connectors).** If the tunnel
behind `fronts.espr.ai` (connector on CT108) is dashboard-managed: Cloudflare
dashboard → Networks → Routes → Tunnels → that tunnel → Public Hostname:
`ewb` / zone `espr.ai` / URL `http://192.168.0.121:8080`. If you pick C, tell
me first — the nginx front must then bind the LAN interface instead of
loopback (30-second change on my side, `EWB_PROXY_LISTEN`), otherwise 8080 is
loopback-only and the connector can't reach it.

Verification I will run when live: `curl -I https://ewb.espr.ai` (200 +
valid cert + `server: cloudflare`), `/data/results/meta.json`, a deep link,
then flip the dashboard About + README to the public URL and mark DONE.

## READY FOR CLOUDFLARE HOSTNAME  (2026-11-07 ~21:15 UTC)

Owner chose **Option C** (reuse the fronts.espr.ai tunnel, connector on
CT108). My side is done:

- nginx front rebound to `0.0.0.0:8080` (re-rendered with
  `EWB_PROXY_LISTEN=0.0.0.0 ./ops/setup-public.sh install`; the rendered conf
  reverts to loopback on a plain re-install — keep the env var on this box,
  recorded in README).
- Verified from the orca container: `http://192.168.0.121:8080/` → 200
  (dashboard HTML + security headers), `/data/results/meta.json` → 200 JSON.
  Pitfall hit: `systemctl reload nginx` does not rebind a changed listen
  address — setup script switched to `restart`.

**Owner's remaining step** (Cloudflare dashboard): Networks → Routes →
Tunnels → the `fronts.espr.ai` tunnel → add Public Hostname:
`hostname: ewb` / `zone: espr.ai` / `URL: http://192.168.0.121:8080` (type
HTTP). No DNS record changes needed otherwise. I poll
`https://ewb.espr.ai` and finish verification when it flips to 200.

## Recommendation (implemented, pending authorisation)

**Cloudflare Tunnel + local nginx front** on the ewb box:
`Cloudflare edge → cloudflared (dials out 443, no inbound ports, works behind
the residential NAT at public IP 69.126.15.140) → nginx on 127.0.0.1:8080 →
ewb_live.serve :8050`.

Why not the alternatives:
- **Caddy/nginx with automatic HTTPS on the box**: requires a DNS-only record
  pointing at 69.126.15.140 (currently the record is Cloudflare-proxied),
  inbound router port-forwards to the LXC, and the residential IP. The tunnel
  avoids all three and matches the proven fleet pattern (`fronts.espr.ai`
  runs exactly Caddy-through-Cloudflare-Tunnel on CT108).
- **Reusing an existing tunnel** (Option C above) is the only cheaper path,
  but its ingress config is not reachable to me: no SSH trust to the
  mandelhub host from either the ewb box or the orca container
  (`Permission denied (publickey)`), and the tunnel definitions live in the
  owner's Cloudflare account.

## Investigation findings (read-only, 2026-11-07)

- **DNS**: `espr.ai` is on Cloudflare (`chad/lucy.ns.cloudflare.com`).
  `ewb.espr.ai` exists, proxied (resolves to CF anycast 104.21.79.151 /
  172.67.146.82). The 503 body is byte-identical to `www.espr.ai` /
  `app.espr.ai`, which the fleet notes document as "proxied record with no
  live origin — 503 is the normal state". So the record exists but **no
  tunnel/origin has ever been attached** (nothing is broken; nothing was
  serving it before).
- **No proxy software on either box**: no cloudflared/caddy/nginx/traefik on
  the ewb LXC or the orca container; no `~/.cloudflared` / `/etc/cloudflared`
  / `/etc/caddy` / `/etc/nginx` anywhere on those two hosts.
- **Retired dashboard** `/opt/ewb-dashboard/deploy/setup-ewb.sh` (read-only):
  uvicorn FastAPI :8000 only, no tunnel/proxy config — the old dashboard was
  never public. `notes/infra.md` confirms only :8000/:25/:53/:22 were live.
- **LAN sweep** for the 503's author: no LAN host answers as `ewb.espr.ai`
  (checked every :80 responder on 192.168.0.0/24, incl. the spreadanomaly
  nginx pattern from `aaTman/spreadanomaly-ifs@deploy/nginx` — its repo has
  no ewb vhost). Consistent with "record with no origin".
- **Egress/ports**: box egress = container egress = 69.126.15.140 (shared
  NAT). Inbound 80/443 untestable from inside and irrelevant for the tunnel
  design (connector is outbound-only).

## Implemented (no credentials needed) — all on the box, from repo sources

Sources live in the repo (`ops/public/`, portable: hostname/tunnel/ports via
env, no box IPs, per SPEC Portability); the setup script renders them onto
the box and owns nothing outside `/etc/nginx`, `/etc/cloudflared/ewb-live.yml`,
one unit file:

- `ops/public/ewb-live-proxy.nginx.conf` → `/etc/nginx/conf.d/ewb-live.conf`:
  loopback `:8080` front over `:8050` (which stays internal, `serve.py`
  untouched — design's file). Caching: results JSON proxy-cached 5 min served
  with `max-age=60, stale-while-revalidate=300` (upstream `no-store` is
  overridden intentionally — JSON is rewritten at most weekly; override +
  `proxy_hide_header` documented in the conf); `history/` frozen snapshots
  30 d; images 12 h; app shell/vendor honor upstream headers;
  `proxy_cache_use_stale error timeout updating 5xx` so the weekly atomic
  rewrite and web-service restarts are invisible to the public.
- `ops/public/ewb-live-security.headers.conf` → `/etc/nginx/snippets/`:
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
  `Referrer-Policy: strict-origin-when-cross-origin`,
  HSTS `max-age=15768000` gated on `X-Forwarded-Proto: https`. No CSP: the
  dashboard loads CDN vendor scripts (design lane).
- `ops/public/cloudflared-config.yml` + `ewb-live-tunnel.service` →
  `/etc/cloudflared/ewb-live.yml` + `ewb-live-tunnel.service` (installed,
  **disabled** until credentials exist; ingress = hostname → 127.0.0.1:8080,
  default 404 for any other hostname).
- `ops/setup-public.sh`: `install` (apt nginx + cloudflared .deb, render,
  `nginx -t` strict) / `finish-tunnel` (create + `route dns` + enable;
  refuses without cert.pem or `$CLOUDFLARE_API_TOKEN`) / `status` /
  `uninstall`. Guarded to root-on-ewb like `ops/install.sh`. README § Public
  URL documents the whole path.

Verified on the box (from the box; public verification is post-credentials):
- `nginx -t` clean; `systemctl is-active nginx` = active; `:8000` untouched.
- `http://127.0.0.1:8080/` → 200; `/data/results/meta.json` → 200, single
  `Cache-Control: public, max-age=60, stale-while-revalidate=300`,
  `X-EWB-Cache MISS→HIT` on repeat; all security headers present; HSTS only
  with `X-Forwarded-Proto: https`.
- `http://127.0.0.1:8050/` still serves directly (200) — nothing regressed.
- `ewb-live-tunnel.service` present, disabled, would restart-loop-proofed by
  `Restart=always` once enabled.
- cloudflared installed (`cloudflared --version`), not logged in.

## Pitfalls learned this session

- `cloudflared tunnel login` on a headless box is awkward: the OAuth
  callback redirects to `localhost:8479` **of the box** — hence Option A
  (API token) is the recommended owner path.
- nginx `add_header` inheritance: a location with any own `add_header` loses
  the server-level set → the security snippet is included per-location.
- `proxy_ignore_headers` only controls nginx's own caching; the upstream
  header still reaches the client unless you also `proxy_hide_header`
  (was observed as duplicate Cache-Control headers).
- `nginx -t && systemctl reload nginx` under `set -e`: a failed `&&`-chain
  does not abort the script (the failed command is a non-final list member) —
  rendered-but-unloaded config would have looked like a successful install.
- `ops/remote.sh sync` does not preserve the exec bit added on the box —
  re-chmod `ops/setup-public.sh` after sync (or commit it mode 755 at the
  orchestrator's next commit).
- `cloudflared --version` prints `cloudflared version <v>` — field 3.

## Requests
- (to design) none blocking. FYI: public responses now carry `max-age=60`
  on results JSON (browser-visible change vs LAN's `no-store`); deep links
  and `?data=` snapshots are unaffected. A CSP would need your sign-off since
  vendor libs load from CDNs.
- (to product owner) see OWNER ACTION NEEDED at the top.

## State when pausing
Waiting on Option A/B/C from the owner. Nothing else runs on my side. On
authorisation: `finish-tunnel` → public verification → About/README public URL
→ `## DONE` here.

## LIVE — verification (2026-11-07 ~00:40 UTC, option C)

- `curl -I https://ewb.espr.ai/` → **HTTP/2 200**, `server: cloudflare`.
- TLS valid: `CN=espr.ai`, issuer Google Trust Services WE1, valid
  2026-09-26 → 2026-12-25; strict-TLS request 200 (no --insecure).
- `/data/results/meta.json` → 200 valid JSON (window 2026-08-09→11-07).
- Deep links: app uses hash routes (`#event=<id>&lead=24h`, app.js:1156) —
  the server only ever serves `/` → 200; per-event assets resolve
  (`events/<id>/viz.json` 204 = genuinely absent pre-viz-run, identical on
  LAN 8050).
- About dialog + README updated with the public URL (README also lists the
  8080-vs-8050 open item below).

### Open item (owner, 1 min, non-blocking)
The tunnel public hostname currently points at **http://192.168.0.121:8050**
(app direct) — proven by edge JSON carrying the app's `no-store` + `pragma`
and no `X-EWB-Cache` header, i.e. the nginx cache front (:8080) is bypassed.
Ask the owner to edit the hostname URL to `http://192.168.0.121:8080`.
Check: `curl -sI https://ewb.espr.ai/data/results/meta.json | grep -i x-ewb-cache`.

## Public scrub (2026-11-08, owner task via orchestrator)

Inventory (live surface): `/` HTML + `app.js`/`eventviz.js`/`styles.css`/vendor,
top-level + nested JSON under `/data/results/` (meta, events, scores,
scorecard, pair_status, weeks, mini* sets, events/*/viz.json), plus route
probes for `notes/`, `ops/`, `logs/`, `.git`, repo root, dotfiles, directory
listings, and non-JSON results files.

**Leaks found (all were live public before this fix):**
1. `serve.py` served ANY top-level `data/results/` file with a dot →
   `probe_job.log` + `pipeline_full_run.log` (200) exposed `/home/orca/...`
   venv paths, python warnings, job ids; `scores.parquet` also served.
2. `meta.json` (+ `mini*/meta.json`): `climatology.path`
   `/opt/ewb-live/data/clim/...` and `origin` naming `/opt/ewb-owb`.
3. `pair_status.json` ×3 event groups: reason string naming the
   `EWB_LIVE_SKIP_EVENTS` env var, box memory spec (`>128 GB`) and
   `notes/ewb.md`.
4. About page LAN URL `http://192.168.0.121:8050/` (added by me last task).

**Clean:** dashboard JS/CSS, events/scores/scorecard/weeks JSON, viz.json
content (one base64 false-positive on `LXC`, pattern now word-bounded),
headers (`server: cloudflare` masks origin banner; no X-* infra headers),
404s for everything outside dashboard/ + data/results/.

**Fixes (source, deployed + ewb-live-web restarted):** JSON-only serve rule
in `serve.py`; climatology path/origin scrubbed in `owb_clim.py` (ewb lane);
pair_status reason genericized in `pipeline.py`; built JSON scrubbed in place
(local + box; weekly regenerates clean Monday); dev logs moved
`data/results/` → `logs/`; About LAN URL removed (design lane);
`server_tokens off` in nginx template (re-rendered; front on standby while
the tunnel points at :8050).

**Gate:** `scripts/public_scrub_check.sh` (tree + URL modes) +
`tests/test_public_scrub.py`; run as deploy gate in `ops/install.sh` and as
step 7 of `ewb_live/weekly.py` (fails the cycle on a hit). Patterns cover
private IPs, localhost/ports 8050/8080/8479, `/home/orca`, `/opt/ewb*`,
`/root/`, mandelhub/orca/proxmox/LXC/CT-ids, unit names, remote.sh/ssh refs,
notes refs, box specs.

**Verification after deploy:** URL-mode crawl of https://ewb.espr.ai CLEAN
(rc=0); parquet/logs now 404 at app and edge; live meta.json provenance =
`t2m_owb_1990_2019.zarr` / generic origin; live HTML has zero `192.168`;
deep link 200; targeted pytest 3 passed. Coordination posted in
`notes/channel_infra.md` + orchestration messages to ewb/design/frontend.

## DONE

### Public scrub — closure round (2026-11-08 ~05:20)
Residual report: `curl -s https://ewb.espr.ai/ | grep -cE '192\.168|8050'` = 1.
1. **Located**: `dashboard/index.html` load-error banner printed the dev run
   command `python -m ewb_live.serve --port 8050`. Fixed at source: banner is
   now user-facing copy ("Reload the page; …data refresh may be in progress").
   Frontend lane notified (channel_infra.md) — do not re-add run commands to
   user-visible copy.
2. **Second leak found by URL-mode crawl** (the tree scan missed it because
   the box rebuilds its own `data/results/`): live `pair_status.json` still
   carried `EWB_LIVE_SKIP_EVENTS … notes/ewb.md … >128 GB memory` for 2 events
   (22 entries). `pipeline.py` source was already generic; scrubbed the stale
   built file on the box in place. Next weekly rebuild writes clean strings,
   and the weekly scrub gate (step 7) now guards publish.
3. **Gate hardened**: `public_scrub_check.sh` patterns now also fail on
   `--port`, `python -m`, `ewb_live\.serve` (dev-command tells in any served
   file); planted-leak pytest extended to cover them. Both tests pass.
4. **Verified public surface (owner grep `192\.168|8050`)**:
   `/` `app.js` `eventviz.js` `styles.css` `meta.json` `events.json`
   `scorecard.json` `viz.json` — all 0. Remaining bare-grep hits are numeric
   substrings in data values, not infra references:
   `scores.json` (temperature values like 253.9492**38050**7) and
   `vendor/plotly.min.js` (**980505**). Semantically clean; no `:8050`
   anywhere. "Public URL"/hosting mentions on the page: 0 (About is science-only).
5. `public_scrub_check.sh https://ewb.espr.ai` → CLEAN (rc=0).
