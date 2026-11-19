#!/bin/bash
f=$(ls $1/nodes_*.dat | head -1); echo "$f"; head -4 "$f"; echo "..."; awk 'NR>1{s+=$1}END{print "rows:",NR-1}' "$f"
