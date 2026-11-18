#!/bin/bash
# Re-run the dataviz palette gates on the SEVEN event-type identity tokens as
# they are actually written in dashboard/styles.css, in the order they appear in
# the chips row (TYPE_ORDER in dashboard/app.js):
#
#   heat_wave → cold_snap → freeze → atmospheric_river → flood →
#   tropical_cyclone → midlat_cyclone
#
# That order is the pairlist that matters: it is the chip row's neighbour list,
# and its first five are also the Gantt lane order. Marks are 15-16 px glyphs
# (non-text contrast, floor 3:1), labels always wear text tokens.
#
#   bash scripts/chips_palette_check.sh            # adjacent + all-pairs
#
# Exit status is the validator's: 0 = every gate cleared, 1 = something failed.
# The all-pairs run is informational — the skill itself says a 7-hue categorical
# set cannot pass all-pairs ≥15/≥8; it is printed so the weak pairs stay named
# rather than quietly ignored (see notes/chips_design.md).
set -uo pipefail
cd "$(dirname "$0")/.."
V=scripts/vendor/validate_palette.js
command -v node >/dev/null || { echo "node is required"; exit 2; }

read_tokens() {  # $1 = light|dark  -> the seven identity hexes in TYPE_ORDER
  python3 - "$1" <<'PY'
import re, sys
mode = sys.argv[1]
css = open('dashboard/styles.css').read()
# the two token blocks: :root { ... } and html[data-theme="dark"] { ... }
blocks = re.findall(r'(:root|html\[data-theme="dark"\])\s*\{.*?\n\}', css, re.S)
raw = re.search(r'(:root\s*\{.*?\n\})', css, re.S).group(1)
dark = re.search(r'html\[data-theme="dark"\]\s*\{.*?\n\}', css, re.S).group(0)
block = dark if mode == 'dark' else raw
ORDER = [('heat_wave', 'heat'), ('cold_snap', 'cold-snap'), ('freeze', 'freeze'),
         ('atmospheric_river', 'ar'), ('flood', 'flood'), ('tropical_cyclone', 'tc'),
         ('midlat_cyclone', 'mlc')]          # TYPE_ORDER in dashboard/app.js
out = []
for _, tok in ORDER:
    m = re.search(r'--series-' + tok + r':\s*(#[0-9a-fA-F]{6})', block)
    if not m:
        sys.exit('missing --series-' + tok + ' in the ' + mode + ' block')
    out.append(m.group(1).lower())
print(','.join(out))
PY
}
LIGHT=$(read_tokens light) || exit 2
DARK=$(read_tokens dark) || exit 2

rc=0
for spec in "light:$LIGHT:#ffffff" "dark:$DARK:#262626"; do
  IFS=: read -r mode pal surface <<< "$spec"
  echo "=================== $mode  $pal"
  node "$V" "$pal" --mode "$mode" --surface "$surface" || rc=1
  echo "------------------- $mode · all pairs (informational)"
  node "$V" "$pal" --mode "$mode" --surface "$surface" --pairs all | sed -n '3,6p'
done
exit $rc
