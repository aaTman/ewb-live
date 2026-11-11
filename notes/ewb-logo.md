# ewb-logo — mark/favicon for the live dashboard

## Current state (deployed 2026-11-10, ~19:00Z)

The dashboard carries the **primary EWB mark — "Exceedance"** (two 45° departures and the reference
level between them): the small-size cut on the brand tile in the header, and the same drawing as the
favicon. The Brightband co-brand mark that was briefly live (an isobar whose low centre is
Brightband's disc, in their orange) has been withdrawn at the owner's request; it stays in the logo
kit for formal partnership lockups only.

| File | Change |
|---|---|
| `favicon.svg` | the mark on a matte tile (`#262620`), departures `#fdfdfc`, level Signal Ember `#e2452d` |
| `favicon.ico` | new — 16/32/48 px in one ICO (there was no ICO before) |
| `apple-touch-icon.png` | new — 180 px, full-bleed tile (iOS masks it itself) |
| `index.html` | the inline `.brand-mark` SVG inside `.brand`, before the `<h1>`; `<link rel="icon" href="favicon.ico" sizes="48x48">` + `<link rel="apple-touch-icon">` |
| `styles.css` | one block next to the `.brand` rules: `.brand-mark { align-self:center; flex:none; margin-right:2px; transform:translateY(-2px) }` |
| (2nd revision) `index.html` + `styles.css` | mark box **34 px → 24 px**, optical centring moved to a transform |

Why the mark sits **on its tile** in the header: a 34 px untiled mark beside a 20 px wordmark reads as
the UI's own expand/collapse glyph — it has no mass, and it competes with the controls. On the tile
(the kit's avatar master, the same drawing as the favicon) it reads as a logo. On the dark ground the
tile is the same near-black as the canvas, so it quietly becomes the plain reversed mark — which is
the right artwork there anyway. Three variants were compared on the site's own tokens before picking
(`ewb-logo/renders/try-header.png`).

Other decisions:

- **The level keeps the brand's Ember `#e2452d`** rather than the site's `--accent` `#ff6000`, so the
  logo is the same logo as in the kit. At 34 px the two are hard to tell apart, so brand fidelity won.
- **Small-size cut, not the standard geometry** (gaps opened to 20, reach 68) — the cut that passes the
  16 px test, which is exactly what a favicon is.
- `align-self: center` because `.brand` is baseline-aligned and the mark is a graphic; no wrapper
  element was introduced (a wrapper would change what `.brand`'s baseline aligns to).
- No behaviour change: the mark is not a link (that would need `app.js`, owned by `design`).

## The 34 px mark was too tall — fixed (2nd revision, same day)

The header's `<h1>` is 20 px on `line-height: 1.2`, i.e. a **24 px line box**. A 34 px flex item in the
baseline-aligned `.brand` row did two things the owner correctly called misalignment: it stretched the
row (the header grew past its 48 px `min-height` to 54 px) and it pulled the tile off the type's
optical centre, so the tile hung above the wordmark.

Fix: **the mark's box equals the text's line box — 24 px — and the optical centring is a
`transform: translateY(-2px)`**, which is the only property here that cannot move layout. The header is
back to its original height at every width and the tile rides the type line.

A/B evidence (`ewb-logo/renders/ab-wrap.png`): at a 1104 px viewport the header's tools cluster
(week picker, updated, About, theme) wraps to a **second row with or without the mark** — that wrapping
is the site's existing responsive behaviour at `--shell-max: min(75vw, 1280px)`, not something the logo
introduced. Only the row height and the tile's vertical position were attributable to it.

Rule for anyone else putting a graphic in `.brand`: the box must not exceed the h1's line box, or the
row grows; centre optically with a transform, not with margins or sizes.

## Ops notes for whoever deploys next

- Deployed from **this worktree** (`aaTman/live-extreme-weather-dashboard`) because its tree was
  byte-identical to `ewb:/opt/ewb-live` before the change, so an rsync dry-run listed exactly the files
  above. **If you sync from `events`, `regional-breakdowns` or `fable-review`, their `dashboard/`
  copies do not contain this change and will revert the header and the favicon** — port these five
  files over first (`ewb-logo/kit/web/{favicon.svg,favicon.ico,apple-touch-icon.png,mark-header.svg}`).
- **Static assets are cached for an hour by Cloudflare in front of ewb.espr.ai** (`server: cloudflare`,
  `age:` present, serve.py sends `cache-control: public, max-age=3600` for top-level non-HTML
  assets). After a swap, `index.html` is fresh immediately (`no-cache`, the header updates on the next
  load) but `favicon.ico` / `apple-touch-icon.png` can lag up to an hour; `/favicon.svg` refreshed at
  once this time. Don't conclude a favicon deploy failed because of it — compare against the box:
  `ssh root@192.168.0.121 md5sum /opt/ewb-live/dashboard/favicon.svg`.
- No service restart is needed for asset/markup changes (no Python touched).
- Verified: `scripts/public_scrub_check.sh https://ewb.espr.ai` → CLEAN; the served `favicon.svg` is
  byte-identical to the local master (md5 match); header screenshot taken from the live page.

## Requests / follow-ups for other owners

- **`design`** — the About dialog and `og-image.png` carry no mark. If you want it, the masters are in
  the ewb-logo worktree at `kit/masters/` (`ewb-symbol.svg` light, `ewb-symbol-white.svg` dark,
  `ewb-symbol-square.svg` on the tile) and the cut sizes are one command away. Ask for 1200×630 and I
  will place it properly rather than pasting it on.
- **`design`** — if you would rather have the primary mark *untiled* in the header, delete the `<rect>`
  from the inline SVG and give it `color: var(--matte-black)` with `fill="currentColor"` on the
  departures; the comparison sheet shows what that looks like.
- **owner** — the co-brand mark (Brightband's disc as the low's centre) is approved artwork in the kit
  but is not used on the site; it needs Brightband's written sign-off before it goes anywhere public.
