import zipfile
from lxml import etree
p = r"d:\BlackList\MSBD5017_Project_Pitch.pptx"
z = zipfile.ZipFile(p)
A="http://schemas.openxmlformats.org/drawingml/2006/main"
P="http://schemas.openxmlformats.org/presentationml/2006/main"
ns={"a":A,"p":P}
root=etree.fromstring(z.read("ppt/slides/slide1.xml"))
for sp in root.iter("{%s}sp"%P):
    nm=sp.find(".//{%s}cNvPr"%P).get("name")
    txt="".join(t.text or "" for t in sp.iter("{%s}t"%A))
    if txt.strip():
        print(nm, "|", txt)
print("--- core ---")
core=z.read("docProps/core.xml").decode("utf-8")
import re
print(re.findall(r"<dc:title>(.*?)</dc:title>", core))
