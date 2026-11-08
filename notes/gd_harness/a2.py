import sys; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
ids = sys.argv[1].split(','); w=int(sys.argv[2]); theme=sys.argv[3]; tag=sys.argv[4]
with sync_playwright() as p:
    for i in ids:
        b,pg = open_page(p,w,theme,BASE+f"/#event={i}&lead=24h",wait=6000)
        el = pg.query_selector('#detail-sec')
        txt = pg.evaluate("()=>document.querySelector('#detail-sec').innerText.slice(0,900)")
        print('==',i, pg.errors[:2]); print(txt)
        el.screenshot(path=f"{OUT}/{tag}-{i}-{theme}-{w}.png")
        b.close()
