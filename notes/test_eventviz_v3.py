#!/usr/bin/env python
"""Playwright verify for eventviz.js v3 (Leaflet maps). Run with repo root as cwd.

Asserts per event (deep link ?event=<id>):
  - #detail-viz contains ≥1 canvas with painted pixels
  - basemap tile requests hit cartocdn or openstreetmap
  - .leaflet-container count == panel count + 1 (main app map)
  - no console/page errors
Also: #event=&lead= deep link starts on that lead; clicking a lead rewrites
the hash (EventViz.onLead contract); zz-test mock hides non-ok panels and
shows the reason line. Screenshots → notes/screenshots/.
"""
import asyncio, sys, json, re
from playwright.async_api import async_playwright

BASE = 'http://127.0.0.1:8074/'
EVENTS = ['31b6bf4a1a44', 'd7f17e199dbf', 'a5e694627968']
SHOTS = 'notes/screenshots'
FAIL = []

def check(name, cond, detail=''):
    tag = 'PASS' if cond else 'FAIL'
    print(f'  [{tag}] {name}' + (f' — {detail}' if detail else ''))
    if not cond:
        FAIL.append(name + (': ' + detail if detail else ''))

STATS_JS = """() => {
  const host = document.querySelector('#detail-viz');
  const out = {
    panels: host.querySelectorAll('.ev3-panel').length,
    maps: host.querySelectorAll('.leaflet-container').length,
    pageMaps: document.querySelectorAll('.leaflet-container').length,
    fieldImgs: host.querySelectorAll('img.leaflet-image-layer').length,
    fieldImgOk: [...host.querySelectorAll('img.leaflet-image-layer')].filter(i => i.complete && i.naturalWidth > 0).length,
    tilesLoaded: [...host.querySelectorAll('img.leaflet-tile')].filter(i => i.complete && i.naturalWidth > 0).length,
    canvases: [...host.querySelectorAll('canvas')].map(c => {
      try { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; }
      catch (e) { return -1; }
    }),
    nodata: (host.querySelector('.ev3-nodata') || {}).textContent || '',
    leadOn: (host.querySelector('.ev-lead.on') || {}).textContent || '',
    grat: host.querySelectorAll('.leaflet-overlay-pane path').length,
    fsBtns: host.querySelectorAll('.ev3-fsbtn').length,
  };
  return out;
}"""

