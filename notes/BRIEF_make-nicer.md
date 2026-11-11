# make-nicer — aesthetic pass on the EWB Live dashboard

Worktree: /home/orca/orca/workspaces/ewb-live/make-nicer (branch aaTman/make-nicer).
Baseline commit ecb4421 = an exact copy of the LIVE code in root@192.168.0.121:/opt/ewb-live (not a git repo there).
The site is in `dashboard/` (index.html, app.js, styles.css, mergedmap.js, globalmap.js, rawmap.js, eventviz.js).

## Owner's complaints (verbatim intent)
1. "The color scale on the map is terrible. It blocks the view of the map and is uneven."
2. "Make sure the new side navigation is aesthetically pleasing and functional at the same time."
3. "Remove the duplicate heat wave / freeze / atmospheric river toggles. There's a second set of them that's completely redundant."

## Hard rules
- Edit code ONLY in this worktree. Never edit /opt/ewb-live on the box, never restart ewb-live-web, never deploy. The orchestrator deploys after review.
- All compute (servers, tests, headless browsers) runs on the LXC box root@192.168.0.121, never on this orca machine.
- Preview: build your own preview tree on the box, e.g. /opt/ewb-preview-<you>/ containing `ewb_live/` + `dashboard/` rsynced from this worktree, with `data` symlinked to /opt/ewb-live/data (read-only use). Run `python -m ewb_live.serve --port <your port>` from it with /opt/ewb-live/.venv/bin/python (nohup, in the background). Re-rsync after every edit.
- Check your work visually: take headless screenshots (playwright/chromium on the box if present; otherwise install it into a venv under /opt/ewb-preview-<you>/) at desktop 1440x900 AND phone 390x844, light and dark if the site has themes. Copy them into notes/shots/<you>/ in this worktree with before/after names. Also check the browser console for errors.
- Stay in your lane (file ownership below). To touch the other agent's area, first write the request in notes/channel_make-nicer.md and wait for an answer.
- Commit only your own paths: `git add <paths> && git commit -m "..." ` (never `git add -A`). Don't push.
- Shared append-only channel: notes/channel_make-nicer.md. Append entries headed `## <you> <UTC time> — <topic>`.
- When done, append a DONE entry: what you changed and why, the screenshot paths, the commits, and anything you're unsure about. Then WAIT. The orchestrator will review and may send you back to fix things; you are not finished until the orchestrator says ACCEPTED.
