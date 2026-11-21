import zipfile, re
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
A="http://schemas.openxmlformats.org/drawingml/2006/main"
P="http://schemas.openxmlformats.org/presentationml/2006/main"
ns={"a":A,"p":P}
for n,names in [(1,["Google Shape;164;p13","Google Shape;165;p13"]),(6,["Google Shape;1541;p18","Google Shape;1542;p18","Google Shape;1543;p18"]),(16,["Google Shape;2417;p28","Google Shape;2418;p28","Google Shape;2419;p28"]),(28,["Google Shape;4640;p40","Google Shape;4641;p40","Google Shape;4642;p40"]),(26,["Google Shape;4441;p38","Google Shape;4442;p38"])]:
    root = etree.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    for target in names:
        for sp in root.iter("{%s}sp"%P):
            cNvPr = sp.find(".//p:cNvPr", ns)
            if cNvPr is not None and cNvPr.get("name")==target:
                print("---- slide",n,target)
                print(etree.tostring(sp, pretty_print=True).decode()[:2600])
