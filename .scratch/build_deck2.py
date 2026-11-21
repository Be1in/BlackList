# -*- coding: utf-8 -*-
"""Text-rich rebuild of the MSBD5017 pitch deck on the Slidesgo template."""
import copy
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

SRC = r"d:\BlackList\.scratch\template.pptx"
OUT = r"d:\BlackList\MSBD5017_Project_Pitch.pptx"

NAVY = RGBColor(0x30, 0x47, 0x5E)
DARK = RGBColor(0x22, 0x28, 0x31)
ORANGE = RGBColor(0xF2, 0xA3, 0x65)
GRAY = RGBColor(0x8A, 0x96, 0xA3)
LIGHT = RGBColor(0xF4, 0xF5, 0xF6)
MUTED = RGBColor(0xB8, 0xC2, 0xCC)
BODY_FONT = "Fira Sans"
MED_FONT = "Fira Sans Medium"

prs = Presentation(SRC)

KEEP = [0, 5, 27, 15]           # cover, dashboard, lane, shield
sldIdLst = prs.slides._sldIdLst
sldIds = list(sldIdLst)
for i, sid in enumerate(sldIds):
    if i not in KEEP:
        prs.part.drop_rel(sid.rId)
        sldIdLst.remove(sid)
kept = {i: sid for i, sid in enumerate(sldIds) if i in KEEP}
for sid in list(sldIdLst):
    sldIdLst.remove(sid)
for i in KEEP:
    sldIdLst.append(kept[i])

slides = list(prs.slides)
cover, problem, solution, progress = slides

def shape_by_name(slide, name):
    for sh in slide.shapes:
        if sh.name == name or sh.name.startswith("Google Shape;%s;" % name):
            return sh
    raise KeyError(name)

def clear_slide(slide, keep=()):
    for sh in list(slide.shapes):
        if sh.name in keep:
            continue
        sh._element.getparent().remove(sh._element)

def set_rich_text(shape, paragraphs, size=None, align=None, color=None, space_after=None):
    txBody = shape.text_frame._txBody
    ps = txBody.findall(qn("a:p"))
    tmpl_p = ps[0]
    tmpl_r = tmpl_p.find(qn("a:r"))
    if tmpl_r is None:
        from lxml import etree
        tmpl_r = etree.Element(qn("a:r"))
        etree.SubElement(tmpl_r, qn("a:rPr"))
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

def add_text(slide, x, y, w, h, paragraphs, size=10.5, color=DARK,
             align=PP_ALIGN.LEFT, font=BODY_FONT, line_spacing=1.2,
             space_before=0, space_after=0, anchor=MSO_ANCHOR.TOP):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = 0
    tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    first = True
    for para in paragraphs:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.alignment = align
        p.line_spacing = line_spacing
        p.space_before = Pt(space_before)
        p.space_after = Pt(space_after)
        runs = para if isinstance(para, list) else [(para, False)]
        for item in runs:
            text = item[0]
            bold = item[1] if len(item) > 1 else False
            run_color = item[2] if len(item) > 2 else color
            r = p.add_run()
            r.text = text
            r.font.name = font
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.color.rgb = run_color
    return box

def add_block(slide, x, y, w, h, num, lead, body, num_color=ORANGE):
    """Big numeral + bold lead + body paragraph."""
    add_text(slide, x, y, 0.62, 0.6, [[(num, True)]], size=24, color=num_color, line_spacing=1.0)
    add_text(slide, x + 0.58, y + 0.02, w - 0.58, 0.3, [[(lead, True)]], size=11.5, color=DARK)
    add_text(slide, x + 0.58, y + 0.31, w - 0.58, h - 0.31, [[(body, False)]],
             size=10.5, color=NAVY, line_spacing=1.18)

def add_notes(slide, text):
    slide.notes_slide.notes_text_frame.text = text

# ================================================================== 1. COVER
title = shape_by_name(cover, "Google Shape;164;p13")
title.left, title.top = Inches(0.5), Inches(1.34)
title.width, title.height = Inches(4.35), Inches(1.7)
set_rich_text(title, [[("Observable", True)], [("blacklist events", True)], [("and their governance", True)]],
              size=30, align="l", color=DARK)

sub = shape_by_name(cover, "Google Shape;165;p13")
sub.left, sub.top, sub.width, sub.height = Inches(0.5), Inches(3.02), Inches(4.15), Inches(0.75)
set_rich_text(sub, [[("A reproducible Ethereum dataset of USDT and USDC blacklist events "
                     "\u2014 and the governance questions it can, and cannot, answer.", False)]],
              size=12, align="l", color=NAVY)

add_text(cover, 0.5, 3.88, 4.2, 0.28,
         [[("PENG Beining  \u00b7  CHANG Juxiao  \u00b7  LI Mingzheng", True)]], size=13, color=DARK)
add_text(cover, 0.5, 4.18, 4.2, 0.24, ["Presenting today: all three authors"], size=10, color=GRAY)
add_text(cover, 0.5, 4.48, 4.2, 0.26,
         [[("For stablecoin issuers, regulators and on-chain analysts", False)]], size=10.5, color=ORANGE)
