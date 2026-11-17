"""Simple AR ramp tests to verify the implementation."""
import pytest
pytestmark = pytest.mark.e2e

import json
import os
import sys
import tempfile
import shutil
import base64
import zlib
import pytest
from playwright.sync_api import sync_playwright, expect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from tests.e2e.test_dashboard_fe import _gd_server, _build_gd_fixture, _build_raw_fixture, RAW_TS


@pytest.fixture(scope="module")
def ar_ramp_server(tmp_path_factory):
    """AR ramp lane: index carries `ivt` schema 3 and every frame has a `.ivt` sidecar."""
    root = _build_gd_fixture(str(tmp_path_factory.mktemp("fe-ar-ramp-simple")),
                             with_timeline=False, raw_ivt=True)
    srv, url = _gd_server(root)
    yield url
    srv.shutdown()


def test_ar_ramp_legend_visible(ar_ramp_server, browser):
    """Test that the AR ramp legend is visible when AR layer is on."""
    page = browser.new_page()
    page.goto(ar_ramp_server + "/#map=2025110612&layers=a")
    page.wait_for_selector("#raw-canvas:not([hidden])", timeout=20000)
    page.wait_for_function("MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(2000)
    
    # Check that the legend is visible
    legend = page.locator("#gm-legend:not(.hidden)")
    expect(legend).to_be_visible()
    
    # Check for 5 swatches
    swatches = page.locator("#gm-legend .swatch")
    assert swatches.count() == 5, f"Expected 5 legend swatches, got {swatches.count()}"
    
    # Check legend text
    leg_text = page.inner_text("#gm-legend")
    assert "AR IVT" in leg_text, f"Expected 'AR IVT' in legend, got: {leg_text}"
    
    page.close()


def test_ar_ramp_paints_green_pixels(ar_ramp_server, page_ctx_with_logs):
    """Test that the AR ramp paints green pixels on the canvas.
    2025110606 is the last frame at/below the fixture cutoff — 2025110612
    paints NOTHING (past-cutoff frames are boxes-only), which is why this
    test used to fail with 0 green pixels."""
    page = page_ctx_with_logs
    page.goto(ar_ramp_server + "/#map=2025110606&layers=a")
    page.wait_for_selector("#raw-canvas:not([hidden])", timeout=20000)
    page.wait_for_function("MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(5000)  # wait for frame to load
    
    # Check for green pixels on the canvas
    # AR ramp colors are all green (R and B similar, G higher)
    green_count = page.evaluate("""() => {
        const cv = document.querySelector('#raw-canvas');
        const ctx = cv.getContext('2d'), img = ctx.getImageData(0, 0, cv.width, cv.height);
        const px = new Uint32Array(img.data.buffer);
        let count = 0;
        for (let i = 0; i < px.length; i++) {
            const r = px[i] & 255, g = (px[i] >> 8) & 255, b = (px[i] >> 16) & 255;
            // Green pixels: G > R and G > B, and not too close to white
            if (g > r + 10 && g > b + 10 && r < 200 && g < 230) {
                count++;
            }
        }
        return count;
    }""")
    assert green_count > 0, f"Expected green pixels > 0, got {green_count}"
    print(f"Found {green_count} green pixels")
    
    page.close()


def test_ar_ramp_legend_hidden_when_ar_off(ar_ramp_server, browser):
    """The AR ramp leaves the legend when the AR layer is off.
    (The strip itself never goes display:none — its height is a CLS
    reservation and the surviving lanes still need a key; per 2026-11-11
    redesign the AR GROUP disappearing is the contract.)"""
    page = browser.new_page()
    page.goto(ar_ramp_server + "/#map=2025110612&layers=h")
    page.wait_for_selector("#raw-canvas:not([hidden])", timeout=20000)
    page.wait_for_function("MergedMap._dbg().mounted === true", timeout=20000)
    page.wait_for_timeout(2000)

    assert page.locator("#gm-legend .lg-group").count() == 1        # heat only
    assert page.locator("#gm-legend .lg-group[data-layer=a]").count() == 0
    assert page.locator("#gm-legend .lg-group[data-layer=h]").count() == 1

    page.close()


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page_ctx_with_logs(browser):
    """Page context that captures console logs."""
    page = browser.new_page()
    logs = []
    page.on("console", lambda m: logs.append(m.text))
    page.logs = logs
    yield page
    page.close()
