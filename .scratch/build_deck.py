# -*- coding: utf-8 -*-
"""Build the MSBD5017 pitch deck from the Slidesgo template."""
import copy
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.oxml.ns import qn

SRC = r"d:\BlackList\.scratch\template.pptx"
OUT = r"d:\BlackList\MSBD5017_Project_Pitch.pptx"

NAVY = RGBColor(0x30, 0x47, 0x5E)
DARK = RGBColor(0x22, 0x28, 0x31)
ORANGE = RGBColor(0xF2, 0xA3, 0x65)
GRAY = RGBColor(0x8A, 0x96, 0xA3)
BODY_FONT = "Fira Sans"
MED_FONT = "Fira Sans Medium"

prs = Presentation(SRC)

# ------------------------------------------------------------------ slide set
KEEP = [0, 5, 27, 15]           # cover, dashboard, lane flow, shield layout
sldIdLst = prs.slides._sldIdLst
sldIds = list(sldIdLst)
for i, sid in enumerate(sldIds):
    if i not in KEEP:
        prs.part.drop_rel(sid.rId)
        sldIdLst.remove(sid)
# reorder to the KEEP order
kept = {i: sid for i, sid in enumerate(sldIds) if i in KEEP}
for sid in list(sldIdLst):
    sldIdLst.remove(sid)
for i in KEEP:
    sldIdLst.append(kept[i])

slides = list(prs.slides)
cover, problem, solution, progress = slides

# ------------------------------------------------------------------ helpers
def shape_by_name(slide, name):
    for sh in slide.shapes:
        if sh.name == name or sh.name.startswith("Google Shape;%s;" % name):
            return sh
    raise KeyError(name)

def set_rich_text(shape, paragraphs, size=None, align=None, color=None):
    """paragraphs: list of paragraphs, each a list of (text, bold_or_None)."""
    txBody = shape.text_frame._txBody
    ps = txBody.findall(qn("a:p"))
    tmpl_p = ps[0]
    tmpl_r = tmpl_p.find(qn("a:r"))
    if tmpl_r is None:
        tmpl_r = tmpl_p.find(qn("a:endParaRPr"))
        # build a minimal run if the paragraph only has endParaRPr
        from lxml import etree
        tmpl_r = etree.SubElement(etree.Element(qn("a:p")), qn("a:r"))
        rpr = etree.SubElement(tmpl_r, qn("a:rPr"))
        etree.SubElement(tmpl_r, qn("a:t"))
    for p in ps:
        txBody.remove(p)

    for runs in paragraphs:
        newp = copy.deepcopy(tmpl_p)
        for child in list(newp):
            if child.tag in (qn("a:r"), qn("a:br"), qn("a:fld")):
                newp.remove(child)
        pPr = newp.find(qn("a:pPr"))
        if pPr is not None and align is not None:
            pPr.set("algn", align)
        for item in runs:
            text, bold = item[0], item[1]
            run_color = item[2] if len(item) > 2 else color
            nr = copy.deepcopy(tmpl_r)
            t = nr.find(qn("a:t"))
            t.text = text
            rPr = nr.find(qn("a:rPr"))
            if rPr is not None:
                if bold is True:
                    rPr.set("b", "1")
                elif bold is False and "b" in rPr.attrib:
                    del rPr.attrib["b"]
                if size:
                    rPr.set("sz", str(int(round(size * 100))))
                if run_color is not None:
                    for f in rPr.findall(qn("a:solidFill")):
                        rPr.remove(f)
                    fill = rPr.makeelement(qn("a:solidFill"), {})
                    clr = fill.makeelement(qn("a:srgbClr"), {"val": "%02X%02X%02X" % (run_color[0], run_color[1], run_color[2])})
                    fill.append(clr)
                    rPr.append(fill)
            end_rpr = newp.find(qn("a:endParaRPr"))
            if end_rpr is not None:
                end_rpr.addprevious(nr)
            else:
                newp.append(nr)
        txBody.append(newp)