add_text(cover, 0.5, 4.80, 4.3, 0.24,
         ["MSBD5017  \u00b7  Project pitch  \u00b7  20 November 2026"], size=8.5, color=GRAY)
add_text(cover, 0.5, 5.33, 3.0, 0.18, ["Template: Slidesgo \u00b7 Freepik"], size=7, color=MUTED)

add_notes(cover, "We are the MSBD5017 team: PENG Beining, CHANG Juxiao and LI Mingzheng. "
                 "We study stablecoin freeze events: we rebuilt every USDT and USDC blacklist event "
                 "on Ethereum into one reproducible dataset, and we are explicit about what it cannot "
                 "tell you.")

# ================================================================== 2. PROBLEM
clear_slide(problem, keep=("Google Shape;1499;p18",))
art = shape_by_name(problem, "Google Shape;1499;p18")
art.left, art.top, art.width, art.height = Inches(6.55), Inches(1.02), Inches(2.60), Inches(3.46)

add_text(problem, 0.5, 0.42, 5.55, 0.86,
         [[("US$4.4bn frozen. ", True), ("The reasons are still private.", True)]],
         size=25, color=DARK, line_spacing=1.05)
add_text(problem, 0.5, 1.28, 5.5, 0.46,
         [[("Why it matters: ", True),
           ("billions in user funds can be frozen with no public reason or appeal \u2014 and "
            "courts are now testing that power.", False)]],
         size=10.5, color=ORANGE, line_spacing=1.15)

add_block(problem, 0.5, 1.80, 5.5, 1.1, "01",
          "The event is public; the authority is not.",
          "A freeze is one on-chain log. Who holds the freeze key, on what request, for which "
          "entity, and with what appeal path never appear on chain.")
add_block(problem, 0.5, 2.82, 5.5, 1.1, "02",
          "Definitions are not shared.",
          "Freezing, unfreezing and destruction are different events, and value at freeze is not "
          "value still frozen at cutoff. Merge them and every issuer comparison moves.",
          num_color=NAVY)
add_block(problem, 0.5, 4.02, 5.5, 1.05, "03",
          "Counts are not evidence.",
          "Public trackers report totals without saying what they cover, how many records they "
          "checked, or what they cannot see. A precise number can hide an unauditable method.",
          num_color=MUTED)

add_text(problem, 0.5, 5.16, 4.6, 0.24,
         ["Tether disclosure, April 2026 (issuer-reported)"], size=8, color=GRAY)

add_notes(problem, "The problem is specific: Tether reports more than US$4.4 billion frozen, "
                   "including a US$344 million action with OFAC, and freeze authority is now "
                   "being tested in court. Why it matters: billions of user funds can be frozen "
                   "with no public reason and no clear appeal. Yet the chain records only that an "
                   "address was frozen, not who asked or what happens next. Public trackers make "
                   "this worse: they count events without shared definitions, coverage or error "
                   "rates, so a precise-looking number can hide an unauditable method.")

# ================================================================== 3. SOLUTION
clear_slide(solution)

add_text(solution, 0.5, 0.40, 9.0, 0.55,
         [[("Turning freeze logs into a record anyone can check", True)]], size=24, color=DARK, line_spacing=1.0)
add_text(solution, 0.5, 1.02, 9.0, 0.55,
         [[("Issuers can freeze any wallet, but the public only sees a short message on chain. "
            "We turn those messages into one comparable record.", False)]],
         size=11, color=NAVY, line_spacing=1.15)

add_block(solution, 0.50, 1.78, 4.35, 1.35, "01", "What it covers",
          "5,970 freeze, unfreeze and destroy records for USDT and USDC on Ethereum, from 2020 to "
          "November 2026 \u2014 covering 4,077 separate freeze cases.")
add_block(solution, 5.05, 1.78, 4.35, 1.35, "02", "What we measure",
          "For each case, two separate amounts: what was locked at the moment of freezing, and what "
          "is still locked today. They are never added together.", num_color=NAVY)
add_block(solution, 0.50, 3.12, 4.35, 1.15, "03", "How we check it",
          "Every amount is rebuilt from raw chain data and compared with a second independent "
          "archive. All checks match, and anyone can rerun the build.", num_color=NAVY)
add_block(solution, 5.05, 3.12, 4.35, 1.15, "04", "What we admit",
          "We publish what we cannot verify \u2014 who owns a wallet, why a freeze happened \u2014 "
          "instead of guessing. Gaps are labelled, not filled with zeros.")

add_text(solution, 0.5, 4.40, 9.0, 0.42,
         [[("What is different: ", True), ("every figure can be rebuilt and checked, and the gaps "
                                           "are published rather than hidden.", False)]],
         size=10.5, color=NAVY, line_spacing=1.2)

add_text(solution, 0.5, 4.94, 9.0, 0.35,
         [[("Value: regulators can compare issuers, researchers can reuse the data, and compliance "
            "teams can see what is really frozen.", True)]],
         size=11, color=ORANGE)

