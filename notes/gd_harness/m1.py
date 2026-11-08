import sys,io; sys.path.insert(0,'/tmp/gd_scratch')
from gd import *
from PIL import Image, ImageDraw
ids=sys.argv[1].split(','); theme=sys.argv[2]; out=sys.argv[3]; crop_h=int(sys.argv[4]) if len(sys.argv)>4 else 700
tiles=[]
with sync_playwright() as p:
    for i in ids:
        b,pg=open_page(p,1440,theme,BASE+f"/#event={i}&lead=24h",wait=7000)
        el=pg.query_selector('#detail-sec'); png=el.screenshot()
        im=Image.open(io.BytesIO(png)).convert('RGB'); im=im.crop((0,0,im.width,min(im.height,crop_h)))
        im=im.resize((im.width*55//100, im.height*55//100)); d=ImageDraw.Draw(im); d.rectangle((0,0,150,14),fill=(0,0,0)); d.text((3,2),i,fill=(255,255,255))
        tiles.append(im); b.close()
W=tiles[0].width; H=max(t.height for t in tiles); cols=2; rows=(len(tiles)+1)//2
sheet=Image.new('RGB',(cols*W+10*(cols-1),rows*H+10*(rows-1)),(128,128,128))
for k,t in enumerate(tiles): sheet.paste(t,((k%cols)*(W+10),(k//cols)*(H+10)))
sheet.save(f"{OUT}/{out}.png"); print(sheet.size)