async def probe(page, requests, errors):
    await page.wait_for_selector('#detail-viz .ev3-panel', timeout=15000)
    await page.wait_for_function(
        "() => [...document.querySelectorAll('#detail-viz img.leaflet-image-layer')].every(i => i.complete && i.naturalWidth > 0)",
        timeout=10000)
    await page.wait_for_timeout(1200)  # let tiles stream in
    return await page.evaluate(STATS_JS)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=['--no-sandbox'])
        for ev in EVENTS:
            for theme in ['dark', 'light']:
                print(f'== {ev} {theme}')
                ctx = await browser.new_context(viewport={'width': 1440, 'height': 1000})
                page = await ctx.new_page()
                errors, requests = [], []
                page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
                page.on('pageerror', lambda e: errors.append(str(e)))
                page.on('request', lambda r: requests.append(r.url))
                await page.add_init_script(
                    "localStorage.setItem('ewb-live-state', JSON.stringify({themePref:%r}))" % theme)
                await page.goto(BASE + '?event=' + ev, wait_until='load')
                s = await probe(page, requests, errors)
                check('panels > 1', s['panels'] > 1, f"{s['panels']} panels")
                check('leaflet-container == panels + 1 (app map)',
                      s['pageMaps'] == s['panels'] + 1, f"pageMaps={s['pageMaps']} panels={s['panels']}")
                check('field overlays painted', s['fieldImgs'] > 0 and s['fieldImgOk'] == s['fieldImgs'],
                      f"{s['fieldImgOk']}/{s['fieldImgs']} imgs")
                check('≥1 canvas with painted pixels', any(n > 0 for n in s['canvases']), str(s['canvases']))
                tile_reqs = [u for u in requests if 'arcgisonline' in u or 'tile.openstreetmap.org' in u]
                check('basemap tiles requested', len(tile_reqs) > 0, f"{len(tile_reqs)} tile requests")
                check('basemap tiles loaded', s['tilesLoaded'] > 0, f"{s['tilesLoaded']} loaded")
                check('graticule/contour paths present', s['grat'] > 0, f"{s['grat']} paths")
                check('fullscreen buttons per panel', s['fsBtns'] == s['panels'])
                check('theme-matched Esri gray tiles used',
                      any(('World_' + ('Dark' if theme == 'dark' else 'Light') + '_Gray_Base') in u for u in tile_reqs))
                check('no console errors', not errors, '; '.join(errors[:3]))
                shot = f'{SHOTS}/eventviz-v3-{ev}-{theme}.png'
                await page.locator('#detail-viz').screenshot(path=shot)
                print('  shot:', shot)
                await ctx.close()

        # --- deep-link lead contract (app.js hash) ---
        print('== lead deep link + onLead hash rewrite (31b6bf4a1a44, #lead=72h)')
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = await ctx.new_page()
        errors = []
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.goto(BASE + '#event=31b6bf4a1a44&lead=72h', wait_until='load')
        s = await probe(page, [], errors)
        check('starts on lead 72h from hash', s['leadOn'] == '72h', s['leadOn'])
        await page.locator('#detail-viz .ev-lead').filter(has_text=re.compile(r'^24h$')).first.click()
        await page.wait_for_timeout(400)
        h = await page.evaluate('location.hash')
        check('onLead rewrote hash to lead=24h', 'lead=24h' in h, h)
        check('no console errors on lead switch', not errors, '; '.join(errors[:3]))
        # fullscreen open/close
        fserr = []
        await page.locator('#detail-viz .ev3-panel .ev3-fsbtn').first.click()
        await page.wait_for_timeout(500)
        on = await page.locator('#detail-viz .ev3-panel.ev3-fs').count()
        await page.keyboard.press('Escape')
        await page.wait_for_timeout(300)
        off = await page.locator('#detail-viz .ev3-panel.ev3-fs').count()
        check('fullscreen opens and Esc closes', on == 1 and off == 0)
        await ctx.close()

        # --- zz-test mock: non-ok models hidden, reason line present ---
        zz = json.load(open('data/results/events/zz-test/viz.json'))
        exp_panels = 1 + sum(1 for m, v in zz['models'].items()
                             if isinstance(v.get('24'), dict) and v['24'].get('status') == 'ok')
        print(f'== zz-test mock (status!=ok hidden, reason line; expect {exp_panels} panels)')
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = await ctx.new_page()
        errors = []
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.goto(BASE, wait_until='load')
        s = await page.evaluate("""async () => {
          const viz = await window.EventViz.load('zz-test');
          const host = document.createElement('div');
          host.id = 'zz-host'; document.body.appendChild(host);
          window.EventViz.render(host, viz, null);
          return {
            panels: host.querySelectorAll('.ev3-panel').length,
            maps: host.querySelectorAll('.leaflet-container').length,
            nodata: host.querySelector('.ev3-nodata').textContent,
            leadOn: host.querySelector('.ev-lead.on').textContent,
          };
        }""")
        await page.wait_for_timeout(800)
        check(f'{exp_panels} panels at lead 24 (obs + ok models)', s['panels'] == exp_panels, str(s['panels']))
        check('maps == panels inside test host', s['maps'] == s['panels'], str(s['maps']))
        check('reason line names skipped model', 'graphcast(skipped (no ensemble members))' in s['nodata'], s['nodata'])
        check('reason line names not-built model', 'pangu_weather(unavailable — not built)' in s['nodata'], s['nodata'])
        check('no console errors on mock render', not errors, '; '.join(errors[:3]))
        # re-render (theme flip idempotency): render again into the same host
        s2 = await page.evaluate("""async () => {
          const viz = await window.EventViz.load('zz-test');
          const host = document.querySelector('#zz-host');
          window.EventViz.render(host, viz, null);
          return { panels: host.querySelectorAll('.ev3-panel').length,
                   maps: host.querySelectorAll('.leaflet-container').length,
                   roots: host.querySelectorAll('.ev3-root').length };
        }""")
        check('re-render idempotent (single root, no map leak)',
              s2['roots'] == 1 and s2['maps'] == s2['panels'] == exp_panels, str(s2))
        check('no console errors on re-render', not errors, '; '.join(errors[:3]))
        await ctx.close()

        # --- AR fill toggle ---
        print('== AR overlap fill toggle (d7f17e199dbf)')
        ctx = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        page = await ctx.new_page()
        errors = []
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('pageerror', lambda e: errors.append(str(e)))
        await page.goto(BASE + '?event=d7f17e199dbf', wait_until='load')
        await probe(page, [], errors)
        before = await page.evaluate("document.querySelectorAll('#detail-viz .ev3-panel').length")
        await page.locator('#detail-viz .ev3-fill').click()
        await page.wait_for_timeout(1500)
        after = await page.evaluate("""() => ({
          panels: document.querySelectorAll('#detail-viz .ev3-panel').length,
          maps: document.querySelectorAll('#detail-viz .leaflet-container').length,
          fields: [...document.querySelectorAll('#detail-viz img.leaflet-image-layer')].filter(i => i.complete && i.naturalWidth > 0).length,
        })""")
        check('fill toggle rebuilds panels cleanly', after['panels'] == before and after['maps'] == after['panels'],
              str(after))
        check('2 overlays per model panel when filled', after['fields'] >= after['panels'] * 2, str(after['fields']))
        check('no console errors on fill toggle', not errors, '; '.join(errors[:3]))
        await ctx.close()
        await browser.close()

    print('\n' + ('ALL PASS' if not FAIL else 'FAILURES:\n  ' + '\n  '.join(FAIL)))
    sys.exit(0 if not FAIL else 1)

asyncio.run(main())
