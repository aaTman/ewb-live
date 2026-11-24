# OWB restyle — computed contrast report (WCAG 2.1 relative-luminance formula)

Verified against the live dashboard via Playwright (computed tokens) + python computation.
Light ground: paper #ffffff / canvas #fdfdfc. Dark ground: paper #262626 / canvas #1e1e1a.
Requirement: text ≥ 4.5:1; non-text (focus ring) ≥ 3:1. `--ink-muted` is meta-only (notes/ticks/footer),
governed by the 3:1 floor for non-body text per DESIGN-OWB §1.

## Light theme

| pair | fg | bg | ratio | pass |
|---|---|---|---:|---|
| ink (matte-black) / paper | #262620 | #ffffff | 15.21 | AA ✓ |
| ink / canvas | #262620 | #fdfdfc | 14.95 | AA ✓ |
| ink-secondary / paper | #6e6e60 | #ffffff | 5.17 | AA ✓ |
| ink-secondary / canvas | #6e6e60 | #fdfdfc | 5.08 | AA ✓ |
| ink-muted / paper (meta only) | #8b8b7b | #ffffff | 3.46 | non-body, ≥3 ✓ |
| accent-link / paper | #b34500 | #ffffff | 5.57 | AA ✓ |
| better text (freeze) / paper | #1a4f96 | #ffffff | 8.07 | AA ✓ |
| worse text (heat) / paper | #c6493c | #ffffff | 4.75 | AA ✓ |
| alert-ink / alert-bg | #9c311c | #fbe9e3 | 6.23 | AA ✓ |
| focus ring / paper (non-text) | #cc4e00 | #ffffff | 4.51 | ≥3 ✓ |

## Dark theme (derived, same hue system)

| pair | fg | bg | ratio | pass |
|---|---|---|---:|---|
| ink / dark paper | #ecead8 | #262626 | 12.49 | AA ✓ |
| ink / dark canvas | #ecead8 | #1e1e1a | 13.80 | AA ✓ |
| ink-secondary / dark paper | #b9b7a4 | #262626 | 7.47 | AA ✓ |
| ink-muted / dark paper (meta only) | #8b8b7b | #262626 | 4.38 | meta-only, ≥3 ✓ |
| accent-link (dark) / dark paper | #ff8b45 | #262626 | 6.51 | AA ✓ |
| better text / dark paper | #6c9ad6 | #262626 | 5.21 | AA ✓ |
| worse text / dark paper | #e87c6e | #262626 | 5.43 | AA ✓ |
| better text / dark tint | #6c9ad6 | #2c2c26 | 4.84 | AA ✓ |
| worse text / dark tint | #e87c6e | #2c2c26 | 5.04 | AA ✓ |
| alert-ink / alert-bg | #f2a48d | #3a251f | 7.15 | AA ✓ |
| focus ring (dark) / dark paper (non-text) | #ff7a26 | #262626 | 5.81 | ≥3 ✓ |

## Dark series ladder (§2 rule "never inverted")

L* preserved order AR < freeze < heat in both themes:
- light: AR 26.1 < freeze 34.0 < heat 48.4
- dark (L+15): AR 41.0 (#99493b) < freeze 49.0 (#4d73bf) < heat 63.4 (#f57160)

## Playwright-asserted facts (both themes unless noted)

- `document.fonts.check('14px Inter')` and `'12px "IBM Plex Mono"'` true; computed body family = Inter; body bg rgb(253,253,252) light / rgb(30,30,26) dark; body weight 400, line-height 21px (1.5)
- h1 computed: 20px / 500 / −0.02em (−0.4px)
- runtime tokens (read by app.js/eventviz.js): --better/--worse bare rgb triplets, --accent #ff6000, --series-*/--paper/--border per theme
- dark theme activated through the app's own #theme-toggle button (auto→light→dark cycle)
- list view 51 rows populated; event ?event=31b6bf4a1a44 detail chips radius 999px, .k label 10.5px/+0.08em
- selected row: bg #dfdecb + ink + weight 500 (light), #3a3a30 + #ecead8 (dark)
- zero console/page errors; zero #ff6000 text nodes app-wide; no font weight > 500 in DOM (UA b/th and leaflet-bar bold capped to 500)
