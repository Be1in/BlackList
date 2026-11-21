import zipfile, re
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
print([n for n in z.namelist() if "theme" in n])
d = z.read("ppt/theme/theme1.xml").decode("utf-8")
import re
print(re.findall(r"<a:clrScheme.*?</a:clrScheme>", d, re.S)[0][:2000])
print(re.findall(r"<a:fontScheme.*?</a:fontScheme>", d, re.S)[0][:1200])
