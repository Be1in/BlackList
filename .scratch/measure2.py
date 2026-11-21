from PIL import ImageFont
f = r"C:\Windows\Fonts\arialbd.ttf"
cands = ["Turning freeze logs into a record anyone can check",
         "Turning on-chain freeze messages into a public record",
         "A public record of stablecoin freezes",
         "From freeze notices to a public record",
         "Making stablecoin freezes visible and checkable"]
for size in (24, 25):
    font = ImageFont.truetype(f, size*4)
    print("size", size, "avail 9.0in =", 9*72, "pt")
    for s in cands:
        w = font.getlength(s)/4
        print(f"  {w:6.1f}pt {w/72:5.2f}in  {s}")
