#!/bin/bash
# mlc-research PROOF: extract ERA5 (Nov 2023 + 6-day Oct warm start) and track it.
set -euo pipefail
cd /opt/ewb-live
mkdir -p data/mlc
echo "### extract Nov 2023 (MSLP + 850 hPa vorticity)"; date -u
python scripts/mlc/extract_month.py --year 2023 --month 11 --out data/mlc/nov23
echo "### extract Oct 26-31 2023 (stitching context)"; date -u
python scripts/mlc/extract_month.py --start 2023-10-26T00 --end 2023-10-31T18 --out data/mlc/octctx
mkdir -p data/mlc/inputs
ln -sf ../octctx/etcc_*.nc data/mlc/inputs/ 2>/dev/null || true
ln -sf ../nov23/etcc_*.nc data/mlc/inputs/ 2>/dev/null || true
ls -la data/mlc/inputs | head -3
echo "### track Oct 26 - Nov 30"; date -u
bash scripts/mlc/track.sh data/mlc/inputs data/mlc/run_octnov vor
echo "### done"; date -u; du -sh data/mlc/*; df -h /opt | tail -1
