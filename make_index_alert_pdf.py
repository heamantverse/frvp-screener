import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()
small_white = ParagraphStyle("small_white", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white)
small_normal = ParagraphStyle("small_normal", parent=styles["Normal"], fontSize=8, leading=10)
plan_style = ParagraphStyle("plan", parent=styles["Normal"], fontSize=9, leading=12)
sugg_style = ParagraphStyle("sugg", parent=styles["Normal"], fontSize=9, leading=12,
                            textColor=colors.HexColor("#7c2d12"))

CALL_C, PUT_C = "#15803d", "#b91c1c"


def p(text, style=None):
    return Paragraph(str(text), style or small_normal)


def cell(val):
    return "-" if val is None else str(val)


def target_cell(targets, i):
    if targets and i < len(targets):
        return cell(targets[i])
    return "-"


def plan_lines(plan):
    if not plan:
        return []
    c = CALL_C if plan["side"] == "CALL" else PUT_C
    out = [Paragraph(f'<font color="{c}"><b>{plan["side"]}</b></font>: {plan["trigger"]}', plan_style)]
    tg = "  |  ".join(f"T{i + 1} {t['price']} ({t['name']})" for i, t in enumerate(plan["targets"])) or "-"
    sl = f"{plan['sl']['price']} ({plan['sl']['name']})" if plan.get("sl") else "-"
    out.append(Paragraph(f"&nbsp;&nbsp;&nbsp;Entry <b>{plan['entry']}</b>  |  {tg}  |  SL <b>{sl}</b>", plan_style))
    return out


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
        f"VAL {idx['low']} - VAH {idx['high']}   |   Aaje bhav: {idx['price']}", styles["Normal"]))
    story.append(Paragraph(f"<b>Best Entry (Day Low):</b> {idx['best_entry']}", styles["Normal"]))
    story.append(Spacer(1, 4))

    plans = idx.get("plans") or {}
    story += plan_lines(plans.get("CALL"))
    story += plan_lines(plans.get("PUT"))
    if idx.get("suggestion"):
        story.append(Spacer(1, 3))
        story.append(Paragraph("Suggestion: " + idx["suggestion"], sugg_style))

    opts = idx.get("options") or []
    if opts:
        header = [p(h, small_white) for h in
                  ["Type", "Strike", "LTP", "Delta", "Theta", "Best Entry", "Entry", "T1", "T2", "T3", "SL", "Status"]]
        rows = [header]
        for o in opts:
            if o.get("error"):
                rows.append([p(o["type"]), p(cell(o.get("strike"))), p("-"), p("-"), p("-"),
                             p("-"), p("-"), p("-"), p("-"), p("-"), p("-"), p(o["error"])])
                continue
            tg = o.get("targets")
            rows.append([
                p(o["type"]), p(cell(o.get("strike"))), p(cell(o.get("ltp"))),
                p(cell(o.get("delta"))), p(cell(o.get("theta"))),
                p(f"<b>{cell(o.get('best_entry'))}</b>"),
                p(f"<b>{cell(o.get('entry'))}</b>"),
                p(target_cell(tg, 0)), p(target_cell(tg, 1)), p(target_cell(tg, 2)),
                p(cell(o.get("sl"))),
                p(o.get("status", "-")),
            ])
        table = Table(rows, colWidths=[34, 46, 46, 44, 44, 58, 52, 50, 50, 50, 50, 190], repeatRows=1)
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
    "Option Entry/Target/SL Delta thi anumaan chhe; theta ane IV pan asar kare chhe.",
    styles["Normal"]))

doc = SimpleDocTemplate("Index_Alert.pdf", pagesize=landscape(A4), leftMargin=30, rightMargin=30,
                        topMargin=30, bottomMargin=30)
doc.build(story)
print("PDF ready")