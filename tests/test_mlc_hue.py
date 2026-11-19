"""The MLC hue has ONE source of truth (owner decision 2026-11-19).

Midlatitude cyclone is RED — the chips palette's `#a0312d` light / `#cf4a55`
dark — and the earlier amber re-hue is gone from the product. This test exists
because the hue has already forked once (amber in the lane tree vs red in the
chips palette, a question the owner had to settle by hand): the CSS token is
the only definition, every surface (chips, legend, map, track view) reads it
through `--series-mlc`, and the banned amber hexes stay banned. The dataviz
gate itself is `scripts/chips_palette_check.sh` (validator vs the other six
lanes), run here when node is available.

Offline, tiny, no network.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "dashboard" / "styles.css"

LIGHT_HEX = "#a0312d"
DARK_HEX = "#cf4a55"
# the retired amber re-hue (light/dark). Any appearance in shipped source means
# the hue forked again — it is history in notes/, which is a log, not the design.
BANNED = ("7d5200", "d9a441")


def _token_block(header: str) -> str:
    m = re.search(rf"{re.escape(header)}\s*\{{.*?\n\}}", CSS.read_text(), re.S)
    assert m, f"no {header} block in styles.css"
    return m.group(0)


def test_series_mlc_is_defined_once_per_theme_with_the_owner_red():
    for header, want in ((":root", LIGHT_HEX), ('html[data-theme="dark"]', DARK_HEX)):
        defines = re.findall(r"--series-mlc\s*:\s*([^;]+);", _token_block(header))
        assert len(defines) == 1, (header, defines)
        assert want in defines[0].lower(), (header, defines)


def test_no_amber_survives_in_shipped_source():
    offenders = []
    for path in list((ROOT / "dashboard").iterdir()) + list((ROOT / "ewb_live").rglob("*")):
        if not path.is_file() or path.suffix not in {".js", ".py", ".css", ".html"}:
            continue
        text = path.read_text(errors="ignore").lower()
        if any(b in text for b in BANNED):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"the retired amber is back in: {offenders}"


def test_the_surfaces_take_the_hue_from_the_token_not_a_literal():
    app = (ROOT / "dashboard" / "app.js").read_text()
    assert re.search(r"midlat_cyclone:\s*'--series-mlc'", app), \
        "app.js must map the lane to the token, not a colour"
    for name in ("tracklayer.js", "trackviz.js"):
        src = (ROOT / "dashboard" / name).read_text()
        # the lane hue reaches canvas only through cssVar(--series-…) lookups
        for hex_lit in re.findall(r"#[0-9a-fA-F]{6}\b", src):
            assert hex_lit.lower() not in (LIGHT_HEX, DARK_HEX), \
                f"{name} hard-codes the MLC red — read it from --series-mlc"


def test_the_seven_lane_palette_still_passes_the_dataviz_gate():
    if shutil.which("node") is None:
        import pytest
        pytest.skip("node is not available")
    r = subprocess.run(["bash", "scripts/chips_palette_check.sh"],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
