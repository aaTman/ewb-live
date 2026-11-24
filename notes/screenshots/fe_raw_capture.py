import os
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.expanduser("~/.cache/ms-playwright"))
from playwright.sync_api import sync_playwright

BASE = "http://192.168.0.121:8050"
OUT = "notes/screenshots"
os.makedirs(OUT, exist_ok=True)
with sync_playwright() as pw:
    b = pw.chromium.launch()
    # desktop, latest frame, all layers
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    errs = []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE + "/#raw=2026110118", wait_until="networkidle")
    pg.wait_for_function(
        "document.querySelector('#raw-readout').textContent.includes('01 Nov 2026')",
        timeout=20000)
    pg.wait_for_timeout(1500)  # let frame decode + paint
    el = pg.query_selector("#raw-sec")
    el.scroll_into_view_if_needed()
    pg.wait_for_timeout(400)
    el.screenshot(path=OUT + "/fe-raw-desktop.png")
    print("readout:", pg.text_content("#raw-readout").strip())
    print("badge visible:", pg.is_visible("#raw-badge"))
    # heat-only layer via deep link
    pg.goto(BASE + "/#raw=2026102012&layers=h", wait_until="networkidle")
    pg.wait_for_timeout(1500)
    pg.query_selector("#raw-sec").screenshot(path=OUT + "/fe-raw-heat-only.png")
    print("heat-only hash:", pg.evaluate("decodeURIComponent(location.hash)"))
    # pixel sanity: warm pixels present on heat-only frame
    px = pg.evaluate("""(() => {
      const cv = document.querySelector('#raw-canvas');
      const d = cv.getContext('2d').getImageData(0,0,cv.width,cv.height).data;
      let warm = 0;
      for (let i = 0; i < d.length; i += 4)
        if (d[i] > 150 && d[i] - d[i+2] > 40) warm++;
      return warm;
    })()""")
    print("warm heat pixels on heat-only frame:", px)
    # mobile
    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(BASE + "/#raw=2026110118", wait_until="networkidle")
    m.wait_for_timeout(2000)
    m.query_selector("#raw-sec").scroll_into_view_if_needed()
    m.wait_for_timeout(400)
    m.query_selector("#raw-sec").screenshot(path=OUT + "/fe-raw-mobile.png")
    print("errors:", [e for e in errs if "Failed to load resource" not in e][:4] or "none")
    b.close()