add_notes(solution, "Our solution turns those on-chain messages into a record anyone can check. We "
                    "collect 5,970 freeze, unfreeze and destroy records for USDT and USDC on Ethereum "
                    "between 2020 and November 2026, and group them into 4,077 freeze cases. For each "
                    "case we report two separate amounts: what was locked at the moment of freezing, "
                    "and what is still locked today. We never add them together, because that would "
                    "double-count. Every amount is rebuilt from raw chain data and compared against a "
                    "second independent archive: all checks match, and the build can be rerun by "
                    "anyone. Most trackers stop at counts; we also publish what we cannot verify, such "
                    "as who owns a wallet or why a freeze happened. In technical terms, the two "
                    "amounts are D1 and D2: D1 is the balance at the end of the block before the "
                    "freeze starts, adjusted for earlier transfers in the same block; D2 is "
                    "re-queried at the cutoff block for addresses still blacklisted.")

# ================================================================== 4. PROGRESS
clear_slide(progress)

add_text(progress, 0.5, 0.38, 9.0, 0.55,
         [[("Where we are, and what we need from you", True)]], size=24, color=DARK, line_spacing=1.0)

add_text(progress, 0.5, 1.15, 5.35, 0.28, [[("Completed", True)]], size=12.5, color=ORANGE)
add_text(progress, 0.5, 1.43, 5.35, 0.95,
         [[("5,970 events, 4,077 freeze cases and 9,466 balance rows, including 3,573 wallet "
            "balances still frozen at the cutoff. The 67/67 audit passes, and the dataset rebuilds "
            "from a fixed snapshot with published scripts.", False)]],
         size=10.5, color=NAVY, line_spacing=1.18)

add_text(progress, 0.5, 2.60, 5.35, 0.28, [[("Risk", True)]], size=12.5, color=NAVY)
add_text(progress, 0.5, 2.88, 5.35, 0.9,
         [[("215 USDC events have low-confidence contract versions and stay out of version-sensitive "
            "comparisons. Exchange labels are not attempted: no free source carries dated deposit "
            "addresses.", False)]],
         size=10.5, color=NAVY, line_spacing=1.18)

add_text(progress, 0.5, 3.95, 5.35, 0.28, [[("Next", True)]], size=12.5, color=NAVY)
add_text(progress, 0.5, 4.23, 5.35, 0.5,
         [[("Write the empirical paper on issuer freeze behaviour.", False)]],
         size=10.5, color=NAVY, line_spacing=1.18)

panel = progress.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(6.20), Inches(1.10),
                                  Inches(3.30), Inches(3.62))
panel.fill.solid()
panel.fill.fore_color.rgb = LIGHT
panel.line.fill.background()
panel.shadow.inherit = False
panel.adjustments[0] = 0.06

add_text(progress, 6.40, 1.28, 2.90, 0.3,
         [[("Questions for feedback", True)]], size=12, color=NAVY)
add_text(progress, 6.40, 1.62, 2.90, 2.92, [
    [("Q1  ", True, ORANGE), ("Is a shared block window enough to make issuer comparisons credible?",
                              False, DARK)],
    [("Q2  ", True, ORANGE), ("Should unmatched addresses be reported as unknown rather than as "
                              "unsanctioned?", False, DARK)],
    [("Q3  ", True, ORANGE), ("Which gap matters most to regulators: exchange labels, Tron, or the "
                              "90 pre-window events?", False, DARK)],
], size=10.5, color=DARK, line_spacing=1.2, space_after=16)

add_notes(progress, "Progress. Completed: the dataset itself \u2014 5,970 events, 4,077 freeze cases, "
                    "9,466 balance rows, and 3,573 wallet balances still frozen at the cutoff, which "
                    "is 2,867 USDT plus 706 USDC. The 67-check audit passes, and the dataset rebuilds "
                    "from a fixed snapshot with the published scripts. Risks: 215 USDC events have "
                    "low-confidence contract versions, so they stay out of version-sensitive "
                    "comparisons; and the exchange-label layer is not attempted, because no free "
                    "source carries dated deposit addresses. Next: write the empirical paper on "
                    "issuer freeze behaviour. If asked why the OFAC match rate is only 3.2%: it is a "
                    "coverage limit, not a completion rate \u2014 most issuer freezes are not "
                    "sanctions cases, we use a single current snapshot rather than a historical "
                    "series, and we match address strings exactly without inferring entities. Three "
                    "questions for you: is a shared block window enough to make issuer comparisons "
                    "credible? Should unmatched addresses be reported as unknown rather than "
                    "unsanctioned? And which documented gap matters most to regulators \u2014 "
                    "exchange labels, Tron, or the 90 pre-window events?")

prs.core_properties.title = "Observable blacklist events and their governance"
prs.core_properties.author = "PENG Beining; CHANG Juxiao; LI Mingzheng"
prs.core_properties.subject = "MSBD5017 project pitch: observable stablecoin freeze events"
prs.core_properties.comments = "Template: Technology Infographics by Slidesgo (Freepik)"

prs.save(OUT)
print("saved", OUT, "slides:", len(prs.slides))
