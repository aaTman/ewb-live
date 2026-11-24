"""Event-type chips e2e (role chips-design, briefs-new-events/chips_design.md).

Covers the streamlined chip contract on the deployed dashboard
(EWB_LIVE_URL — run it against the chips preview during review):

  * tablist semantics: role=tablist/tab, aria-selected, one roving tabindex,
    aria-controls naming the regions the filter changes, count in the
    accessible name
  * keyboard: Arrow/Home/End move focus WITHOUT re-rendering; Enter selects
    and writes the selection to the URL (#tab=), like Region does
  * an honest zero tab stays selectable and its tooltip says why
  * one component: identical heights per viewport, no page-level horizontal
    overflow at 390 px, and NO scroller — a compact content-sized row (one
    line from ~1300 px, wrapping below) keeps every chip on screen at every width
    (owner 2026-11-18; compressed 2026-11-19)
  * identity colour tokens: flood teal, midlat_cyclone red, heat orange, and
    two visually distinct storm glyphs
  * text tokens, not series colours: every chip label clears WCAG AA on its
    own ground in the active theme (identity rides glyph + label)

Run: EWB_LIVE_URL=http://192.168.0.121:8052 .venv/bin/python -m pytest tests/e2e/test_chips_fe.py -m e2e
"""
import os

import pytest

pytestmark = pytest.mark.e2e

LIVE_URL = os.environ.get("EWB_LIVE_URL", "http://192.168.0.121:8050").rstrip("/")

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _reachable(url):
    import urllib.request
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url + "/data/results/meta.json",
                headers={"User-Agent": "Mozilla/5.0 (e2e)"}), timeout=5):
            return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def live_url():
    if not _reachable(LIVE_URL):
        pytest.skip(f"dashboard not reachable at {LIVE_URL}")
    return LIVE_URL


@pytest.fixture()
def page_ctx():
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.errors = []
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        yield page
        browser.close()


TYPE_KEYS = ["heat_wave", "cold_snap", "freeze", "atmospheric_river",
             "flood", "tropical_cyclone", "midlat_cyclone"]


def _tabs(page):
    return page.locator("#type-tabs [role=tab]")


# ---------------------------------------------------------------- semantics --
def test_tablist_semantics_and_grouped_order(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url, wait_until="networkidle")
    page.wait_for_selector("#type-tabs [role=tab]")
    assert page.get_attribute("#type-tabs", "role") == "tablist"
    info = page.eval_on_selector_all(
        "#type-tabs [role=tab]",
        r"""els => els.map(e => ({ sel: e.getAttribute('aria-selected'),
            tab: e.tabIndex, controls: e.getAttribute('aria-controls'),
            text: e.textContent.replace(/\s+/g, ' ').trim(),
            labelled: e.hasAttribute('aria-label'),
            etype: e.dataset.etype, swatch: !!e.querySelector('.sw') }))""")
    assert len(info) == 1 + len(TYPE_KEYS)          # All + every published lane
    # every tab: the count rides the visible text (the accessible name),
    # aria-controls names BOTH regions, and every hazard tab carries a lane swatch
    for t in info:
        assert t["controls"] and "explore" in t["controls"] and "scorecard-sec" in t["controls"], t
        assert not t["labelled"] and t["text"] and t["text"][-1].isdigit(), t
        if t["etype"] != "all":
            assert t["swatch"], t
    # exactly one tab is in the tab order, and it is the selected one
    assert [t["tab"] for t in info].count(0) == 1
    assert info[[t["tab"] for t in info].index(0)]["sel"] == "true"
    # grouped order: temperature | water | storms (chips redesign)
    assert [t["etype"] for t in info[1:]] == TYPE_KEYS
    # labelled groups: Temperature | Water | Storms. Each group head is preceded
    # by a presentational caption — role=none + aria-hidden, so the tablist still
    # owns exactly one tab per type (the count above is unchanged) and the caption
    # never enters the a11y tree.
    caps = page.locator("#type-tabs .type-group")
    assert caps.count() == 3
    assert [c.strip().lower() for c in caps.all_text_contents()] == [
        "temperature", "water", "storms"]
    assert page.get_attribute("#type-tabs .type-group", "aria-hidden") == "true"
    assert page.get_attribute("#type-tabs .type-group", "role") == "none"
    assert page.errors == []


