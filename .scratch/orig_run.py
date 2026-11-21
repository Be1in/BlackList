import zipfile
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
A="http://schemas.openxmlformats.org/drawingml/2006/main"
P="http://schemas.openxmlformats.org/presentationml/2006/main"
root = etree.fromstring(z.read("ppt/slides/slide1.xml"))
for sp in root.iter("{%s}sp"%P):
    nm=sp.find(".//{%s}cNvPr"%P).get("name")
    if nm.startswith("Google Shape;164"):
        for r in sp.iter("{%s}r"%A):
            print(etree.tostring(r, pretty_print=True).decode())
