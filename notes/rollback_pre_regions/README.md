# Rollback: regional breakdowns on the live site (2026-11-10)

Pre-deploy bytes of everything the region deploy touched. On the box the same
files live in `/opt/ewb-live/rollback_pre_regions_20261110/`.

| File here                 | Restore to (on the box)        | md5 (pre-deploy) |
|---------------------------|--------------------------------|------------------|
| `dashboard_app.js`        | `/opt/ewb-live/dashboard/app.js`         | `77791cfb…` |
| `dashboard_mergedmap.js`  | `/opt/ewb-live/dashboard/mergedmap.js`   | `6d8b21c8…` |
| `dashboard_index.html`    | `/opt/ewb-live/dashboard/index.html`     | `0ad31534…` |
| `dashboard_styles.css`    | `/opt/ewb-live/dashboard/styles.css`     | `de3bbd18…` |
| `ewb_live_pipeline.py`    | `/opt/ewb-live/ewb_live/pipeline.py`     | `9b0222ee…` |
| `ewb_live_serve.py`       | `/opt/ewb-live/ewb_live/serve.py`        | `46ce1b3a…` |

These are fable-review's landed redesign bytes (`aaTman/fable-review` @ `b901e2e`)
— i.e. rolling back restores the site exactly as it stood before regions.

Rollback recipe (single-file scp only; never a tree sync while other lanes have
WIP in their worktrees):

```bash
cd /opt/ewb-live/rollback_pre_regions_20261110
cp dashboard_app.js /opt/ewb-live/dashboard/app.js
cp dashboard_mergedmap.js /opt/ewb-live/dashboard/mergedmap.js
cp dashboard_index.html /opt/ewb-live/dashboard/index.html
cp dashboard_styles.css /opt/ewb-live/dashboard/styles.css
cp ewb_live_pipeline.py /opt/ewb-live/ewb_live/pipeline.py
cp ewb_live_serve.py /opt/ewb-live/ewb_live/serve.py
rm -f /opt/ewb-live/data/results/scorecard_by_region.json \
      /opt/ewb-live/data/results/sample/scorecard_by_region.json
systemctl restart ewb-live-web.service   # serve.py change needs a restart
```

`scorecard.json` / `events.json` / `scores.json` were NOT touched by the region
deploy (md5s checked before and after), so a rollback needs no data rebuild.
The region file is additive. Roll back app.js together with serve.py: the new
app.js lazy-fetches `scorecard_by_region.json` and needs the 204 that the new
serve.py gives it (old app.js + old serve.py, or new app.js + new serve.py,
are both console-clean; the mixed pair logs a 404).
