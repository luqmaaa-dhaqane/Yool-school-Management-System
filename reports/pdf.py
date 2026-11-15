"""Draws report cards as PDF with ReportLab (pure Python: installs everywhere, including Windows)."""
import io
from datetime import date
from xml.sax.saxutils import escape

from django.conf import settings
from django.contrib.staticfiles import finders
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

NAVY = colors.HexColor("#7c442c")      # school brown (table headers, rules)
DEEP = colors.HexColor("#3e2415")      # deep brown (titles)
GOLD = colors.HexColor("#ecc474")
LIGHT = colors.HexColor("#f5ede3")
LINE = colors.HexColor("#d9cdb9")
RED = colors.HexColor("#b3261e")
WIDTH = A4[0] - 30 * mm

_base = getSampleStyleSheet()
STYLE = {
    "school": ParagraphStyle("school", parent=_base["Title"], fontSize=18, leading=22, textColor=DEEP, alignment=0, spaceAfter=0),
    "sub": ParagraphStyle("sub", parent=_base["Normal"], fontSize=11, leading=14, textColor=colors.HexColor("#444444")),
    "cell": ParagraphStyle("cell", parent=_base["Normal"], fontSize=8.5, leading=10.5),
    "cellc": ParagraphStyle("cellc", parent=_base["Normal"], fontSize=8.5, leading=10.5, alignment=TA_CENTER),
    "head": ParagraphStyle("head", parent=_base["Normal"], fontSize=8, leading=10, textColor=colors.white, alignment=TA_CENTER),
    "headl": ParagraphStyle("headl", parent=_base["Normal"], fontSize=8, leading=10, textColor=colors.white),
    "label": ParagraphStyle("label", parent=_base["Normal"], fontSize=7.5, leading=9, textColor=colors.HexColor("#666666")),
    "value": ParagraphStyle("value", parent=_base["Normal"], fontSize=10, leading=12, fontName="Helvetica-Bold"),
    "h2": ParagraphStyle("h2", parent=_base["Heading3"], fontSize=10.5, leading=13, textColor=DEEP, spaceBefore=8, spaceAfter=3),
    "comment": ParagraphStyle("comment", parent=_base["Normal"], fontSize=9, leading=11.5),
    "small": ParagraphStyle("small", parent=_base["Normal"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555")),
    "warn": ParagraphStyle("warn", parent=_base["Normal"], fontSize=9, leading=11, textColor=RED, fontName="Helvetica-Bold", alignment=TA_CENTER),
}


def _p(text, style="cell"):
    return Paragraph(escape("" if text is None else str(text)), STYLE[style])


def _num(value):
    return "-" if value is None else f"{value:.1f}"


def _logo():
    path = finders.find(settings.SITE_LOGO_STATIC)
    if not path:
        return None
    try:
        image = Image(path)
        scale = min(22 * mm / image.imageHeight, 40 * mm / image.imageWidth)
        image.drawWidth, image.drawHeight = image.imageWidth * scale, image.imageHeight * scale
        return image
    except Exception:  # an unreadable logo must never stop report cards
        return None


def _card_story(card):
    story = []
    # --- header ----------------------------------------------------------
    title = [_p(card.school.name, "school"), _p("Student Report Card", "sub"),
             _p(f"Academic year {card.term.academic_year.name}  |  {card.term.name}", "sub")]
    logo = _logo()
    header = Table([[logo, title]] if logo else [[title]], colWidths=[46 * mm, WIDTH - 46 * mm] if logo else [WIDTH])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LINEBELOW", (0, 0), (-1, 0), 2.2, GOLD),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [header, Spacer(1, 4)]
    if card.provisional:
        story += [_p("PROVISIONAL: this term is not locked yet, so grades may still change.", "warn"), Spacer(1, 3)]

    # --- student ---------------------------------------------------------
    s = card.student
    dob = f"{s.date_of_birth:%d %b %Y} (age {s.age(card.term.ends_on)})"
    info = [
        [_p("Student", "label"), _p("Student no.", "label"), _p("Grade", "label")],
        [_p(s.full_name, "value"), _p(s.student_no, "value"), _p(card.section.display_name, "value")],
        [_p("Date of birth", "label"), _p("Gender", "label"), _p("Class teacher", "label")],
        [_p(dob, "value"), _p(s.get_gender_display(), "value"), _p(card.homeroom_teacher or "-", "value")],
    ]
    info_table = Table(info, colWidths=[WIDTH * 0.42, WIDTH * 0.24, WIDTH * 0.34])
    info_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), LIGHT), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                                    ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    story += [info_table, Spacer(1, 6)]

    # --- results -----------------------------------------------------------
    story.append(_p("Academic results", "h2"))
    if not card.has_grades:
        story.append(_p("No grades have been recorded for this term yet."))
    else:
        n_types = len(card.type_names)
        fixed = [32 * mm, 27 * mm] + [16 * mm] * n_types + [15 * mm, 13 * mm, 17 * mm]
        widths = fixed + [max(WIDTH - sum(fixed), 18 * mm)]
        head = [_p("Subject", "headl"), _p("Teacher", "headl")] + [_p(t, "head") for t in card.type_names] + [
            _p("Term %", "head"), _p("Grade", "head"), _p("Position", "head"), _p("Remark", "headl")]
        rows = [head]
        for line in card.lines:
            rows.append([_p(line.subject), _p(line.teacher)] + [_p(_num(line.by_type.get(t)), "cellc") for t in card.type_names] + [
                _p(_num(line.percent), "cellc"), _p(line.label or "-", "cellc"),
                _p(f"{line.rank} / {line.ranked_students}" if line.rank else "-", "cellc"), _p(line.remark)])
        table = Table(rows, colWidths=widths, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        style += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2, len(rows), 2)]
        table.setStyle(TableStyle(style))
        story.append(table)
        summary = Table([[_p("Overall average", "label"), _p("Overall grade", "label"), _p("Position in class", "label"), _p("Remark", "label")],
                         [_p(_num(card.overall) + ("%" if card.overall is not None else ""), "value"), _p(card.overall_label or "-", "value"),
                          _p(f"{card.rank} of {card.class_size}" if card.rank else "-", "value"), _p(card.overall_remark or "-", "value")]],
                        colWidths=[WIDTH * 0.25] * 4)
        summary.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, NAVY), ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
                                     ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story += [Spacer(1, 4), summary]

    # --- attendance --------------------------------------------------------
    a = card.attendance
    att = Table([[_p(h, "head") for h in ("Days school was open", "Days recorded", "Present", "Late", "Excused", "Absent", "Attendance")],
                 [_p(card.days_open, "cellc"), _p(a.days, "cellc"), _p(a.present, "cellc"), _p(a.late, "cellc"), _p(a.excused, "cellc"),
                  _p(a.absent, "cellc"), _p("-" if a.rate is None else f"{a.rate:.1f}%", "cellc")]], colWidths=[WIDTH / 7] * 7)
    att.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), NAVY), ("GRID", (0, 0), (-1, -1), 0.4, LINE)]))
    story += [_p("Attendance", "h2"), att]

    # --- comments and signatures -------------------------------------------
    def comment_box(title, text):
        box = Table([[_p(title, "label")], [Paragraph(escape(text).replace("\n", "<br/>") if text else "&nbsp;", STYLE["comment"])]],
                    colWidths=[WIDTH], rowHeights=[None, None])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, LINE), ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                                 ("TOPPADDING", (0, 1), (0, 1), 6), ("BOTTOMPADDING", (0, 1), (0, 1), 16)]))
        return box
    gap = 6 * mm
    cell = (WIDTH - 3 * gap) / 4
    sign = Table([[_p("Class teacher", "label"), "", _p("Principal", "label"), "", _p("Parent / guardian", "label"), "",
                   _p(f"Date issued: {date.today():%d %b %Y}", "label")]],
                 colWidths=[cell, gap, cell, gap, cell, gap, cell])
    sign.setStyle(TableStyle([("LINEABOVE", (0, 0), (0, 0), 0.5, colors.black), ("LINEABOVE", (2, 0), (2, 0), 0.5, colors.black),
                              ("LINEABOVE", (4, 0), (4, 0), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("TOPPADDING", (0, 0), (-1, -1), 2)]))
    story.append(KeepTogether([_p("Comments", "h2"), comment_box("Class teacher's comment", card.teacher_comment), Spacer(1, 4),
                               comment_box("Principal's comment", card.principal_comment), Spacer(1, 16 * mm), sign]))

    if card.grade_key:
        story += [Spacer(1, 6), _p("Grading key:  " + "   |   ".join(f"{label} {rng} {remark}".strip() for label, rng, remark in card.grade_key), "small")]
    return story


def render_report_cards(cards, compress=True):
    """One PDF with every card on its own page(s). Returns the PDF bytes."""
    buffer = io.BytesIO()
    footer_name = settings.SITE_NAME

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(15 * mm, 9 * mm, f"{footer_name}  |  {settings.SITE_SUBTITLE}")
        canvas.drawRightString(A4[0] - 15 * mm, 9 * mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=12 * mm, bottomMargin=16 * mm,
                            title="Report cards", author=footer_name, pageCompression=1 if compress else 0)
    story = []
    for i, card in enumerate(cards):
        if i:
            story.append(PageBreak())
        story += _card_story(card)
    if not story:
        story = [Paragraph("No students to show.", STYLE["cell"])]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
