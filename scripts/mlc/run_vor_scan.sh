#!/bin/bash
# mlc-research PROOF: how strong must the 850 hPa vorticity filter be?
set -euo pipefail
cd /opt/ewb-live
IN="data/mlc/octctx/etcc_*.nc data/mlc/nov23/etcc_*.nc"
for V in 0.000002 0.00001 0.00002 0.0005; do
  TAG="vor_$(echo $V | tr -d '0.')"
  echo "### $TAG vor850 >= $V"
  bash scripts/mlc/track.sh "$IN" data/mlc/run "$TAG" 1.0 "$V" 2>&1 | grep -E "^-- nodes|Discrete times|EXCEPTION"
done
echo "### orography cross-check (nodes over >500 m terrain)"
for TAG in mslp vor vor_000002 vor_00001 vor_00002 vor_0005 vor3; do
  [ -f "data/mlc/run/nodes_$TAG.XXXXXX.dat000000.dat" ] || continue
  echo "--- $TAG"
  python scripts/mlc/check_orography.py --nodes "data/mlc/run/nodes_$TAG." --topo data/mlc/topo.nc
done