def add_text(slide, x, y, w, h, paragraphs, size=12, bold=False, color=NAVY,
             align=PP_ALIGN.LEFT, font=BODY_FONT, line_spacing=None, space_after=0):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = 0
    first = True
    for para in paragraphs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        if line_spacing:
            p.line_spacing = line_spacing
        p.space_after = Pt(space_after)
        runs = para if isinstance(para, list) else [(para, bold)]
        for text, b in runs:
            r = p.add_run()
            r.text = text
            r.font.name = font
            r.font.size = Pt(size)
            r.font.bold = bold if b is None else b
            r.font.color.rgb = color
    return box

def add_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text

# ================================================================== 1. COVER
title = shape_by_name(cover, "Google Shape;164;p13")
title.left, title.top = Inches(0.5), Inches(1.30)
title.width = Inches(4.35)
set_rich_text(title, [[("Freeze Events,", True)], [("Reconstructed", True)]], size=40, align="l", color=DARK)

sub = shape_by_name(cover, "Google Shape;165;p13")
sub.left, sub.top, sub.width, sub.height = Inches(0.5), Inches(2.84), Inches(4.1), Inches(0.75)
set_rich_text(sub, [[("A reproducible, auditable dataset of USDT and USDC blacklist "
                     "events on Ethereum \u2014 including the gaps it cannot close.", False)]],
              size=12.5, align="l", color=NAVY)

add_text(cover, 0.5, 3.72, 4.2, 0.28,
         [[("PENG Beining  \u00b7  CHANG Juxiao  \u00b7  LI Mingzheng", True)]],
         size=13, color=DARK)
add_text(cover, 0.5, 4.06, 4.2, 0.24,
         ["Presenting today: all three authors"], size=10, color=GRAY)
add_text(cover, 0.5, 4.33, 4.2, 0.26,
         [[("For stablecoin issuers, regulators and on-chain analysts", False)]],
         size=10.5, color=ORANGE)
add_text(cover, 0.5, 4.66, 4.2, 0.24,
         ["MSBD5017  \u00b7  Project pitch  \u00b7  20 November 2026"],
         size=8.5, color=GRAY)
add_text(cover, 0.5, 5.33, 3.0, 0.18,
         ["Template: Slidesgo \u00b7 Freepik"], size=7, color=RGBColor(0xB4, 0xBB, 0xC4))

add_notes(cover, "We are the MSBD5017 team: PENG Beining, CHANG Juxiao and LI Mingzheng. "
                 "We study stablecoin freeze events: we rebuilt every USDT and USDC "
                 "blacklist event on Ethereum into one reproducible dataset, and we are explicit "
                 "about what it cannot tell you.")

# ================================================================== 2. PROBLEM
ptitle = shape_by_name(problem, "Google Shape;1541;p18")
set_rich_text(ptitle, [[("US$4.4bn", True)], [("frozen.", True)], [("Still no", True)],
                       [("answers.", True)]], size=28, align="ctr", color=DARK)

items = [
    ("1542", "1543", "01", "Reason missing.",
     " A freeze is a public log; why, who asked and what follows never are.", RGBColor(0xB8, 0xC2, 0xCC)),
    ("1544", "1545", "02", "Definitions drift.",
     " Freeze, unfreeze and destroy are rarely defined separately.", NAVY),
    ("1546", "1547", "03", "Balances blur.",
     " Value frozen at the start is not value still frozen at cutoff.", ORANGE),
    ("1548", "1549", "04", "No audit trail.",
     " Dashboards give counts, never coverage, error or missing data.", DARK),
]
for num_name, body_name, num, lead, rest, accent in items:
    set_rich_text(shape_by_name(problem, num_name), [[(num, True)]], size=20, align="l", color=accent)
    set_rich_text(shape_by_name(problem, body_name),
                  [[(lead, True), (rest, False)]], size=12.5, align="l", color=DARK)

add_text(problem, 0.5, 4.62, 2.4, 0.34,
         ["Tether disclosure, April 2026 (issuer-reported)"], size=8, color=GRAY)

add_notes(problem, "The specific problem: the event is observable, the meaning is not. Tether reports "
                   "more than US$4.4 billion frozen since launch, including a US$344 million "
                   "action with OFAC, and freeze authority is now being tested in court. Yet the chain "
                   "shows only that an address was frozen. It does not show why, who requested it, who "
                   "the address belongs to, or what happens on appeal. Existing dashboards count "
                   "events, but without shared definitions, coverage or error, their issuer totals "
                   "reflect data processing choices more than behaviour.")

