# Rollback: footprint-assignment + small-sample cue (2026-11-10, ~11:05Z)

> **Do not use this recipe as-is after 13:01Z.** It restores `dashboard/app.js`,
> `dashboard/mergedmap.js` and `dashboard/index.html` from 11:05Z, which predates
> term_720dbed3's paint-gate deploy (heat 3 / freeze 1, live 13:01:28Z) — running
> it would silently revert that lane's fix. If you must undo only the regions
> work now, diff the current live files against these and remove the region code
> by hand, or ask the regions lane (`notes/channel_regions.md`).

These 8 files are the live bytes **immediately before** the two owner follow-ups
(>25 % footprint-overlap assignment, small-sample cue). They already contain the
regional-breakdowns feature as deployed at 04:32Z — this is a rollback of the
second round only, not of regions.

Copies also live on the box at `/opt/ewb-live/rollback_pre_regions_v2_20261110/`
(same files, `/` flattened to `_`).

| this dir | on the box |
|---|---|
| `dashboard_app.js` | `dashboard/app.js` |
| `dashboard_mergedmap.js` | `dashboard/mergedmap.js` |
| `dashboard_index.html` | `dashboard/index.html` |
| `dashboard_styles.css` | `dashboard/styles.css` |
| `ewb_live_regions.py` | `ewb_live/regions.py` |
| `ewb_live_pipeline.py` | `ewb_live/pipeline.py` |
| `ewb_live_weekly.py` | `ewb_live/weekly.py` |
| `scripts_build_region_scores.py` | `scripts/build_region_scores.py` |

Rollback recipe (single-file copies; never a tree sync while other lanes have WIP):

```bash
cd /opt/ewb-live
for f in dashboard/app.js dashboard/mergedmap.js dashboard/index.html dashboard/styles.css \
         ewb_live/regions.py ewb_live/pipeline.py ewb_live/weekly.py scripts/build_region_scores.py; do
  cp -a "rollback_pre_regions_v2_20261110/$(echo $f | tr / _)" "$f"
done
. .venv/bin/activate && python scripts/build_region_scores.py data/results
systemctl restart ewb-live-web.service
```

The rebuild restores the centroid-rule JSON without the `assignment`/`events`
blocks; the dashboard treats a missing `events` map as "use client geometry" and a
missing `min_sample` as 5, so the UI stays consistent either way. No scoring data
changes — `scorecard.json` and `scores.json` were md5-checked before and after the
deploy — so a rollback needs no data rebuild beyond the region file.
