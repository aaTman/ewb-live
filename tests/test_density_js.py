"""The event-density grid maths is pure and lives in dashboard/density.js.

The map paints one colour per grid cell for "how many events of the current
tab cover this cell". That count is the only thing a reader can check against
the event list, so the geometry lives in one testable file with no DOM:
`countGrid` (bbox → per-cell counts, dateline-aware) and `scaleOf` (the ramp's
min/max for the current view). These cases run the real file through node.

Offline, tiny, no network.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DENSITY = ROOT / "dashboard" / "density.js"

pytestmark = pytest.mark.skipif(
	shutil.which("node") is None, reason="node is not available"
)


def _run_js(program: str) -> object:
	"""Evaluate `program` in node with `EwbDensity` required, return its stdout.

	The program must `console.log` one JSON value, which is parsed and
	returned so the assertions stay in Python.

	Args:
		program: JavaScript body with `D` bound to the density module.

	Returns:
		The JSON value the program printed.
	"""
	src = f"const D = require({str(DENSITY)!r});\n{program}"
	r = subprocess.run(
		["node", "-e", src], capture_output=True, text=True, timeout=60, check=False
	)
	assert r.returncode == 0, f"node failed: {r.stderr[-2000:]}"
	return json.loads(r.stdout)


def _grid(events: list[list[float]], cell_deg: int = 10) -> dict[str, object]:
	"""Return `{nx, ny, counts}` for `events` (bboxes) on a `cell_deg` grid."""
	body = (
		"const g = D.countGrid(" + json.dumps(events) + ", " + str(cell_deg) + ");"
		"console.log(JSON.stringify({ nx: g.nx, ny: g.ny, cellDeg: g.cellDeg,"
		" counts: Array.from(g.counts) }));"
	)
	return _run_js(body)


def _cell(g: dict[str, object], lat: float, lon: float) -> int:
	"""Return the count of the grid cell holding `lat`/`lon` (row 0 at -90)."""
	nx = int(g["nx"])
	i = int((lat + 90) / int(g["cellDeg"]))
	j = int((lon + 180) / int(g["cellDeg"]))
	return int(g["counts"][i * nx + j])  # type: ignore[index]


def test_one_box_counts_one_in_its_cells_and_zero_elsewhere() -> None:
	"""A single bbox paints 1 across its own cells and nothing outside."""
	g = _grid([[10.0, 20.0, 0.0, 20.0]])
	assert _cell(g, 15.0, 5.0) == 1
	assert _cell(g, 15.0, 15.0) == 1
	assert _cell(g, 5.0, 5.0) == 0
	assert _cell(g, 25.0, 5.0) == 0
	assert _cell(g, 15.0, -5.0) == 0
	assert _cell(g, 15.0, 25.0) == 0
	# 10° cells: the box is one row (lat 10–20) by two columns (lon 0–20).
	assert sum(int(c) for c in g["counts"]) == 2  # type: ignore[union-attr]


def test_two_overlapping_boxes_give_two_in_the_overlap() -> None:
	"""Overlap counts: the shared cells see both events."""
	g = _grid([[10.0, 20.0, 0.0, 20.0], [10.0, 20.0, 10.0, 30.0]])
	assert _cell(g, 15.0, 15.0) == 2
	assert _cell(g, 15.0, 5.0) == 1
	assert _cell(g, 15.0, 25.0) == 1
	assert _cell(g, 15.0, 35.0) == 0


def test_a_dateline_box_counts_both_halves_and_not_lon_zero() -> None:
	"""`[10, 20, 170, -170]` is the eastward arc over the pole seam, not 170° of
	the middle of the map."""
	g = _grid([[10.0, 20.0, 170.0, -170.0]])
	assert _cell(g, 15.0, 175.0) == 1
	assert _cell(g, 15.0, -175.0) == 1
	assert _cell(g, 15.0, 0.0) == 0
	assert _cell(g, 15.0, 165.0) == 0
	assert sum(int(c) for c in g["counts"]) == 2  # type: ignore[union-attr]


def test_scale_of_max_is_the_highest_count() -> None:
	"""The ramp's top stop is the busiest cell in the current view."""
	out = _run_js(
		"const g = D.countGrid([[10,20,0,10],[10,20,10,20],[10,20,20,30]], 10);"
		"console.log(JSON.stringify(D.scaleOf(g, 1)));"
	)
	assert out["max"] == 1  # type: ignore[call-overload]
	out2 = _run_js(
		"const g = D.countGrid([[10,20,0,20],[10,20,10,30],[10,20,5,25]], 10);"
		"console.log(JSON.stringify(D.scaleOf(g, 1)));"
	)
	assert out2["max"] == 3  # type: ignore[call-overload]


def test_scale_of_clamps_the_minimum() -> None:
	"""A minimum below 1 means 1; a minimum above the top means the top."""
	prog = (
		"const g = D.countGrid([[10,20,0,20],[10,20,10,30]], 10);"
		"console.log(JSON.stringify([D.scaleOf(g, 0), D.scaleOf(g, 99), "
		"D.scaleOf(g, 2)]));"
	)
	scale0, scale99, scale2 = _run_js(prog)
	assert scale0["min"] == 1
	assert scale2["min"] == 2
	assert scale99["min"] == scale99["max"]
	for s in (scale0, scale99, scale2):
		assert 1 <= s["min"] <= s["max"]


def test_an_empty_view_scales_to_zero() -> None:
	"""No events (or none in the region) means an empty ramp, min 1, max 0."""
	out = _run_js(
		"console.log(JSON.stringify(D.scaleOf(D.countGrid([], 10), 1)));"
	)
	assert out == {"min": 1, "max": 0}


def test_index_html_loads_density_before_mergedmap() -> None:
	"""`mergedmap.js` reads `window.EwbDensity`, so script order is load-bearing."""
	html = (ROOT / "dashboard" / "index.html").read_text()
	dens = html.find('src="density.js"')
	merged = html.find('src="mergedmap.js"')
	assert dens != -1, "index.html must load density.js"
	assert merged != -1
	assert dens < merged


def test_the_density_layer_takes_its_hue_from_a_token_not_a_literal() -> None:
	"""The density ramp's colour is a CSS token, so it follows the theme flip.

	Same rule as every other map surface (`tests/test_mlc_hue.py`): no shipped
	JS may bake a hex for a lane hue, or dark mode paints the light ramp.
	"""
	js = (ROOT / "dashboard" / "mergedmap.js").read_text()
	start = js.index("function paintDensity")
	block = js[start : js.index("function densGroupHtml", start)]
	assert "densTabToken" in block, "paintDensity must resolve the lane hue from a token"
	for lit in re.findall(r"#[0-9a-fA-F]{3,6}\b", block):
		raise AssertionError(f"paintDensity hard-codes {lit}; read a --series- token")
