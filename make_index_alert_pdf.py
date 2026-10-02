import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()

small_white = ParagraphStyle("small_white", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white)
small_normal = ParagraphStyle("small_normal", parent=styles["Normal"], fontSize=8, leading=10)

ACTION_COLOR = {
    "CALL": colors.HexColor("#15803d"),
    "PUT": colors.HexColor("#b91c1c"),
    "WATCH": colors.HexColor("#b45309"),
}


def p(text, style=None):
    return Paragraph(str(text), style or small_normal)


def cell(val):
    return "-" if val is None else str(val)


def status_cell(status):
    s = status or "-"
    if s.startswith("TRADE"):
        c = "#15803d"
    elif s.startswith("SKIP"):
        c = "#b91c1c"
    else:
        c = "#b45309"
    return p(f'<font color="{c}"><b>{s}</b></font>')


story = [
    Paragraph("Pre-market Index Alert", styles["Title"]),
    Paragraph("Generated: " + data["generated_at"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 12),
]

for idx in data["indexes"]:
    story.append(Paragraph(idx["name"], styles["Heading2"]))
    if idx.get("error"):
        story.append(Paragraph(idx["error"], styles["Normal"]))
        story.append(Spacer(1, 10))
        continue

    story.append(Paragraph(
        f"VAL {idx['low']} - VAH {idx['high']}   |   Aaje bhav: {idx['price']}",
        styles["Normal"]))

    bias = idx.get("bias")
    c = ACTION_COLOR.get(bias, colors.black)
    story.append(Paragraph(f'<font color="{c.hexval()}"><b>{bias}</b></font>', styles["Normal"]))
    story.append(Paragraph(f"<b>Best Entry:</b> {idx['best_entry']}", styles["Normal"]))
    if idx.get("target") is not None:
        story.append(Paragraph(f"Index Target: {idx['target']}   Index SL: {idx['sl']}", styles["Normal"]))
    else:
        story.append(Paragraph("Confirmation ni wait karo (stall zone)", styles["Normal"]))

    opts = idx.get("options") or []
    if opts:
        header = [p(h, small_white) for h in
                  ["Type", "Strike", "LTP", "Delta", "Theta", "Best Entry", "Target", "SL", "Status"]]
        rows = [header]
        for o in opts:
            if o.get("error"):
                rows.append([p(o["type"]), p(cell(o.get("strike"))), p("-"), p("-"), p("-"),
                             p("-"), p("-"), p("-"), p(o["error"])])
                continue
            rows.append([
                p(o["type"]), p(cell(o.get("strike"))), p(cell(o.get("ltp"))),
                p(cell(o.get("delta"))), p(cell(o.get("theta"))),
                p(f"<b>{cell(o.get('best_entry'))}</b>"),
                p(cell(o.get("target"))), p(cell(o.get("sl"))),
                status_cell(o.get("status")),
            ])
        table = Table(rows, colWidths=[36, 44, 42, 42, 42, 56, 52, 46, 120], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(Spacer(1, 6))
        story.append(table)
    elif idx.get("option_error"):
        story.append(Paragraph(f"Option: {idx['option_error']}", styles["Normal"]))

    story.append(Spacer(1, 14))

story.append(Paragraph(
    "Note: Aa filter/suggestion chhe, kharidva-vechvani guarantee ke salah nathi. "
    "Option premium ni chaal delta/theta par pan depend kare chhe, faqat levels par nahi.",
    styles["Normal"]))

doc = SimpleDocTemplate("Index_Alert.pdf", pagesize=A4, leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
doc.build(story)
print("PDF ready")