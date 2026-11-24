# viz v3 + heat/freeze overlay component — design spec for frontend

Owner direction 2026-11-07 (two messages, final supersedes): heat/freeze event
maps render **ONE exceedance class** (heat above P85, freeze below P15 — no
other quantiles will be computed), reference
`notes/owner_refs/2026-11-07_heatwave_reference.png`. Visual design = design's;
JS implementation = frontend's (ownership split in `notes/channel_design_frontend.md`).
Builder: `ewb_live/viz/build.py` (SCHEMA = 3, mine).

## 1. Data contract (viz.json for heat_wave / freeze)

New in schema 3 (all existing v2 fields unchanged — additive only, so v2
readers keep working; feature-detect `daily_bits`):

```jsonc
"obs": {
  "daily_bits": { "b64": "...", "ndays": N, "ny": ny, "nx": nx },   // NEW
  "daily": ["0101/1110..."],      // legacy v2 strings, same meaning, day-aligned
  "consec": {b64, shape, lo, hi}, // u8 max-consecutive-days grid (kept for duration bars)
  "max_consec": 6, "anchor_day": "2026-09-17", "footprint_cells": 1174
},
"models": {
  "graphcast": {
    "24": { "daily_bits": {...},          // NEW, same encoding
            "consec": {...}, "max_consec": 4, "footprint_cells": 900, "status": "ok" },
    "72": { "status": "beyond horizon (need 246 h, max 240 h)" }   // reason vocabulary unchanged
  }, ...   // leads: keys "24","72","120","240" (HEAT_LEADS_DEFAULT)
}
```

**Bit unpack (exact contract, unit-tested in `tests/test_viz_unit.py`):**
day-major, row-major, MSB-first; one pad tail per day:

```js
const stride = Math.ceil(ny * nx / 8);                 // bytes per day
const bytes  = Uint8Array.from(atob(daily_bits.b64), c => c.charCodeAt(0));
const on     = bytes[day * stride + (i >> 3)] & (1 << (7 - (i & 7)));  // i = y*nx + x
```

Semantics of `on == 1`: the cell exceeded the threshold at **all four
6-hourly steps** of that valid day (same rule as detection), and the cell is
land. Land is already applied — no ocean masking needed client-side. Valid
days align with `window.days[]` (index `day`). `nx/ny` match `grid.lon/lat`
lengths; grid is 0.25° for heat/freeze.

Context fields for the UI: `quantile` (0.85 | 0.15 after ewb's p85/p15
re-run; today's fixtures may still carry 0.95), `threshold_note`
("OWB ERA5 1990-2019 climatology, P85"), `truth_source`.
AR (`kind:"ar_ivt"`) is unchanged by v3.

## 2. Overlay rendering (fixes the owner-flagged defects)

- **Single color, everything else transparent.** Heat exceed: `--tier-heat`
  `#c6493c`; freeze exceed: `--tier-freeze` `#1a4f96` (same inks as the type
  chips — legend swatch ties to the type). Fill alpha 0.85 on land cells;
  below-threshold cells must not paint anything (owner bug: opaque dark
  square = no-data background drawn).
- **Crisp cell edges**: nearest-neighbour / `image-rendering: pixelated`
  canvas at the 0.25° lattice (currently we smooth-interpolate — the owner
  reference is blocky cells, keep the dashed event bbox outline, drop the
  smooth blend for heat/freeze fields; AR IVT keeps its smooth rendering).
  Optional 1 px inner edge `rgba(20,20,20,.15)` only if it stays legible.
- Basemap per theme (Esri Light Gray / Dark Gray, existing) + existing faint
  graticule. No country fills.

## 3. Panel anatomy (reference-faithful)

Per event-map panel (observed AND each model-at-lead panel — one component):

- chip, top-left inside the map, white pill in light theme / surface token in
  dark: **`Heatwave #<12-hex-id>`** or **`Freeze #<id>`** (deep-link target;
  keep `#event=<id>` routing as-is). AR panels keep their current titles.
- bottom bar over the map's lower margin (no panel background):
  - left: `Valid time: 16 Sep 2026` (day granularity — our rule is per-day;
    add title "held at all four 6-hourly steps of the day").
  - right: single swatch + label `P85` (freeze: `P15`).
- **valid-time stepper** (‹ 16 Sep ›, plus play at ½×–4× later if cheap):
  ONE stepper per event-detail view, synced across observed + all model
  panels (the comparison must step together). Default day = `obs.anchor_day`.
  Model panels that lack `daily_bits` (older schema-2 asset or `status` !=
  "ok") keep today's honest reason-line behavior.
- Existing behaviors preserved: fit-to-occupied extent with z4 clamp, synced
  pan/zoom, fullscreen, model picker (cap 4 + baseline lock), deep link
  `#event=<id>&lead=<h>`.

## 4. Copy (design-owned strings — apply in dashboard JS)

- `TYPE_CRITERIA.heat_wave`: `Daily 2 m temperature above the 85th percentile
  of local climate (ERA5 1990-2019), at least 3 consecutive days, over a
  region ≥200,000 km².`
- `TYPE_CRITERIA.freeze`: `Daily 2 m temperature below the 15th percentile of
  local climate (ERA5 1990-2019), at least 3 consecutive days, over a region
  ≥200,000 km².`
- About → Methodology first paragraph: `Heat / freeze events: days when
  2 m temperature crosses the P85 (heat) or P15 (freeze) threshold of the
  Operational WeatherBench ERA5 1990-2019 hourly climatology at every 6-hourly
  step, land-only, for ≥3 consecutive days. Event maps show the cells that
  clear the threshold on each valid day.`
- Owner decision background: p85 heat / p15 freeze replaces p95/p05
  (SPEC.md "Temperature thresholds"); zero events in a lane is legitimate —
  the calm empty state already ships (tabs/table/map/scorecard).

## 5. Controls row — design answer on `more` vs `more metrics`

Both exist because they drive different axes (hidden lead bins vs the metric
set), but two "more" buttons on one row reads as a duplicate (owner Q).
Ruling per the simplification plan (design_review round-2 minor #2):
**one disclosure per row.** The LEADS row keeps exactly one `more leads`
pill-disclosure. Remove the `more metrics` button from the controls row
entirely; move metrics expansion into each scorecard block header as a small
`+ metrics` toggle next to the type chip (single shared state — today's
`scMore`). Repeated tab clicks must never add another control (the bug the
owner found: `notes/owner_refs/2026-11-07_more_metrics_multiplying.png`).
Design will QA the controls row after the deploy (all tabs clicked ×3, event
open/close ×3, both themes) and sign off in this channel.

## 6. Fixture assets for development

- Live on the box: `http://192.168.0.121:8050/data/results/events/457f3f87e24a/viz.json`
  — heat, US Central, schema 3 (built ~21:05 UTC 2026-11-07, unit
  `ewb-viz-v3fix`, MemoryMax=12G). NOTE: current published events still carry
  `details.quantile` 0.95 (p95 run) — masks are P95-based until ewb's p85/p15
  re-run rebuilds the set; the SHAPE is identical.
- No freeze fixture exists yet (freeze lane is empty until p15 re-run); QA
  freeze styling by swapping `--tier-heat` for `--tier-freeze` in devtools.
- Rebuild protocol (design-owned, serial only, never concurrent with scoring,
  one event per unit): `ssh root@192.168.0.121 "systemd-run --unit=ewb-viz-<id>
  --property=MemoryMax=12G --property=WorkingDirectory=/opt/ewb-live --
  /opt/ewb-live/.venv/bin/python -m ewb_live.viz --events <id> --force"`.