# ================================================================== 3. SOLUTION
sol_title = shape_by_name(solution, "Google Shape;4640;p40")
set_rich_text(sol_title, [[("One pipeline, stated limits", True)]], size=25, align="l", color=DARK)

add_text(solution, 5.45, 0.46, 3.95, 0.55,
         [[("One auditable baseline for issuers, regulators and researchers.", False)]],
         size=10.5, color=NAVY, align=PP_ALIGN.RIGHT)

steps = [
    ("4643", "4644", "1  Extract", "Every USDT and USDC log, one block range."),
    ("4647", "4648", "2  Define", "Freeze episodes; two balances kept apart."),
    ("4641", "4642", "3  Verify", "21,492 cross-checks; zero balance error."),
    ("4645", "4646", "4  Disclose", "Coverage, gaps and limits, documented."),
]
for t_name, b_name, lead, body in steps:
    set_rich_text(shape_by_name(solution, t_name), [[(lead, True)]], size=13, align="l", color=DARK)
    set_rich_text(shape_by_name(solution, b_name), [[(body, False)]], size=11.5, align="l", color=NAVY)

add_notes(solution, "Our solution is a dataset, not a dashboard. We extract every freeze, "
                    "unfreeze and destruction log for USDT and USDC on Ethereum across one fixed "
                    "block range. We define the unit of analysis, the freeze episode, and two separate "
                    "balances: value at the moment of freezing, and value still frozen at the study "
                    "cutoff. We verify every reconstruction against an independent archive endpoint: "
                    "21,492 checks, all matching. And unlike existing trackers, we publish the "
                    "coverage, the missingness and the questions the data cannot answer.")

# ================================================================== 4. PROGRESS
prog_title = shape_by_name(progress, "Google Shape;2417;p28")
set_rich_text(prog_title, [[("Where we are, and what we need", True)]], size=33, align="ctr", color=DARK)

left = [
    ("2418", "2419", "Built", "5,970 events \u00b7 4,077 episodes \u00b7 3,573 frozen"),
    ("2420", "2421", "Now", "OFAC match 3.2%; labels unavailable"),
    ("2422", "2423", "Next", "Issuer comparison, sensitivity, paper"),
]
for t_name, b_name, lead, body in left:
    set_rich_text(shape_by_name(progress, t_name), [[(lead, True)]], size=16, align="l", color=ORANGE)
    set_rich_text(shape_by_name(progress, b_name), [[(body, False)]], size=10.5, align="l", color=DARK)

right = [
    ("2424", "2425", "Q1", "Is one shared block window fair to both issuers?"),
    ("2426", "2427", "Q2", "Unmatched addresses: report as unknown?"),
    ("2428", "2429", "Q3", "Which gap matters most to regulators?"),
]
for t_name, b_name, lead, body in right:
    set_rich_text(shape_by_name(progress, t_name), [[(lead, True)]], size=16, align="r", color=NAVY)
    set_rich_text(shape_by_name(progress, b_name), [[(body, False)]], size=10.5, align="r", color=DARK)

add_notes(progress, "Progress. Completed: the full dataset, the data dictionary and a 67-check "
                    "contract audit, all passing; 5,970 events, 4,077 freeze episodes and 3,573 "
                    "addresses still frozen at the cutoff. In progress: OFAC matching, currently 3.2% "
                    "of events; the exchange-label layer is blocked because no free source has dated "
                    "deposit addresses, so we report it as not attempted rather than zero. A second "
                    "risk: 215 USDC events have low-confidence contract versions, so they stay out of "
                    "version-sensitive comparisons. Next: the "
                    "issuer comparison on a shared window, sensitivity analysis, then the paper. "
                    "Three questions for you: one, is a shared block window enough to make issuer "
                    "comparisons credible? Two, should unmatched addresses be reported as unknown "
                    "rather than unsanctioned? Three, which documented gap matters most to regulators?")

prs.core_properties.title = "Freeze Events, Reconstructed"
prs.core_properties.author = "PENG Beining; CHANG Juxiao; LI Mingzheng"
prs.core_properties.subject = "MSBD5017 project pitch: observable stablecoin freeze events"
prs.core_properties.comments = "Template: Technology Infographics by Slidesgo (Freepik)"

prs.save(OUT)
print("saved", OUT, "slides:", len(prs.slides))
