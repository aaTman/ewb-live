"""Scratch: surface the renderMaps exception on the freeze fixture."""
import pytest
from test_dashboard_fe import FREEZE_VIZ_ID, freeze_server, page_ctx  # noqa: F401 (fixtures)

pytestmark = pytest.mark.e2e


def test_debug_render(freeze_server, page_ctx):
    page = page_ctx
    page.goto(freeze_server + "/")
    page.wait_for_selector("#event-table tbody tr, .gm-lrow", timeout=15000)
    out = page.evaluate("""async (id) => {
        const viz = await EventViz.load(id);
        const host = document.createElement('div');
        document.body.appendChild(host);
        try {
            EventViz.render(host, viz, { id: id, type: 'freeze', title: 'T' }, {});
            return { ok: true, panels: host.querySelectorAll('.ev3-panel').length,
                     rows: host.querySelectorAll('.ev3-layer').length,
                     slider: host.querySelectorAll('.ev3-slider').length };
        } catch (e) {
            return { ok: false, err: String((e && e.stack) || e) };
        }
    }""", FREEZE_VIZ_ID)
    print(out)
    assert out.get("ok"), out.get("err")
    assert out["panels"] == 1 and out["rows"] >= 2 and out["slider"] == 1
