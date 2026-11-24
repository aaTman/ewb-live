#!/bin/bash
B=/opt/ewb-live/opt/mm/envs/te/bin
echo "### StitchNodes full ($( $B/StitchNodes --help 2>&1 | wc -l) lines)"
$B/StitchNodes --help 2>&1
echo "### NodeFileEditor calculate help"
$B/NodeFileEditor --help 2>&1 | sed -n '1,60p'
