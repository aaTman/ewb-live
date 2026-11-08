import sys; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
w=int(sys.argv[1]); theme=sys.argv[2]; tag=sys.argv[3]; sels=sys.argv[4].split(',')
with sync_playwright() as p:
    b,pg=open_page(p,w,theme,wait=4500)
    for s in sels:
        el=pg.query_selector(s)
        if el: el.screenshot(path=f"{OUT}/{tag}-{s.strip('#.').replace(' ','_')}-{theme}-{w}.png")
        else: print('missing',s)
    print(pg.errors[:3])
    b.close()
