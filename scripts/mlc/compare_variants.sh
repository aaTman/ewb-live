#!/bin/bash
cd /opt/ewb-live
for TAG in mslp vor vor3 vor3v; do
  echo "===================== variant $TAG"
  python scripts/mlc/analyze_tracks.py --tracks data/mlc/run/tracks_$TAG.csv --from 2023-11-01 --to 2023-11-30 2>&1 | grep -vE "gribapi|warnings.warn|Deprecation|t0, t1" | sed -n '/# tracks file/,/deepest 10/p;/FOUND track/p;/deepest along track/p;/displacement .*path/p'
done
echo "===================== disk"
du -sh data/mlc/nov23 data/mlc/octctx data/mlc/run; df -h /opt | tail -1
