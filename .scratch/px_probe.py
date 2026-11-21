from PIL import Image
im = Image.open(r"d:\BlackList\.scratch\render1\幻灯片2.PNG").convert("RGB")
w,h = im.size
print(w,h)
# scan region x from 600..w-1, find non-white pixels
minx=w; maxx=0; miny=h; maxy=0
for y in range(h):
    for x in range(600,w):
        r,g,b = im.getpixel((x,y))
        if not (r>245 and g>245 and b>245):
            if x<minx: minx=x
            if x>maxx: maxx=x
            if y<miny: miny=y
            if y>maxy: maxy=y
print("art bbox px", minx,miny,maxx,maxy, "in inches", minx/w*10, miny/h*5.625, maxx/w*10, maxy/h*5.625)
