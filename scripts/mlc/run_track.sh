#!/bin/bash
# mlc-research PROOF: track Oct 26 - Nov 30 2023 with both recipe variants.
set -euo pipefail
cd /opt/ewb-live
IN="data/mlc/octctx/etcc_*.nc data/mlc/nov23/etcc_*.nc"
echo "### variant vor (MSLP closed contour + 850 hPa relative vorticity)"; date -u
bash scripts/mlc/track.sh "$IN" data/mlc/run vor 1.0 0.0000000002
echo "### variant mslp (MSLP closed contour only)"; date -u
bash scripts/mlc/track.sh "$IN" data/mlc/run mslp 1.0 off
echo "### variant vor3 (closed contour 3 hPa / 6 deg + vorticity)"; date -u
bash scripts/mlc/track.sh "$IN" data/mlc/run vor3 3.0 0.0000000002
date -u; df -h /opt | tail -1; du -sh data/mlc/*
