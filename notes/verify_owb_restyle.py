import asyncio, json, sys
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8075/"
SHOTS = "notes/screenshots"

async def probe(page, label):
    await page.wait_for_timeout(1800)  # let data + plotly settle
    r = await page.evaluate("""() => {
      const cs = getComputedStyle(document.body);
      return {
        fontInter: document.fonts.check('14px Inter'),
        fontMono: document.fonts.check('12px "IBM Plex Mono"'),
        bodyFont: cs.fontFamily.split(',')[0],
        bodyBg: cs.backgroundColor,
        bodyColor: cs.color,
        bodyWeight: cs.fontWeight,
        bodyLineH: cs.lineHeight,
        h1Font: document.querySelector('h1') ? getComputedStyle(document.querySelector('h1')).fontSize + ' ' + getComputedStyle(document.querySelector('h1')).fontWeight + ' ' + getComputedStyle(document.querySelector('h1')).letterSpacing : null,
        theme: document.documentElement.dataset.theme,
        rows: document.querySelectorAll('tbody tr').length,
        chips: document.querySelectorAll('.chip, .pill').length,
      };
    }""")
    print(label, json.dumps(r))
    return r

async def main():
    errors = []
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        # ---- desktop 1440x900, light ----
        pg = await b.new_page(viewport={"width":1440,"height":900})
        pg.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
        pg.on("pageerror", lambda e: errors.append(str(e)))
        await pg.goto(BASE, wait_until="networkidle")
        await pg.evaluate("document.documentElement.dataset.theme='light'")
        L = await probe(pg, "LIGHT-desktop-list")
        geo = await pg.evaluate("""() => ({header: document.querySelector('header.top').getBoundingClientRect().height,
          navTop: getComputedStyle(document.querySelector('nav.controls')).top})""")
        print("desktop geo", json.dumps(geo))
        await pg.screenshot(path=f"{SHOTS}/owb-restyle-light.png", full_page=False)
        # event detail
        await pg.goto(BASE + "?event=31b6bf4a1a44", wait_until="networkidle")
        await pg.evaluate("document.documentElement.dataset.theme='light'")
        await pg.wait_for_timeout(1500)
        r = await pg.evaluate("""() => ({
          chips: document.querySelectorAll('.detail-chips .chip').length,
          miniRows: document.querySelectorAll('#detail-mini tbody tr').length,
          story: !!document.querySelector('.event-story'),
          chipRadius: document.querySelector('.chip') ? getComputedStyle(document.querySelector('.chip')).borderRadius : null,
          chipFont: document.querySelector('.chip .k') ? getComputedStyle(document.querySelector('.chip .k')).fontSize + ' / ' + getComputedStyle(document.querySelector('.chip .k')).letterSpacing : null,
        })""")
        print("LIGHT-detail", json.dumps(r))
        await pg.screenshot(path=f"{SHOTS}/owb-restyle-detail-light.png")
        # ---- desktop dark via real toggle ----
        await pg.goto(BASE, wait_until="networkidle")
        # cycle auto->light->dark via the app's own button (two clicks from auto)
        for _ in range(3):
            await pg.click("#theme-toggle")
            await pg.wait_for_timeout(300)
            t = await pg.evaluate("document.documentElement.dataset.theme")
            if t == "dark": break
        D = await probe(pg, "DARK-desktop-list")
        await pg.screenshot(path=f"{SHOTS}/owb-restyle-dark.png")
        # dark computed token spot values
        tok = await pg.evaluate("""() => { const cs=getComputedStyle(document.documentElement);
          return ['--bg','--text','--text-dim','--border','--accent','--better','--worse','--series-heat','--series-freeze','--series-ar']
            .reduce((o,k)=>(o[k]=cs.getPropertyValue(k).trim(),o),{}); }""")
        print("DARK tokens", json.dumps(tok))
        # dark detail
        await pg.goto(BASE + "?event=31b6bf4a1a44", wait_until="networkidle")
        await pg.wait_for_timeout(1500)
        await pg.screenshot(path=f"{SHOTS}/owb-restyle-detail-dark.png")
        # ---- mobile 400x850 light ----
        mp = await b.new_page(viewport={"width":400,"height":850})
        mp.on("console", lambda m: errors.append(m.text) if m.type=="error" else None)
        await mp.goto(BASE, wait_until="networkidle")
        await mp.evaluate("document.documentElement.dataset.theme='light'")
        M = await probe(mp, "LIGHT-mobile-list")
        await mp.screenshot(path=f"{SHOTS}/owb-restyle-mobile.png", full_page=False)
        # header height for sticky check
        hh = await mp.evaluate("document.querySelector('header.top').getBoundingClientRect().height")
        print("mobile header h", hh)
        await b.close()
    # assertions
    state = {"ok": True}
    def chk(name, cond):
        print(("PASS " if cond else "FAIL ") + name); state["ok"] = state["ok"] and cond
    chk("fonts: Inter loaded", L["fontInter"] and D["fontInter"])
    chk("fonts: IBM Plex Mono loaded", L["fontMono"] and D["fontMono"])
    chk("body font = Inter", L["bodyFont"].strip('"') == "Inter")
    chk("light bg = rgb(253,253,252)", L["bodyBg"] == "rgb(253, 253, 252)")
    chk("light body ink = matte-black", L["bodyColor"] == "rgb(38, 38, 32)")
    chk("light weight 400", L["bodyWeight"] == "400")
    chk("dark bg = rgb(30,30,26)", D["bodyBg"] == "rgb(30, 30, 26)")
    chk("dark ink on dark paper >=AA", D["bodyColor"] == "rgb(236, 234, 216)")
    chk("dark theme activated via toggle", D["theme"] == "dark")
    chk("list table populated", L["rows"] > 0)
    chk("zero console errors", len(errors) == 0)
    print("console errors:", errors[:10])
    sys.exit(0 if state["ok"] else 1)

asyncio.run(main())
