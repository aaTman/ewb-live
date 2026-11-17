#!/bin/bash
B=/opt/ewb-live/opt/mm/envs/te/bin
ls $B | tr '\n' ' '; echo
echo "======== DetectNodes usage"
$B/DetectNodes --help 2>&1 | head -80
echo "======== StitchNodes usage"
$B/StitchNodes --help 2>&1 | head -80
