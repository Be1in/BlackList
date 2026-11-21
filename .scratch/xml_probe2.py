import zipfile
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
P="http://schemas.openxmlformats.org/presentationml/2006/main"
A="http://schemas.openxmlformats.org/drawingml/2006/main"
ns={"a":A,"p":P}
for n in [1,6,16,26,28]:
    root = etree.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    print("="*15, "SLIDE", n)
    spTree = root.find(".//p:spTree", ns)
    for child in spTree:
        tag = etree.QName(child).localname
        if tag not in ("sp","grpSp","pic","graphicFrame"): continue
        name = child.find(".//p:cNvPr", ns).get("name")
        off = child.find("./p:spPr/a:xfrm/a:off", ns) or child.find("./p:grpSpPr/a:xfrm/a:off", ns)
        ext = child.find("./p:spPr/a:xfrm/a:ext", ns) or child.find("./p:grpSpPr/a:xfrm/a:ext", ns)
        pos=""
        if off is not None and ext is not None:
            pos=f"({int(off.get('x'))/914400:.2f},{int(off.get('y'))/914400:.2f}) {int(ext.get('cx'))/914400:.2f}x{int(ext.get('cy'))/914400:.2f}"
        paras=[]
        for p in child.findall(".//a:p", ns):
            t="".join(x.text or "" for x in p.findall(".//a:t", ns))
            if not t.strip(): continue
            rpr=p.find("./a:r/a:rPr", ns) or p.find("./a:endParaRPr", ns)
            sz=rpr.get("sz") if rpr is not None else None
            b=rpr.get("b") if rpr is not None else None
            ppr=p.find("./a:pPr", ns)
            algn=ppr.get("algn") if ppr is not None else None
            paras.append(f"[sz={sz} b={b} al={algn}] {t}")
        print(f"{tag:8} {name!r:28} {pos:34} {' || '.join(paras)}")