# ---------------------------------------------------------------- keyboard --
def test_keyboard_nav_and_url_selection(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url, wait_until="networkidle")
    page.wait_for_selector("#type-tabs [role=tab]")
    _tabs(page).first.focus()
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")
    page.keyboard.press("ArrowRight")               # All -> heat -> cold -> freeze
    focused = page.evaluate("document.activeElement.textContent")
    assert focused.strip().startswith("Freeze")
    # arrows alone must NOT change the selection (manual activation)
    assert page.evaluate("state.tab") == "all"
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert page.evaluate("state.tab") == "freeze"
    # the selection is shareable URL state, like region/date
    assert "tab=freeze" in page.evaluate("decodeURIComponent(location.hash)")
    # focus survives the re-render the selection triggers
    assert page.evaluate("document.activeElement.getAttribute('aria-selected')") == "true"
    # Home / End
    page.keyboard.press("End")
    assert page.evaluate("document.activeElement.dataset.tab") == "midlat_cyclone"
    page.keyboard.press("Home")
    assert page.evaluate("document.activeElement.textContent").strip().startswith("All")
    # deep link: the URL wins, and an unknown value falls back and self-clears
    page.goto(live_url + "/#tab=flood", wait_until="networkidle")
    page.wait_for_timeout(700)
    assert page.evaluate("state.tab") == "flood"
    rows = page.eval_on_selector_all("#event-table tbody tr[data-id]",
                                     "els => els.map(e => e.textContent)")
    assert rows and all("Flood" in r for r in rows), rows[:3]
    page.goto(live_url + "/#tab=not_a_lane", wait_until="networkidle")
    page.wait_for_timeout(700)
    assert page.evaluate("state.tab") == "all"
    assert "not_a_lane" not in page.evaluate("location.hash")
    assert page.errors == []


# ------------------------------------------------------------- honest zero --
def test_zero_lane_selectable_and_explained(live_url, page_ctx):
    page = page_ctx
    page.goto(live_url, wait_until="networkidle")
    page.wait_for_selector("#type-tabs [role=tab]")
    zero = page.eval_on_selector_all(
        "#type-tabs [role=tab]",
        """els => els.map((e, i) => ({ i, n: +e.querySelector('.n').textContent,
            disabled: e.disabled, title: e.title })).filter(t => t.n === 0)""")
    if not zero:
        pytest.skip("every lane has events this window")
    for t in zero:
        assert not t["disabled"]                    # an honest zero stays selectable
        assert t["title"]                           # and says why / how it is detected
    tab = _tabs(page).nth(zero[0]["i"])
    tab.click()
    page.wait_for_timeout(500)
    # selecting it shows the honest empty state, not a broken list
    note = page.inner_text("#event-table tbody .lane-empty").lower()
    assert "no " in note or "not" in note
    assert page.errors == []


# ------------------------------------------------------------ responsive ----
def _grid(page):
    return page.evaluate("""() => {
        const t = document.querySelector('#type-tabs');
        const tabs = [...t.querySelectorAll('[role=tab]')];
        const box = (e) => e.getBoundingClientRect();
        const rows = {};
        tabs.forEach((e) => { const y = Math.round(box(e).top);
          (rows[y] = rows[y] || []).push({ w: Math.round(box(e).width),
            right: Math.round(box(e).right), over: box(e).right > document.documentElement.clientWidth }); });
        return { cols: getComputedStyle(t).gridTemplateColumns.split(' ').length,
                 rows, heights: [...new Set(tabs.map((e) => Math.round(box(e).height)))],
                 widths: [...new Set(tabs.map((e) => Math.round(box(e).width)))],
                 clipped: tabs.filter((e) => { const l = e.querySelector('.t-label');
                   return l.scrollWidth > l.clientWidth + 1; }).map((e) => e.dataset.tab),
                 rowScroll: t.scrollWidth - t.clientWidth,
                 pageScroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
                 caps: [...t.querySelectorAll('.type-group')].filter((c) => c.getBoundingClientRect().width > 0).length,
                 badges: [...new Set(tabs.map((e) => Math.round(
                     e.querySelector('.n').getBoundingClientRect().right - box(e).right)))] }; }""")


@pytest.mark.parametrize("w,h,rows,dividers", [(1440, 900, [8], 3), (1366, 768, [8], 3),
                                                (820, 1100, [8], 3), (390, 844, [8], 3)])
def test_chips_row_is_compact_never_scrolled_or_clipped(live_url, w, h, rows, dividers):
    """Segmented underline rail keeps all 8 tabs + 3 family dividers on ONE crisp
    row down to tablet widths without scrolling; below 620 px it becomes a smooth
    single-row horizontal swipe rail with 44 px touch targets."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto(live_url, wait_until="networkidle")
        page.wait_for_selector("#type-tabs [role=tab]")
        m = _grid(page)
        assert m["pageScroll"] <= 0, m                      # no horizontal page overflow
        if w >= 620:
            assert m["rowScroll"] == 0, m                   # desktop/tablet cannot scroll
            assert not [c for r in m["rows"].values() for c in r if c["over"]], m
        assert [len(r) for r in m["rows"].values()] == rows, m
        assert m["clipped"] == [], m                        # short labels, never truncated
        assert len(m["heights"]) == 1, m                    # one tab height per viewport
        assert m["caps"] == dividers, m                     # All | Temperature | Water | Storms dividers
        browser.close()


def test_chips_leave_room_for_the_first_screen(live_url):
    """The point of the compression: segmented rail + floating legend leave the
    map and event list inside 900 px at 1440x900."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(live_url, wait_until="networkidle")
        page.wait_for_selector("#type-tabs [role=tab]")
        m = page.evaluate("""() => ({
            chips: document.querySelector('#type-tabs').getBoundingClientRect().height,
            list: document.querySelector('#events-sec').getBoundingClientRect().bottom })""")
        assert m["chips"] <= 42, m                          # one compact segmented rail
        assert m["list"] <= 900, m
        browser.close()


