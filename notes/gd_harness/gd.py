"""guardian-design audit helpers. usage: from gd import *"""
import json, sys, time
from playwright.sync_api import sync_playwright
BASE = "https://ewb.espr.ai"
OUT = "/tmp/gd_scratch/shots"
WIDTHS = [1920,1440,1280,1024,820,390,360]
def H(w): return {1920:1080,1440:900,1280:800,1024:768,820:1180,390:844,360:740}[w]
import os, mimetypes
DASH="/home/orca/orca/workspaces/ewb-live/dogfish/dashboard"
LOCAL = os.environ.get("GD_LOCAL","0")=="1"
def _route(route):
    from urllib.parse import urlparse
    path = urlparse(route.request.url).path
    name = "index.html" if path in ("/","") else path.lstrip("/")
    f = os.path.join(DASH, name)
    if os.path.isfile(f) and not name.startswith("data/"):
        ct = mimetypes.guess_type(f)[0] or "text/plain"
        return route.fulfill(status=200, body=open(f,"rb").read(), headers={"content-type":ct,"cache-control":"no-store"})
    return route.continue_()
def open_page(p, w, theme, url=BASE+"/", wait=3500):
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":w,"height":H(w)}, device_scale_factor=1,
                        color_scheme=theme, has_touch=w<=480, is_mobile=w<=480)
    pg = ctx.new_page()
    pg.errors=[]
    pg.on("pageerror", lambda e: pg.errors.append("pageerror:"+str(e)))
    pg.on("console", lambda m: pg.errors.append("console:"+m.text) if m.type in("error",) else None)
    pg.on("requestfailed", lambda r: pg.errors.append("reqfail:"+r.url[:90]))
    pg.add_init_script("try{localStorage.setItem('ewb-live-state', JSON.stringify({themePref:'%s'}))}catch(e){}"%theme)
    if LOCAL: pg.route('**/*', _route)
    pg.goto(url, wait_until="load")
    pg.wait_for_timeout(wait)
    return b, pg
