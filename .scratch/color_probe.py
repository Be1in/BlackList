import zipfile, collections
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
A="http://schemas.openxmlformats.org/drawingml/2006/main"
P="http://schemas.openxmlformats.org/presentationml/2006/main"
ns={"a":A}
for n in [1,6,16,28]:
    root = etree.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    c=collections.Counter()
    for sp in root.iter("{%s}sp"%P):
        name=sp.find(".//{%s}cNvPr"%P).get("name")
        txt="".join(t.text or "" for t in sp.iter("{%s}t"%A))[:22]
        fills=sp.findall(".//a:rPr/a:solidFill/a:srgbClr", ns)
        for f in fills:
            c[(f.get("val"), txt)] += 1
    print("== slide",n)
    for k,v in c.most_common(14): print("  ",k,v)
