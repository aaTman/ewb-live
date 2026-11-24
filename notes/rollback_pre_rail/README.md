# rollback copy — pre-rail header/nav (owner change 2026-11-10 ~20:40Z)

The OWB rail (dashboard/{index.html,styles.css,app.js}) reached the box through a
shared lane sync (`ops/remote.sh` whole-tree rsync, box `.deploy.lock` stamped
22:34Z) — not through a deploy I ran. Live on :8050 and ewb.espr.ai as of 22:40Z.

These three files are the state the box served BEFORE that sync, reconstructed by
reverse-applying my hunks and verified:

- `index.html` == `git show HEAD:dashboard/index.html` (byte-exact; no other lane had edits)
- `styles.css` == HEAD + the legend lane's `#gm-legend`/`.lg-*` block + `sn-mark::after`
  (their work was already in the tree; a rollback must not eat it)
- `app.js`    == HEAD + the AR lane's `TYPE_CRITERIA` wording (same reason)

Roll back with (from the dogfish worktree; holds the box-side deploy lock):

    cp notes/rollback_pre_rail/{index.html,styles.css,app.js} dashboard/
    flock -w 900 -c "rsync -az dashboard/index.html dashboard/styles.css dashboard/app.js \
      root@192.168.0.121:/opt/ewb-live/dashboard/"
    curl -s http://192.168.0.121:8050/ | grep -c 'class="rail"'   # 0 = rolled back

Both copies KEEP the other lanes' uncommitted work (verified: `diff` of each copy
against `git show HEAD:` shows only their hunks), so restoring all three files
reverts the rail and nothing else. Live-verify after: the rail is gone, the
in-map legend and the heat/freeze chip wording are still there.
