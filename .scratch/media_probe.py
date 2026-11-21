import zipfile
z = zipfile.ZipFile(r"d:\BlackList\.scratch\template.pptx")
media = [(n, z.getinfo(n).file_size) for n in z.namelist() if n.startswith("ppt/media")]
for n,s in media[:60]:
    print(n, s)
print("total media", len(media))
print([n for n in z.namelist() if n.startswith("docProps")])
