import asyncio, json
from playwright.async_api import async_playwright
BASE = "http://127.0.0.1:8075/"

AUDIT = """() => {
  const out = {orangeText: [], over600: [], blueLeftover: [], weights: new Set()};
  const orange = new Set(['rgb(255, 96, 0)','#ff6000']);
  const els = document.querySelectorAll('body *');
  for (const el of els) {
    if (!el.offsetParent && el.tagName !== 'BODY') continue;
    const cs = getComputedStyle(el);
    if (el.children.length === 0 && el.textContent.trim()) {
      if (orange.has(cs.color)) out.orangeText.push(el.tagName+'.'+el.className+': '+el.textContent.slice(0,40));
      if (/29, 111, 237|91, 142, 247|47, 111, 237/.test(cs.color)) out.blueLeftover.push(el.tagName+': '+cs.color);
      const w = parseInt(cs.fontWeight);
      if (w > 600) out.over600.push(el.tagName+'.'+el.className+': '+w);
      if (w) out.weights.add(cs.fontWeight);
    }
  }
  return {orangeText: out.orangeText, over600: out.over600, blueLeftover: out.blueLeftover, weights: [...out.weights]};
}"""

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        for theme in ("light", "dark"):
            pg = await b.new_page(viewport={"width": 1440, "height": 900})
            await pg.goto(BASE, wait_until="networkidle")
            await pg.evaluate(f"document.documentElement.dataset.theme='{theme}'")
            await pg.wait_for_timeout(1200)
            r = await pg.evaluate(AUDIT)
            print(theme, json.dumps(r, indent=1))
            # hover + selected state probe on the events table
            sel = await pg.evaluate("""() => {
              const tr = document.querySelector('#event-table tbody tr');
              return {has: !!tr}; }""")
            # selected row treatment via app class
            await pg.evaluate("""() => { const tr=document.querySelector('#event-table tbody tr');
              if (tr) tr.classList.add('sel'); }""")
            st = await pg.evaluate("""() => { const td=document.querySelector('#event-table tbody tr.sel td');
              const cs=td&&getComputedStyle(td); return cs?{bg:cs.backgroundColor,color:cs.color,weight:cs.fontWeight}:null; }""")
            print(theme, "selected-row:", json.dumps(st))
            # button focus-visible outline token
            fv = await pg.evaluate("""() => { const btn=document.querySelector('button');
              return btn? getComputedStyle(document.documentElement).getPropertyValue('--accent-focus').trim() : null; }""")
            print(theme, "accent-focus:", fv)
            # audit the detail view too (verdict chips, tooltips colors)
            await pg.goto(BASE + "?event=31b6bf4a1a44", wait_until="networkidle")
            await pg.evaluate(f"document.documentElement.dataset.theme='{theme}'")
            await pg.wait_for_timeout(1500)
            r2 = await pg.evaluate(AUDIT)
            print(theme, "DETAIL", json.dumps(r2, indent=1))
            await pg.close()
        await b.close()
asyncio.run(main())
