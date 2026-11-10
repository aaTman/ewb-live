# Fix contract: qualification (min-consecutive-day) gate on the painted temperature layers

Owner bug report 2026-11-10: *"the heat wave bug which kept flashing valid points every 00z is
back. The temperature events should only begin after a minimum consecutive amount is reached.
For heat waves, that's 3."*

## Measured root cause (live site, 2026-11-10, data/results/raw_exceedance, dur.rev=v2)

The detector is clean — `ewb_live/events/temperature.py` applies
`_consecutive_filter_np(exc, MIN_CONSECUTIVE_DAYS=3, grace 1)` and no live event has
`details.max_consecutive_days < 3` (61 heat / 2 freeze events, 0 violations). The bug is in
**what the global map paints**:

1. **The provisional bit is a source of new points.** `.run` bit 7 ("today so far") is set when
   every step *known so far* of the current UTC day exceeded. At 00Z that is ONE step out of
   four, so the 00Z frame qualifies ~2x more cells than the 18Z frame. 2026-11-01, heat layer:
   prov cells 112,075 (00Z) → 86,137 → 70,255 → 60,826 (18Z); of the 00Z prov cells **52,326 have
   completed-days = 0** (16,539 by 18Z) — points that appear at 00Z and vanish inside the day.
   Total painted cells (dashboard/mergedmap.js `runCount` = completed + provisional):
   **118,191 (00Z) → 98,925 → 87,746 → 82,404 (18Z)** — the 00Z sawtooth, reintroduced through
   the provisional overlay that the dur rev v2 fix left in place.
2. **Sub-threshold days are painted as if they were events.** The heat ramp starts at 1 day
   (bins 1..7+), so 1- and 2-day cells — which the detector can never turn into a heat wave —
   are shaded. 2026-11-01 heat: completed>=1 = 65,865 cells, completed>=3 = **37,403**.

