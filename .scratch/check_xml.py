import zipfile
from lxml import etree
z = zipfile.ZipFile(r"d:\BlackList\MSBD5017_Project_Pitch.pptx")
print([n for n in z.namelist() if n.startswith("ppt/slides/slide")])
root = etree.fromstring(z.read("ppt/slides/slide1.xml"))
P="http://schemas.openxmlformats.org/presentationml/2006/main"
for sp in root.iter("{%s}sp"%P):
    nm = sp.find(".//{%s}cNvPr"%P).get("name")
    if "164" in nm or "165" in nm:
        print("=====", nm)
        print(etree.tostring(sp, pretty_print=True).decode()[:4000])
