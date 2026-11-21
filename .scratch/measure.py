from PIL import ImageFont
f = r"C:\Windows\Fonts\arialbd.ttf"
lines = ["Observable blacklist", "Observable", "blacklist events", "events and their", "and their governance", "governance", "Observable blacklist events", "and their governance"]
for size in (40, 36, 34, 32, 30, 28, 26):
    font = ImageFont.truetype(f, size*4)  # 4x for precision
    print("size", size)
    for s in lines:
        w = font.getlength(s)/4
        print(f"   {w:6.1f}pt  {w/72:5.2f}in  {s}")
