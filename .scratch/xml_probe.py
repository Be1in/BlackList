import zipfile
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
ns = {"a":"http://schemas.openxmlformats.org/drawingml/2006/main",
      "p":"http://schemas.openxmlformats.org/presentationml/2006/main"}
for n in [1,6,16,26,28]:
    root = etree.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    print("="*20, "SLIDE", n)
    for sp in root.iter("{%s}sp" % ns["p"], "{%s}grpSp" % ns["p"]):
        name = sp.find(".//p:cNvPr", ns).get("name")
        off = sp.find(".//a:off", ns); ext = sp.find(".//a:ext", ns)
        pos = f"({int(off.get('x'))/914400:.2f},{int(off.get('y'))/914400:.2f}) {int(ext.get('cx'))/914400:.2f}x{int(ext.get('cy'))/914400:.2f}" if off is not None and ext is not None else ""
        texts = []
        for p in sp.findall(".//a:p", ns):
            t = "".join(x.text or "" for x in p.findall(".//a:t", ns))
            rpr = p.find(".//a:rPr", ns)
            sz = rpr.get("sz") if rpr is not None else None
            algn = p.find("./a:pPr", ns).get("algn") if p.find("./a:pPr", ns) is not None else None
            if t.strip(): texts.append(f"[{sz},{algn}]{t}")
        kind = "GRP" if sp.tag.endswith("grpSp") else "SP"
        if kind=="SP" or texts:
            print(f" {kind} {name!r} {pos} :: {' || '.join(texts)[:160]}")
