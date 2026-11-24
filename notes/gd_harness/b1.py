import sys,json,re,datetime as dt; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
import urllib.request
ev=json.loads(urllib.request.urlopen("http://192.168.0.121:8050/data/results/events_selected.json").read())
MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
def fm(d): return f"{MON[d.month-1]} {d.day}"
bad=0
with sync_playwright() as p:
    b,pg=open_page(p,1440,'light',wait=3500)
    rows=pg.evaluate("[...document.querySelectorAll('#event-table tbody tr')].map(r=>[...r.children].map(c=>c.innerText.trim()))")
    print('catalog rows',len(rows)); print(rows[0])
    for e in ev:
        s=dt.datetime.fromisoformat(e['start']); en=dt.datetime.fromisoformat(e['end'])-dt.timedelta(days=1)
        ndays=(en-s).days+1
        pg.evaluate(f"location.hash='#event={e['id']}&lead=24h'"); pg.wait_for_timeout(2500)
        title=pg.inner_text('#detail-title'); story=pg.inner_text('#detail-story')
        exp_dates=f"{fm(s)} – {fm(en)}"
        pk=e['peak_value']; exp_pk=(f"{pk:.1f} °C" if e['event_type']!='atmospheric_river' else f"{pk:.0f} kg m⁻¹ s⁻¹")
        issues=[]
        if exp_dates not in story: issues.append(f"dates '{exp_dates}' not in story")
        if exp_pk not in story and f"{abs(pk):.1f}".replace('-','') not in story.replace('−','-').replace('-',''): issues.append(f"peak '{exp_pk}' not in story")
        if e['event_type']=='atmospheric_river' and 'IVT' not in story: issues.append('no IVT label')
        # catalog row
        row=[r for r in rows if r[1].startswith(title[:30]) and str(ndays) in r]
        if title!=e['title']: issues.append(f"title '{title}' != '{e['title']}'")
        print(e['id'],e['event_type'][:4],'|',story[:110].replace('\n',' '),'|',('ISSUES '+'; '.join(issues)) if issues else 'ok')
        bad+=bool(issues)
        pg.evaluate("document.querySelector('#detail-close').click()"); pg.wait_for_timeout(300)
    print('events with issues:',bad)
    b.close()
