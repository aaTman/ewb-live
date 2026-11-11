#!/bin/sh
# Persistent runner for ewb-live-pipeline.service (replaces the hand-baked
# logs/.runner-pipeline.sh of the 2026-11-06/07 runs). All knobs are env
# overridable from the unit file or the environment; defaults reflect the
# owner-approved config after the 2026-11-07 selection (serial AR lane:
# AR jobs scale ~0.075 GB/deg^2 and a 4.9 h job peaked 74.5 GB).
cd "$(dirname "$0")/.." || exit 1
. .venv/bin/activate
export EWB_LIVE_INIT_PARALLEL="${EWB_LIVE_INIT_PARALLEL:-2}"
export EWB_LIVE_AR_JOBS="${EWB_LIVE_AR_JOBS:-1}"
exec python -m ewb_live.pipeline \
    --days "${EWB_LIVE_DAYS:-90}" \
    --jobs "${EWB_LIVE_JOBS:-1}"
