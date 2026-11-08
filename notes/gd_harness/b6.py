import sys,io; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
from PIL import Image
ws=[int(x) for x in sys.argv[1].split(',')]; theme=sys.argv[2]; out=sys.argv[3]; hh=int(sys.argv[4])
ims=[]
with sync_playwright() as p:
    for w in ws:
        b,pg=open_page(p,w,theme,wait=4500)
        im=Image.open(io.BytesIO(pg.screenshot(full_page=True))).convert('RGB'); b.close()
        sc=hh/im.height; ims.append(im.resize((int(im.width*sc),hh)))
sheet=Image.new('RGB',(sum(i.width for i in ims)+10*(len(ims)-1),hh),(120,120,120)); x=0
for i in ims: sheet.paste(i,(x,0)); x+=i.width+10
sheet.save(f"{OUT}/{out}.png"); print(sheet.size,[ (w) for w in ws])
