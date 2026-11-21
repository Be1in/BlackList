import zipfile
from lxml import etree
P="http://schemas.openxmlformats.org/presentationml/2006/main"
A="http://schemas.openxmlformats.org/drawingml/2006/main"
ns={"p":P,"a":A}
z=zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
root=etree.fromstring(z.read("ppt/slides/slide6.xml"))
grp=root.find(".//p:grpSp",ns)
def xfrm(el):
    return el.find("./p:spPr/a:xfrm",ns) if el.tag.endswith("sp") else el.find("./p:grpSpPr/a:xfrm",ns)
x=xfrm(grp)
off=x.find("a:off",ns); ext=x.find("a:ext",ns); choff=x.find("a:chOff",ns); chext=x.find("a:chExt",ns)
ox,oy=int(off.get("x")),int(off.get("y")); ex,ey=int(ext.get("cx")),int(ext.get("cy"))
cx0,cy0=int(choff.get("x")),int(choff.get("y")); cw,ch=int(chext.get("cx")),int(chext.get("cy"))
print("group",ox/914400,oy/914400,ex/914400,ey/914400,"ch",cx0/914400,cy0/914400,cw/914400,ch/914400)
sx=ex/cw; sy=ey/ch
minx=miny=1e18; maxx=maxy=-1e18
for sh in grp.iter():
    if not (sh.tag.endswith("}sp") or sh.tag.endswith("}grpSp")) or sh is grp: continue
    t=xfrm(sh)
    if t is None: continue
    o=t.find("a:off",ns); e=t.find("a:ext",ns)
    if o is None or e is None: continue
    X=int(o.get("x")); Y=int(o.get("y")); W=int(e.get("cx")); H=int(e.get("cy"))
    # only direct children are in group child coords; nested groups have own coords
    if sh.getparent() is grp:
        ax=ox+(X-cx0)*sx; ay=oy+(Y-cy0)*sy
        minx=min(minx,ax); miny=min(miny,ay); maxx=max(maxx,ax+W*sx); maxy=max(maxy,ay+H*sy)
print("child union",minx/914400,miny/914400,maxx/914400,maxy/914400)
print("overflow right", (maxx-ox-ex)/914400, "bottom", (maxy-oy-ey)/914400)
