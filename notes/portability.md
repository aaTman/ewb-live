# portability — log

Agent: portability (senior cloud/platform, GCP + Docker + reproducible scientific
Python). Date: 2026-11-06. Phase 1 = PLAN + AUDIT ONLY, per tasking.

## What I did

- Read SPEC.md (incl. new Portability ground rule + "Where code runs"), README.md,
  notes/{infra,ops,mlwp-store,ewb,design,channel_infra}.md; then audited the repo
  with grep: hosts/IPs/absolute paths, CWD- vs package-relative data roots,
  env reads, systemd/timer units, ops/remote.sh, local-only stores, apt needs,
  Python/uv pins, egress endpoints, box-exclusive data.
- Live-checked facts, not memory: bucket locations via public GCS API
  (`gcp-public-data-arco-era5` = US-CENTRAL1); Cloud Run quotas from cloud docs
  (max instance 8 vCPU / 32 GiB, job task timeout ≤168 h, service request 60 min);
  `eccodes` 2.50.0 on PyPI = bindings-only wheels on Linux → system
  `libeccodes0` is mandatory (this is why the viz backfill died on the box);
  uv.lock pins EWB to git commit c6467f4; sizes measured on the box via read-only
  `du` (clim product 16 GB, clim2 raw 106 GB, data/cache 43 GB, store 101 MB,
  results 2.9 MB) and box free RAM/cores for sizing sanity.
- Wrote **notes/portability_plan.md** (the deliverable): §1 audit with file:line;
  §2 option (a) GCE+compose vs (b) Cloud Run service+Jobs+Scheduler, costs, limits,
  Icechunk-on-GCS locking, recommendation = (a) now with config engineered so (b)
  is a config change, plus cheap b-lite end-state (results on GCS+CDN); §3
  Dockerfile/compose/config.py setting table/CI; §4 data bootstrap — rebuild store
  (27 min) and truth mirror (8 min), **ship** the 16 GB climatology product (ARCO
  re-derivation rejected: changes the validated thresholds), archive clim2 to
  nearline for provenance; §5 checklist + migration + acceptance test (fresh GCP
  project → one command → live weekly dashboard; rehearse via docker compose on
  the ewb LXC, NOT this container); §6 phase-2 breakdown T1-T8 with owners.
- Posted the 5-line summary to notes/channel_infra.md.

## Decisions & deviations

- No refactors of other agents' code, no git commit (SPEC). No EWB compute run on
  the orca container: all "runs" here were grep/du/ssh-read-only/curl.
- One pre-existing ask already in channel_infra (fold `libeccodes0` into
  ops/install.sh, design 15:45Z) is unaddressed by infra — I re-flagged it as a
  same-day item in §6/T5 rather than editing ops/install.sh myself (ownership
  rule).
- Naming heads-up for T1: `EWB_LIVE_HOST` is currently the SSH target in
  ops/remote.sh — the guard's new config setting must not reuse that name.

## Requests
- (to infra) Fold `libeccodes0 libeccodes-data` into ops/install.sh now (§1.6);
  it's also the only apt line the Dockerfile will need.
- (to ewb/design) Follow the §5 phase-1 checklist in new code; T1's config.py
  sweep keeps current env names so your in-flight work won't churn.

## DONE (phase 1)
- Deliverable written: notes/portability_plan.md (audit with file:line refs,
  architecture options + recommendation, packaging/config design incl. full env
  table, data bootstrap plan, migration + acceptance test, phase-2 breakdown).
- Channel summary posted to notes/channel_infra.md (5 lines).
- Nothing else touched: no code edits, no commits, no compute on this container.
