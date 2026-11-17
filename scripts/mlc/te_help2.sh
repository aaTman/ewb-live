#!/bin/bash
B=/opt/ewb-live/opt/mm/envs/te/bin
$B/StitchNodes --help 2>&1 | sed -n '30,120p'
echo "==== NodeFileEditor"
$B/NodeFileEditor --help 2>&1 | head -40
