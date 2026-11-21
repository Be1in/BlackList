import zipfile, collections
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
A="http://schemas.openxmlformats.org/drawingml/2006/main"
for n in [1,6,16,28,26]:
    root = etree.fromstring(z.read(f"ppt/slides/slide{n}.xml"))
    c = collections.Counter()
    for e in root.iter("{%s}latin"%A):
        c[e.get("typeface")] += 1
    print(n, c.most_common())
