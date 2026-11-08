# Spec: OWB visual identity restyle (owner direction 2026-11-06 17:xx)

Match the FONT STYLES and COLOR CUES of Brightband's OWB (owb.brightband.com)
across the whole dashboard. The binding rules are in the legacy spec
`/opt/ewb-dashboard/docs/DESIGN-OWB.md` on the ewb box
(ssh root@192.168.0.121 'cat /opt/ewb-dashboard/docs/DESIGN-OWB.md') — READ IT
FIRST, in full. Live-site facts already verified: OWB :root tokens match the
doc (canvas #fdfdfc, tint #f2f1e6, alabaster #dfdecb, paper #fff, matte-black
#262620, ink-secondary #6e6e60, ink-muted #8b8b7b, border #d9d9cd,
orange #ff6000); faces are Matter/MatterMono (commercial, NOT licensable) so
the doc's substitute stack applies: Inter (400/500/600) + IBM Plex Mono
(400/500), both OFL, self-hosted from dashboard/vendor/fonts/ (NO Google CDN).

## You own
- dashboard/styles.css (full restyle; PRESERVE every existing selector —
  app.js generates DOM with those classes; adding rules/selectors is fine,
  removing is not).
- dashboard/index.html (link the fonts.css; nothing else structural).
- dashboard/vendor/fonts/ (download woff2 latin 400/500/600 Inter + 400/500
  IBM Plex Mono from jsdelivr @fontsource files, e.g.
  https://cdn.jsdelivr.net/npm/@fontsource/inter/files/inter-latin-400-normal.woff2
  — verify each file is a real woff2 (`file` magic wOF2), sizes ~15-120 KB)
  + dashboard/vendor/fonts.css (@font-face rules, font-display: swap).

## Restyle rules
1. Light theme = OWB faithfully: :root gets the full token set from the doc
   §1 verbatim (--canvas --paper --alabaster --alabaster-mid --tint --border
   --matte-black --ink-secondary --ink-muted --stone-grey --accent
   --accent-link --accent-focus --alert-bg --alert-ink, series §7.1/7.2
   hexes incl. softs and medals) PLUS the type scale (--text-2xs 10.5 ...
   --text-xl 26) and --font-sans/--font-mono per §3. Map ALL existing app
   vars onto the new system: --bg→--canvas, --bg-panel→--paper,
   --bg-inset→--tint, --border→#d9d9cd, --text→--matte-black,
   --text-dim→--ink-secondary, --accent→#ff6000 for graphics only, plus a
   separate --accent-ui #b34500 for links/focus (doc rule: never orange text).
   Better/worse cues keep semantics but retune to OWB discipline: better =
   --accent-link family or ink (choose per doc rules: text must pass AA on
   paper; --series-freeze #1a4f96 works for 'better' text, heat #c6493c for
   'worse'), NOT rgb triplets if possible — keep the --better/--worse rgb
   vars existing (app.js uses rgba()) but retune values to 26,79,150 /
   198,73,60.
2. Typography per §3 discipline: weights 400/500 only (600 ceiling), signed
   letter-spacing ladder, micro-labels uppercase +0.08em --ink-muted, mono
   numerals tabular, sizes only from the 7-step scale (allow 11.5/12.5/14/20
   etc as defined), line-heights 1.5 prose / 1.2 numbers / 1.1 micro-labels.
3. Spacing/§4 ladder, radii 3/4/6/999, hairline rules (--border outer,
   --alabaster-mid internal), states §5 (hover --tint, selected --alabaster +
   3px matte-black left bar, focus-visible 2px --accent-focus, disabled via
   ink not opacity), apply to panels, cards, buttons, chips, tables, nav.
4. DARK MODE: our app has a [data-theme=dark] toggle the legacy doc lacked —
   keep it working by DERIVING a dark set on the same hue system (canvas
   #1e1e1a, paper #262626, tint #2c2c26, alabaster #3a3a30, border #454538,
   ink #ecead8, ink-secondary #b9b7a4, ink-muted #8b8b7b, accent stays
   #ff6000 (bright enough on dark), series lighten ~L+15 keeping the
   lightness ladder AR<freeze<heat INVERTED is NOT allowed — invert only the
   ground, keep series hues recognizable; verify text contrast ≥4.5 on dark
   paper for ink). Update [data-theme=dark] rules (selector already used by
   app.js — check it) — same var names.
5. Event detail + charts: scorecard table per doc §6.3 (sticky header, mono
   right-aligned numerics, type badges with glyph + soft border, rank chips
   matte-black numerals only); verdict chips/mini-table follow chip rules
   (radius 999, --text-2xs labels); plotly default colorway → the series
   hexes + dash patterns (CSS can't reach plotly — JS is another agent's
   file; here only provide CSS vars --series-* the JS reads).
6. Keep the map (Leaflet) + its chrome classes working; tiles are a separate
   agent's concern — just don't break .leaflet container sizing rules.

## Verify (offline, this container)
- Serve: .venv/bin/python -m ewb_live.serve --port 8075 (static only, OK).
- Playwright (.venv chromium --no-sandbox): desktop 1440x900 + mobile 400x850,
  both themes; open list view, an event detail (?event=31b6bf4a1a44), dark
  toggle; assert: fonts loaded (document.fonts.check('14px Inter')), zero
  console errors, computed body fontInter, bg rgb(253,253,252) light;
  screenshots to notes/screenshots/owb-restyle-{light,dark,mobile}.png.
- Contrast spot-check with a tiny python script: text colors vs grounds
  ≥ 4.5 (light) and ≥ 4.5 (dark) for ink/secondary; report the table.
Deliver: summary + contrast table + screenshots. Do not touch app.js,
eventviz.js, or anything under ewb_live/.
