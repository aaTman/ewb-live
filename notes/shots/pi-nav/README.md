# pi-nav screenshots — `make-nicer`

Headless Chromium on the LXC box. `before-*` = the untouched baseline
(`ecb4421`, a copy of live `/opt/ewb-live`) served on port 8063; `after-*` = this
branch (port 8062). Same day, same data, same 1× scale factor, so the pairs are
directly comparable. Both were taken with `themePref` pinned in `localStorage`,
so "light"/"dark" are the two themes and not the OS default.

| shot | what it shows |
| --- | --- |
| `-00-top` | first screen at 1440×900 / 390×844 — the whole nav in context |
| `-01-full` | whole page (the nav is sticky, so it appears at the top only) |
| `-02-rail` / `-02-drawer` | the rail (desktop) / the drawer (phone), isolated |
| `-03-rail-collapsed` | the 58 px collapsed rail: glyph-only, names kept for AT |
| `-04-skiplink` | keyboard focus on the "Skip to content" link |
| `-05-jump-scorecard` | after clicking the rail's Scorecard link: section below the topbar, link marked |
| `-06-detail` | an open event: the rail sheds the section links, keeps About + settings |

Named `desktop-light`, `desktop-dark`, `phone-light`, `phone-dark`.

Read these as the nav's before/after. Two things that differ in the frame are
**pi-colors'** work, not mine: the map's colour legend (`-06-detail`, `-00-top`)
and the removed "MAP LAYERS" chip row under the map, which is complaint 3 and
was landed jointly (mergedmap.js by pi-colors in `d473c55`, markup + CSS by me).