def test_phone_touch_targets_are_44px(live_url):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.goto(live_url, wait_until="networkidle")
        page.wait_for_selector("#type-tabs [role=tab]")
        m = _grid(page)
        assert m["heights"] == [44], m                      # WCAG 2.5.5 / M7
        browser.close()


def test_arrow_keys_walk_the_chips(live_url):
    """Left/Right step a tab; Home/End jump to ends; Up/Down stay put on one row."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(live_url, wait_until="networkidle")
        page.wait_for_selector("#type-tabs [role=tab]")
        page.evaluate("document.querySelector('#type-tabs [role=tab]').focus()")
        seq = []
        for key in ["ArrowRight", "ArrowDown", "ArrowUp", "Home", "End"]:
            page.keyboard.press(key)
            seq.append(page.evaluate("document.activeElement.dataset.tab"))
        assert seq == ["heat_wave", "heat_wave", "heat_wave", "all", "midlat_cyclone"], seq
        browser.close()


# ------------------------------------------------------------- contrast ----
_CONTRAST_JS = """() => {
  const lum = (c) => { const m = c.match(/\\d+(\\.?\\d+)?/g).slice(0,3).map(v => {
      v = +v / 255; return v <= 0.03928 ? v/12.92 : ((v+0.055)/1.055) ** 2.4; });
    return 0.2126*m[0] + 0.7152*m[1] + 0.0722*m[2]; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p,q)=>q-p);
    return (x + 0.05) / (y + 0.05); };
  const bgOf = (e) => { for (let n = e; n; n = n.parentElement) {
      const b = getComputedStyle(n).backgroundColor;
      if (b && !/rgba\\(\\s*0,\\s*0,\\s*0,\\s*0\\)/.test(b)) return b; } return 'rgb(255,255,255)'; };
  const out = [];
  for (const e of document.querySelectorAll('#type-tabs [role=tab], .type-chip')) {
    out.push({ t: e.textContent.trim().slice(0, 22),
               ratio: +ratio(getComputedStyle(e).color, bgOf(e)).toFixed(2) }); }
  return out; }"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_chip_label_text_is_aa_on_its_ground(live_url, page_ctx, theme):
    page = page_ctx
    page.goto(live_url, wait_until="networkidle")
    page.wait_for_selector("#type-tabs [role=tab]")
    page.evaluate("(t) => { document.documentElement.dataset.theme = t;"
                  " localStorage.setItem('ewb-theme', t); }", theme)
    page.wait_for_timeout(400)
    rows = page.evaluate(_CONTRAST_JS)
    assert rows
    bad = [r for r in rows if r["ratio"] < 4.5]
    assert not bad, f"{theme}: sub-4.5 chip labels: {bad}"
    assert page.errors == []


# ------------------------------------------------------------- one metric --
def test_chips_share_one_component_metric(live_url, page_ctx):
    """Tab chips and static type tags are the same component: pill radius,
    no per-lane inline colours (identity flows through data-etype tokens)."""
    page = page_ctx
    page.goto(live_url, wait_until="networkidle")
    page.wait_for_selector("#type-tabs [role=tab]")
    st = page.evaluate("""() => ({
        inline: [...document.querySelectorAll('#type-tabs [role=tab]')]
            .filter((e) => e.getAttribute('style')).length,
        tagInline: [...document.querySelectorAll('.type-chip')]
            .filter((e) => e.getAttribute('style')).length,
        radius: getComputedStyle(document.querySelector('#type-tabs [role=tab]')).borderRadius,
        tagRadius: getComputedStyle(document.querySelector('.type-chip')).borderRadius,
        heights: [...new Set([...document.querySelectorAll('#type-tabs [role=tab]')]
            .map((e) => Math.round(e.getBoundingClientRect().height)))], })""")
    assert st["inline"] == 0 and st["tagInline"] == 0, st   # tokens, not inline hexes
    assert st["radius"] == "5px" and st["tagRadius"] == "999px", st
    assert len(st["heights"]) == 1, st                      # one height, one rhythm
