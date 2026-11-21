import zipfile
from lxml import etree
P="http://schemas.openxmlformats.org/presentationml/2006/main"
A="http://schemas.openxmlformats.org/drawingml/2006/main"
ns={"p":P,"a":A}
z=zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
root=etree.fromstring(z.read("ppt/slides/slide6.xml"))
grp=root.find(".//p:grpSp",ns)
def getxfrm(el):
    return el.find("./p:grpSpPr/a:xfrm",ns) if el.tag.endswith("grpSp") else el.find("./p:spPr/a:xfrm",ns)
def bounds(el, tx, ty, sx, sy):
    # tx,ty: slide offset for el's local origin; sx,sy: scale from el coords to slide
    out=[]
    if el.tag.endswith("grpSp"):
        x=getxfrm(el)
        if x is None: return out
        off=x.find("a:off",ns); ext=x.find("a:ext",ns); co=x.find("a:chOff",ns); ce=x.find("a:chExt",ns)
        ox,oy=int(off.get("x")),int(off.get("y")); ex,ey=int(ext.get("cx")),int(ext.get("cy"))
        cx0,cy0=int(co.get("x")),int(co.get("y")); cw,ch=int(ce.get("cx")),int(ce.get("cy"))
        nsx=ex/cw; nsy=ey/ch
        # el coords -> parent coords
        ntx=tx+(ox-cx0)*sx; nty=ty+(oy-cy0)*sy
        nsx2=sx*nsx; nsy2=sy*nsy
        for child in el:
            if child.tag.endswith("}sp") or child.tag.endswith("}grpSp"):
                out+=bounds(child,ntx,nty,nsx2,nsy2)
    else:
        x=getxfrm(el)
        if x is None: return out
        o=x.find("a:off",ns); e=x.find("a:ext",ns)
        if o is None or e is None: return out
        X=int(o.get("x")); Y=int(o.get("y")); W=int(e.get("cx")); H=int(e.get("cy"))
        out.append((tx+X*sx, ty+Y*sy, tx+(X+W)*sx, ty+(Y+H)*sy))
    return out
x=getxfrm(grp); off=x.find("a:off",ns); ext=x.find("a:ext",ns)
ox,oy=int(off.get("x")),int(off.get("y")); ex,ey=int(ext.get("cx")),int(ext.get("cy"))
b=bounds(grp,ox,oy,1,1)
minx=min(i[0] for i in b); miny=min(i[1] for i in b); maxx=max(i[2] for i in b); maxy=max(i[3] for i in b)
print("true bounds in", minx/914400, miny/914400, maxx/914400, maxy/914400)
print("bbox", ox/914400,oy/914400,(ox+ex)/914400,(oy+ey)/914400)
print("overhang right",(maxx-(ox+ex))/914400,"bottom",(maxy-(oy+ey))/914400)