The criteria text already promises the minimum (`app.js` TYPE_CRITERIA, `index.html`: "at least
3 consecutive days") while the map paints 1-day cells. This contract makes the paint honour it.

## The rule to implement (one source of truth)

`MIN_DAYS[layer]` = the detector's minimum consecutive-day run for that event type, i.e. upstream
`temperature_events.MIN_CONSECUTIVE_DAYS` (= 3 today, and the same constant for both heat and
freeze in `scan_temperature_events`). Exposed to the frontend as **`index.dur.min`**:

```json
"dur": {"rev": "v2", "seed": "2026072500", "min": {"heat": 3, "freeze": 3}}
```

Paint rule (per cell, per layer):

* `done = r & 127` = consecutive COMPLETED days. **`done < MIN_DAYS[layer]` → paint nothing.**
  A sub-threshold cell is blank at every hour of every day.
* Colour bin comes from `done` ALONE (unchanged ramp, bin index = `done - 1`, capped at
  `bins - 1`). Never from `done + provisional`: a cell's colour must not change within a UTC day.
* `r & 128` (today so far) is a **texture only**, and only on already-painted cells
  (`done >= MIN_DAYS`): keep the existing 2px checkerboard to the next ramp shade. The
  provisional bit must never add a cell to the painted set — that is what removes the 00Z flash.
* Consequence: the painted set and its colours are byte-identical across a day's four frames;
  only the dither overlay varies within the day.
* Fallbacks, in order: `index.dur.min` present → use it; absent but `dur.rev` starts with "v2" →
  use the built-in `LAYERS[].min` (3/3); no `dur` at all / v1 sidecars → the old per-step run
  value `r`, gated by the same minimum. No `dur.min` and no `dur` must never crash.

**No data migration is required**: the `.run` encoding is unchanged, so no frame rebuild — only
`index.json` gains `dur.min` on the next (incremental) exceedance build, and the JS gate has a
built-in default so it works against the currently published data immediately.

## File ownership (do not touch files owned by the other agent)

### Reference implementation of the paint loop (frontend)

Current code (`paintFrame`, mergedmap.js ~307-330) bins on `runCount(r, durV2)` = completed +
provisional. Target shape (keep the existing variable names, LUT maths and layer draw order):

```js
    const durV2 = !!(raw.dur && String(raw.dur.rev || '').indexOf('v2') === 0);
    // qualification gate: the detector's MIN_CONSECUTIVE_DAYS per lane (index.dur.min wins)
    const minDays = (L) => (raw.dur && raw.dur.min && raw.dur.min[L.name] != null
      ? raw.dur.min[L.name] : (L.min || 0));
    ...
        if (run && ramp[li]) {
          const L = LAYERS[li];
          const r = run[li * cells + cellIdx[p]];
          const done = durV2 ? (r & 127) : r;        // v1 sidecars: whole byte is the run
          if (done < minDays(L)) continue;           // sub-threshold: blank, every hour
          // today so far (bit 7) = texture only, never a new point
          const prov = durV2 && (r & 128)
            && ((((p % cw) >> 1) + ((p / cw) | 0) >> 1) & 1);
          px[p] = ramp[li][runBin(done + (prov ? 1 : 0), L.bins)];
          continue;
        } else if (u8[li * plane + bi] & m) px[p] = packed[li];
```

`runCount` then has no callers — delete it (do not leave a dead helper). `renderLayerButtons`
is first called while `raw` is still null, so `minDays` must tolerate that (the `(L.min || 0)`
branch); it is called again from the boot path after `raw` loads, which is what makes the
index-provided minimum take effect for the legend too.

### A — frontend (dashboard/)

* `dashboard/mergedmap.js`
  * `LAYERS`: add `min: 3` to heat and freeze with a comment naming `MIN_CONSECUTIVE_DAYS`;
    rewrite both `tip` strings: sub-threshold cells are blank (they can never become an event),
    solid = consecutive completed days at/above the minimum, dithered = today so far on an
    already-qualifying cell.
  * replace `runCount` with the gate: `const minDays = (L) => (raw.dur && raw.dur.min &&
    raw.dur.min[L.name] != null) ? raw.dur.min[L.name] : L.min;` and in `paintFrame` implement
    the rule above (drop the `completed + provisional` binning). v1 fallback: `r` gated by the
    same minimum.
  * legend chips (`renderLayerButtons`): the gradient must show only the bins that can be
    painted, i.e. ramp tokens `min..bins` (not 1..bins).
  * the `LAYERS[].label` text may keep "days > P85"/"days < P15"; the tip carries the minimum.
* `dashboard/app.js` — `TYPE_CRITERIA.heat_wave` / `.freeze`: state that the map shades only
  cells at/above the 3-consecutive-day minimum the detector requires and that today's
  in-progress day is dithered on qualifying cells only.
* `dashboard/index.html` — the raw-detector-map `<li>`: same wording (heat ramp 3–7+, freeze
  shows only its 3+ bin; cells below the minimum are blank).
* `dashboard/styles.css` — only if a ramp token has to change (it should NOT: bins keep their
  day-count indices, `--dur-heat-1..7` / `--dur-freeze-1..3` stay as they are).

Verify with `node --check dashboard/mergedmap.js dashboard/app.js`.

### B — backend + tests

* `ewb_live/viz/exceedance.py`
  * publish `index["dur"]["min"] = {"heat": <MIN>, "freeze": <MIN>}` derived from the detector
    constant (upstream `temperature_events().MIN_CONSECUTIVE_DAYS`) — NOT a literal 3 in two
    places. Keep `DUR_REV = "v2"` (sidecar semantics unchanged) and document the new key plus
    the paint rule in the module docstring (the docstring is the published contract).
* `tests/test_raw_exceedance.py` — add a test that the published `dur.min` maps both temperature
  lanes to the detector constant, and that it survives an incremental index rewrite. Read the
  existing tests first (`test_run_sidecar_v2_in_incremental_build`, `test_build_frames_incremental`)
  and follow their monkeypatching style; the heavy `build()` path is box-only, so test the
  index-dur block through the smallest real seam you can find (extract a tiny pure helper in
  exceedance.py if needed, e.g. `dur_block(seed_tag)` returning the dict, and have `build()` use
  it — do not change any other behaviour).
* `tests/e2e/test_dashboard_fe.py` — extend `_build_raw_fixture` so it can write `.run`
  sidecars + `index.dur` (`rev: "v2"`, `min: {"heat": 3, "freeze": 3}`), and add one Playwright
  test that asserts the gate on synthetic data: a cell with completed=2 paints NOTHING at any of
  the four frames, a cell with completed=3 paints the heat ramp bin for 3 days identically at all
  four frames, and a cell with completed=0 + provisional set paints NOTHING at 00Z. Reuse the
  existing pixel-probe helpers in that file (look for how other raw-map tests read
  `#raw-canvas`). If Playwright cannot run in this environment, still write the test and report
  that it was not executed.

## Hard rules

* Do not touch the climatology, the event detection, the `.run`/`.bin` codecs, or `LAYER_REV`.
* Do not commit, push, deploy, or sync to the `ewb` box. No `ops/remote.sh` calls.
* Only edit the files you own (listed above). Other agents are working in this tree.
* Report: files changed, the exact rule as implemented, and any place where you had to deviate.

## Result (implemented 2026-11-10)

Rule as shipped: a temperature cell paints only when its COMPLETED-day streak >= the lane
minimum (3, `MIN_CONSECUTIVE_DAYS`); the colour bin comes from that streak alone; bit 7 is a
dither texture on already-qualifying cells only. `index.dur.min` is published by the exceedance
build (read from the upstream constant via `exceedance.dur_block()`); the JS default is the same
value, so the gate is live as soon as the JS ships — **no frame rebuild, no data migration**.

Measured on the published sidecars (2026-11-01, heat lane, live `ewb.espr.ai` data):

| frame | painted cells, old rule | painted cells, new rule |
|---|---|---|
| 00Z | 118,191 | 37,403 |
| 06Z | 98,925 | 37,403 |
| 12Z | 87,746 | 37,403 |
| 18Z | 82,404 | 37,403 |

00Z/18Z painted-cell ratio 1.43x -> **1.00x**; the painted set is identical across every frame of
a UTC day (the 52,326 provisional-only cells that used to appear at 00Z are gone).

Tests: `tests/test_raw_exceedance.py` (+2: `dur.min` is the detector constant, survives an index
rewrite), `tests/e2e/test_dashboard_fe.py` (+1 Playwright paint-gate test on synthetic sidecars —
a 2-day cell and a provisional-only cell must paint nothing at any frame including 00Z, a 3-day
cell must paint identically in all four frames; it FAILS on the pre-fix renderer and PASSES on the
fixed one). Full local run: 185 passed (`-m "not network and not e2e"`), e2e 28 passed +
1 new + 1 skipped, 3 pre-existing failures (`heat_detail_reference_component`,
`heat_model_panels_forecast_at_lead`, `map_box_click_opens_detail_and_back_restores_map`) that
fail identically on the unmodified baseline — they need `data/results/events/*` assets this
worktree does not carry.

Known consequence to sign off: the freeze lane shares the 3-day minimum, so its map layer drops
from ~3,085 to ~220 painted cells (2026-11-01) and the 1–2 day ramp shades go unused. To keep
sub-threshold freeze context, set `LAYERS[].min` for freeze (and `dur.min.freeze` if published)
to 1 — one constant per lane.
