# Handoff — guardian-frontend (1) — 2026-11-08 ~15:30 UTC

Successor keeps role name **guardian-frontend**. Brief: `notes/briefs/guardian_frontend.md`. Findings table +
metrics: `notes/guardian_frontend.md` (keep appending). Coordination: `notes/channel_guardians.md`
(append-only, with guardian-design). Never `git commit`; deploy = `bash ops/remote.sh sync`.

## State
- Everything below is DEPLOYED to the box (`ops/remote.sh sync`; web service restarted once; a systemd
  drop-in `/etc/systemd/system/ewb-live-web.service.d/public-url.conf` sets `EWB_PUBLIC_URL=https://ewb.espr.ai`
  — it is also in `ops/ewb-live-web.service` for fresh installs).
- Tests: `scripts/e2e.sh` (3 engines; `EWB_E2E_BROWSERS=chromium` for one). New file
  `tests/e2e/test_guardian_fe.py` (≈95 cases incl. axe, CSP/headers, raw-map pixels, bad-data matrix).
  WebKit needs distro libs: `scripts/e2e_webkit_libs.sh` (already unpacked in `.e2e-webkit-libs/`, gitignored,
  excluded from rsync). Playwright browsers installed under `~/.cache/ms-playwright` (WebKit wrapper patched to honour
  `$EWB_EXTRA_LD`).
- Dev proxy (scratch, not in repo): worktree `dashboard/` + `/data/*` proxied from the box on
  `127.0.0.1:8061` (`<scratchpad>/devserve.py`) — lets you test JS/CSS edits before deploying:
  `EWB_LIVE_URL=http://127.0.0.1:8061 scripts/e2e.sh tests/e2e`.
- Lighthouse: `<scratchpad>/lh.sh <url> out.json desktop|mobile` (npm lighthouse + playwright Chromium).

## Done this session (see findings table F1–F30)
routing/URL robustness, focus mgmt, a11y (axe 0), serve.py hygiene (CSP, nosniff, no dir listings, no-cache own assets,
404 page), SEO/OG/robots/favicon-og image, CLS (reserved heights + font preload + deeplink class), LCP (GlobalDash
self-start/prefetch, parallel weeks), design asks #7–#29 (JS parts), raw-map LUT renderer (stripes + pole slab),
bad-data matrix (weeks.json=null boot bug).

## Added since first draft
- Raw map LUT renderer (stripes + pole slab) DEPLOYED; antimeridian fixes (global-map footprint cut at ±180; degenerate
  full-band bbox recovery/no-box; eventviz ignores full-band boxes) DEPLOYED. Orchestrator's last ask: report when every
  dateline-crossing event renders on https://ewb.espr.ai — re-verify after ewb's bbox-convention fix lands
  (`EWB_LIVE_URL=https://ewb.espr.ai scripts/e2e.sh -k dateline`; also eyeball `#date=<day>&global=<id>`).
- Final 3-engine run logs: `<scratchpad>/final_{chromium,firefox,webkit}.log` (script `final3.sh`) — check them first.
- OPEN: Lighthouse event page desktop CLS 0.46 (Playwright says 0.06) — F30 in the findings table.

## Open / next steps (in order)
1. Re-run the 3-browser suite against the box after the last sync; fix any red (see end of `notes/guardian_frontend.md`).
2. Re-run Lighthouse (public https://ewb.espr.ai, desktop + mobile; home and `#event=31a871378c3e&lead=24h`) and fill
   the before/after table. Known ceilings: Best-practices ≈77–81 because Cloudflare's injected JS-detections script
   triggers 2 Chrome *deprecation* warnings (not ours; owner can switch "JavaScript Detections" off in the CDN);
   mobile performance ≈79 (simulated 4× CPU + slow 4G on a data-driven SPA; real-network FCP 0.9 s).
3. Not done: design #15 (titles `Heat wave · region`), G-23 Gantt scale alignment, raw-map hash sync review,
   `scores.json` (269 KB gz) still on the critical path of first render, catalog Leaflet map is not lazy-initialised.
4. Final: `scripts/public_scrub_check.sh https://ewb.espr.ai`, then write `## DONE` with the launch verdict in
   `notes/guardian_frontend.md`.

## Gotchas
- Other agents edit `dashboard/index.html`, `styles.css`, `app.js` concurrently: re-read before every patch; once
  the `d3-array` script tag vanished from index.html (both maps die) — check it after pulling.
- Public CDN caches whatever the origin allows: own assets are `no-cache` now, but expect minutes of staleness after
  header changes (`cf-cache-status`).
- `state` is a top-level `const` (not `window.state`): use `typeof state !== 'undefined'` in `wait_for_function`.
- Never `pkill -f` a pattern that appears in your own command line (kills your shell).
