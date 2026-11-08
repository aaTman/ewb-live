# guardian-design audit harness (Playwright, run from repo root with .venv/bin/python)
Copy this dir to /tmp/gd_scratch (scripts import `gd` from there; shots go to /tmp/gd_scratch/shots; axe.min.js from an axe-core 4.10 install).
- gd.py       helpers; `GD_LOCAL=1` serves dashboard/ files from the worktree (pre-deploy preview) while data comes from the live site.
- a2.py <ids> <w> <light|dark> <tag>   detail-section screenshots.   m1.py <ids> <theme> <name> <crop_h>   2-up contact sheets.
- a5.py <w> <theme> <tag> '<sel,sel>'  section screenshots.         b6.py <widths> <theme> <name> <h>   full-page strips.
- a8.py axe-core runs (both themes, home/about/event).               b1.py content cross-check of every event story vs events_selected.json.
- gd_rebuild.py  run ON THE BOX (scp to /root) under `systemd-run --property=MemoryMax=12G`: serial viz rebuild WITHOUT the CLI raw_exceedance tail.
