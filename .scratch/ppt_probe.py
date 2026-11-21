from pptx import Presentation
from pptx.util import Emu
p = Presentation(r"d:\BlackList\.scratch\template.pptx")
print("size", p.slide_width, p.slide_height, p.slide_width/914400, p.slide_height/914400)
for i, s in enumerate(p.slides, 1):
    print("=== SLIDE", i, "layout:", s.slide_layout.name)
    for sh in s.shapes:
        t = ""
        if sh.has_text_frame:
            t = sh.text_frame.text.replace("\n", " | ")[:90]
        print(f"  [{sh.shape_type}] name={sh.name!r} pos=({sh.left/914400:.2f},{sh.top/914400:.2f}) size=({sh.width/914400:.2f}x{sh.height/914400:.2f}) :: {t}")
