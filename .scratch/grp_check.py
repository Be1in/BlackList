import zipfile
from lxml import etree
P="http://schemas.openxmlformats.org/presentationml/2006/main"
A="http://schemas.openxmlformats.org/drawingml/2006/main"
ns={"p":P,"a":A}
z=zipfile.ZipFile(r"d:\BlackList\MSBD5017_Project_Pitch.pptx")
root=etree.fromstring(z.read("ppt/slides/slide6.xml"))
g=root.find(".//p:grpSp",ns)
x=g.find("./p:grpSpPr/a:xfrm",ns)
for tag in ("a:off","a:ext","a:chOff","a:chExt"):
    e=x.find(tag,ns); print(tag, {k:round(int(v)/914400,2) for k,v in e.attrib.items()})
