#!/bin/bash
# mlc-research PROOF step 2: TempestExtremes DetectNodes + StitchNodes on ERA5.
#   usage: track.sh "<input nc globs, space separated>" <outdir> <tag> [delta] [vor threshold]
# Learned the hard way:
#  - --in_data takes a ';'-separated file list, not a shell glob -> use --in_data_list
#  - DetectNodes writes ONE node file per input time: the literal "XXXXXX" in --out is
#    replaced by the timestamp, so StitchNodes must be fed an --in_list built from ls
#  - repeated --outputcmd flags OVERWRITE each other: pass a ";"-separated list in one flag
#  - node columns are "i j lon lat <outvar>..." and --in_fmt must list them positionally as
#    "lon,lat,<outvar>..." (TE drops i,j itself); output columns are named after the VARIABLE
set -euo pipefail
IN="$1"; OUT="$2"; TAG="$3"; D="${4:-1.0}"; VT="${5:-0.0000000002}"
B=/opt/ewb-live/opt/mm/envs/te/bin
mkdir -p "$OUT"
rm -f "$OUT"/nodes_"$TAG"*.dat "$OUT"/tracks_"$TAG".csv "$OUT"/nodelist_"$TAG".txt
ls -1 $IN | sort > "$OUT/filelist.txt"   # $IN is intentionally unquoted: it is a glob list
ARGS=(--in_data_list "$OUT/filelist.txt" --searchbymin msl
      --closedcontourcmd "msl,$D,4.5,0"
      --mergedist 6.0 --minabslat 5.0
      --outputcmd "msl,min,2" --out_header)
FMT="lon,lat,msl"
if [ "$VT" != "off" ]; then
  ARGS+=(--thresholdcmd "vor850,>=,$VT,4" --outputcmd "msl,min,2;vor850,avg,2")
  FMT="lon,lat,msl,vor850"
fi
echo "== DetectNodes ($TAG): ${ARGS[*]}"
time "$B/DetectNodes" "${ARGS[@]}" --out "$OUT/nodes_$TAG.XXXXXX.dat"
ls -1 "$OUT"/nodes_$TAG.*.dat | sort > "$OUT/nodelist_$TAG.txt"
echo "-- nodes: $(cat "$OUT"/nodes_$TAG.*.dat | grep -vc '^#' || true) in $(wc -l < "$OUT/nodelist_$TAG.txt") files"
head -3 "$(head -1 "$OUT/nodelist_$TAG.txt")"
echo "== StitchNodes"
time "$B/StitchNodes" --in_list "$OUT/nodelist_$TAG.txt" --in_fmt "$FMT" \
   --range 6 --mintime "${MINTIME:-48h}" --maxgap 12h \
   --out "$OUT/tracks_$TAG.csv" --out_file_format csv --out_seconds
ls -la "$OUT" | tail -5; echo "-- tracks csv:"; head -3 "$OUT/tracks_$TAG.csv"; wc -l < "$OUT/tracks_$TAG.csv"
