import sys,json; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
AX=open('/tmp/gd_scratch/axe.min.js').read()
def run(pg,label):
    pg.evaluate(AX)
    r=pg.evaluate("""async()=>{const r=await axe.run(document,{resultTypes:['violations']});
      return r.violations.map(v=>({id:v.id,impact:v.impact,n:v.nodes.length,ex:v.nodes.slice(0,3).map(n=>n.target.join(' ')+' :: '+(n.any[0]&&n.any[0].message||'').slice(0,110))}))}""")
    print('##',label,[ (v['id'],v['impact'],v['n']) for v in r])
    for v in r:
        for e in v['ex']: print('   ',v['id'],e)
with sync_playwright() as p:
    for theme in ('light','dark'):
        for w,hash_ in ((1440,''),(390,''),(1440,'#event=95aeafb7fd7f&lead=24h'),(1440,'#event=255f68102c43&lead=24h')):
            b,pg=open_page(p,w,theme,BASE+'/'+hash_,wait=6500)
            run(pg,f'{theme} {w} {hash_ or "home"}')
            if not hash_ and w==1440:
                pg.click('#about-btn'); pg.wait_for_timeout(500); run(pg,f'{theme} {w} about')
            b.close()
