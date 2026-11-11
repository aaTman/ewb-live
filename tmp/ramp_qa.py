import json
from playwright.sync_api import sync_playwright
URL = "https://ewb.espr.ai/#map=2026100312&layers=a"
RAMPS = {"light": ["#b8e0b8","#8fbc8f","#659b65","#3c7a3c","#2e7d32"],
         "dark":  ["#3a5a3a","#4a7a4a","#66bb6a","#8fcf8f","#b8e0b8"]}
with sync_playwright() as p:
    b = p.chromium.launch()
    for theme in ("light","dark"):
        pg = b.new_page(viewport={"width":1440,"height":900}, color_scheme=theme)
        pg.goto("https://ewb.espr.ai/", wait_until="load")
        pg.evaluate(f"""() => {{ const s = JSON.parse(localStorage.getItem('ewb-live-state')||'{{}}');
          s.theme = {json.dumps(theme)}; localStorage.setItem('ewb-live-state', JSON.stringify(s)); }}""")
        pg.goto(URL, wait_until="load")
        pg.wait_for_selector("#raw-canvas"); pg.wait_for_timeout(2500)
        tok = pg.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--dur-ar-1').trim()")
        res = pg.evaluate("""(ramp) => {
          const c = document.querySelector('#raw-canvas');
          const d = c.getContext('2d').getImageData(0,0,c.width,c.height).data;
          const want = ramp.map(h => [parseInt(h.slice(1,3),16),parseInt(h.slice(3,5),16),parseInt(h.slice(5,7),16)]);
          const cnt = want.map(()=>0);
          for (let i=0;i<d.length;i+=4){ if(!d[i+3]) continue;
            for (let k=0;k<5;k++) if(d[i]===want[k][0]&&d[i+1]===want[k][1]&&d[i+2]===want[k][2]){cnt[k]++;break;} }
          return cnt; }""", RAMPS[theme])
        leg = ""
        try:
            btn = pg.query_selector("text=/AR IVT/")
            if btn: btn.click(); pg.wait_for_timeout(300)
            el = pg.query_selector("#gm-legend")
            leg = el.inner_text().replace("\n"," / ") if el else ""
        except Exception as e: leg = f"(legend err {e})"
        print(f"{theme}: --dur-ar-1={tok} class-px c1..c5={res} legend={leg!r}")
        pg.screenshot(path=f"notes/screenshots/ramp-v7-{theme}-1440.png")
        pg.close()
    pg = b.new_page(viewport={"width":390,"height":844})
    pg.goto(URL, wait_until="load"); pg.wait_for_selector("#raw-canvas"); pg.wait_for_timeout(2500)
    pg.screenshot(path="notes/screenshots/ramp-v7-mobile-390.png"); pg.close()
    b.close()
