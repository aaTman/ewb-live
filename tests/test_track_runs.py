"""tracklayer draws the track per RUN of consecutive same-`live` fixes, never
as one chord across the event window (owner report 2026-11-23).

layoutStorm() strokes the life outside the event window dashed and the window
itself solid. The original filter-by-flag fed the pre-window AND the
post-window fixes of a system whose life bracketed the window into a SINGLE
dashed polyline, so its segment list joined the last pre-window fix straight
to the first post-window fix — a straight dashed chord lying across the solid
line that is in no data (58 of 124 track storms in the ne-fixes2 build were
pre-live-post; both the TC and the MLC lane). The fix splits `shown` into
maximal runs of consecutive fixes sharing the live flag, strokes each run as
its own pass, and lets the solid run borrow the fix neighbouring it on each
side so the dashed runs MEET the solid line at one shared point — every
consecutive fix pair is then drawn exactly once, no gap, no chord.

The test runs the real dashboard module under node (same node gate as
tests/test_mlc_hue.py): dashboard/tracklayer.js loads into a window stub —
the IIFE touches no DOM at load — and its exported layoutStorm is called with
an identity projection (screen px == degrees), so every drawn polyline
segment can be mapped back to the fixes it connects. The invariants:

* no pre (dashed) segment joins a pre-window fix to a post-window fix;
* every segment joins CHRONOLOGICALLY ADJACENT fixes (the chord's signature
  was a non-adjacent pair);
* pre ∪ live covers every consecutive fix pair EXACTLY once (the boundary
  borrow: the dashed runs meet the solid line with no gap and no overlap);
* splitAtDateline still applies per run (a crossing run breaks at ±180 and
  re-enters on the other side);
* the dots are untouched: shown/live flags are exactly the fixture's window.

Offline, tiny, no network, no browser.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "dashboard" / "tracklayer.js"

HARNESS = r'''
'use strict';
const fs = require('fs');
globalThis.window = {};
eval(fs.readFileSync(%SRC%, 'utf8'));            // the real module, no DOM at load
const throws = [];
function assert(cond, m) { if (!cond) throws.push('FAIL: ' + m); return !!cond; }
const T = globalThis.window.EwbTracks;
if (!assert(T && typeof T.layoutStorm === 'function',
    'layoutStorm is not exported for tests')) { throw new Error(throws.join('\n')); }

const proj = (ll) => [ll[0], ll[1]];             // identity: px == degrees
const frame = (rows) => Date.parse(rows[rows.length - 1][0] + 'Z');
const storm = (id, type, start, end, rows) =>
  ({ id, type, start, end, track: { fixes: rows } });

/* Map a drawn point back to its fix by position (identity projection);
   interpolated seam points at ±180 belong to no fix and map to -1. */
function idxMap(rows) {
  return new Map(rows.map((r, i) => [r[2] + ',' + r[1], i]));   // 'lon,lat'
}
function segPairs(st, idx) {
  const out = [];
  for (const key of ['pre', 'live'])
    for (const part of st.parts[key])
      for (let i = 1; i < part.length; i++) {
        const P = part[i - 1], Q = part[i];
        const a = idx.get(P[0] + ',' + P[1]);
        const b = idx.get(Q[0] + ',' + Q[1]);
        out.push({ key, a: a === undefined ? -1 : a, b: b === undefined ? -1 : b, A: P, B: Q });
      }
  return out;
}

/* every segment joins chronologically ADJACENT fixes; seam endpoints (-1)
   are checked separately by the dateline test below */
function assertAdjacent(tag, segs) {
  for (const s of segs) {
    if (s.a < 0 || s.b < 0) continue;
    assert(Math.abs(s.a - s.b) === 1,
      tag + ': ' + s.key + ' segment joins fix ' + s.a + ' to fix ' + s.b +
      ' (not adjacent) — a chord across the window: ' +
      JSON.stringify([s.A, s.B]));
  }
}

/* ------------------------------------------------------------ pre-live-post
   TC lane, straight eastward line: 2 pre fixes, 4 window fixes, 2 post fixes;
   the old code dashed a chord from fix 1 to fix 6 across the solid line. */
{
  const rows = [
    ['2026-01-02T00:00:00', 20, 10, 45, 1000], ['2026-01-02T06:00:00', 20, 20, 45, 1000],
    ['2026-01-02T12:00:00', 20, 30, 45, 1000], ['2026-01-02T18:00:00', 20, 40, 50, 998],
    ['2026-01-03T00:00:00', 20, 50, 55, 996], ['2026-01-03T06:00:00', 20, 60, 50, 998],
    ['2026-01-03T12:00:00', 20, 70, 40, 1002], ['2026-01-03T18:00:00', 20, 80, 35, 1004]];
  const st = T.layoutStorm(storm('tr1', 'tropical_cyclone',
    '2026-01-02T12:00:00', '2026-01-03T12:00:00', rows), proj, frame(rows), false);
  const segs = segPairs(st, idxMap(rows));
  assertAdjacent('pre-live-post TC', segs);
  // the named regression: NO dashed segment may span the live window (2..5)
  for (const s of segs.filter((s) => s.key === 'pre'))
    assert(!(Math.min(s.a, s.b) < 2 && Math.max(s.a, s.b) > 5),
      'a dashed segment spans from a pre-window fix to a post-window fix: ' +
      JSON.stringify([s.A, s.B]));
  // the dashed life is TWO polylines (pre run, post run), not one chord
  assert(st.parts.pre.length === 2, 'pre dash is ' + st.parts.pre.length +
    ' polylines, expected 2 (one per run)');
  // coverage: every consecutive fix pair is drawn EXACTLY once, no gap/chord
  const pairs = segs.map((s) => Math.min(s.a, s.b)).sort((x, y) => x - y).join(',');
  assert(pairs === '0,1,2,3,4,5,6', 'pairs drawn: ' + pairs + ' (want each of 0..6 once)');
  // the dots: shown + live flags are exactly the window, unchanged
  assert(st.shown.length === 8, 'shown must keep all 8 fixes');
  assert(st.shown.map((f) => (f.live ? 1 : 0)).join() === '0,0,1,1,1,1,0,0',
    'the live flag on the dots changed');
}

/* ------------------------------------------------- dateline crossing runs
   MLC lane crossing ±180 at the pre/live join: the borrowed fix makes the
   solid run ITSELF cross the seam, so splitAtDateline must still break it
   at ±180 and re-enter on the other side. */
{
  const rows = [
    ['2026-03-01T00:00:00', 30, 168, 992, 0], ['2026-03-01T06:00:00', 30, 174, 988, 0],
    ['2026-03-01T12:00:00', 30, 178, 984, 0], ['2026-03-01T18:00:00', 30, -178, 980, 0],
    ['2026-03-02T00:00:00', 30, -172, 978, 0], ['2026-03-02T06:00:00', 30, -166, 982, 0],
    ['2026-03-02T12:00:00', 30, -160, 990, 0], ['2026-03-02T18:00:00', 30, -154, 996, 0]];
  const st = T.layoutStorm(storm('tr2', 'midlat_cyclone',
    '2026-03-01T18:00:00', '2026-03-02T12:00:00', rows), proj, frame(rows), false);
  const segs = segPairs(st, idxMap(rows));
  assertAdjacent('dateline MLC', segs);
  // pre run ends at the last west-of-seam fix (178); the live run BORROWS that
  // fix, which pushes the seam crossing into the solid polyline: it must break
  // at +180 and re-enter at -180, while the dashed pre run stays whole.
  assert(st.parts.live.length === 2, 'the borrowing live run must break into 2 parts, got ' +
    st.parts.live.length);
  if (st.parts.live.length === 2) {
    const p0 = st.parts.live[0], p1 = st.parts.live[1];
    assert(p0[0][0] === 178 && p0[p0.length - 1][0] === 180,
      'live part 1 must start at the borrowed fix and end on the +180 seam: ' + JSON.stringify(p0));
    assert(p1[0][0] === -180 && p1[1][0] === -178,
      'live part 2 must re-enter at -180: ' + JSON.stringify(p1));
  }
  // pre run and post run each stay their own dashed polyline (2 parts)
  assert(st.parts.pre.length === 2, 'pre dash must be 2 runs');
  if (st.parts.pre.length === 2) {
    const preEnds = st.parts.pre.map((p) => p[p.length - 1][0]);
    assert(preEnds.indexOf(180) < 0, 'the pre run itself never crosses the seam and '
      + 'must not gain a seam point: ' + JSON.stringify(st.parts.pre));
  }
}

/* --------------------------------------------------------- single-run cases
   A storm revealed wholly BEFORE its window (all dashed, no live run) and a
   storm wholly INSIDE it (all solid, no dash): one run must never be split
   or joined to a neighbour that does not exist. */
{
  const rows = [['2026-05-01T00:00:00', 10, 100, 40, 1002],
                ['2026-05-01T06:00:00', 10, 104, 42, 1000],
                ['2026-05-01T12:00:00', 10, 108, 44, 998]];
  const pre = T.layoutStorm(storm('tr3', 'tropical_cyclone',
    '2026-05-02T00:00:00', '2026-05-03T00:00:00', rows), proj, frame(rows), false);
  assert(pre && pre.parts.pre.length === 1 && pre.parts.live.length === 0,
    'all-pre storm: one dashed run, no solid run');
  assertAdjacent('all-pre', segPairs(pre, idxMap(rows)));
  const live = T.layoutStorm(storm('tr4', 'tropical_cyclone',
    '2026-05-01T00:00:00', '2026-05-02T00:00:00', rows), proj, frame(rows), false);
  assert(live && live.parts.pre.length === 0 && live.parts.live.length === 1,
    'all-live storm: one solid run, no dash');
  assertAdjacent('all-live', segPairs(live, idxMap(rows)));
}

if (throws.length) { for (const t of throws) console.error(t); process.exit(1); }
console.log('OK: every drawn segment joins adjacent fixes; no dashed chord across the window');
'''


def test_track_runs_never_stroke_a_chord_across_the_window():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not available")
    js = HARNESS.replace("%SRC%", json.dumps(str(SRC)))
    r = subprocess.run([node, "-e", js], check=False,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"node harness failed (rc={r.returncode})\n" \
                              f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
