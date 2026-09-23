import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()
small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.5, leading=9)
small_white = ParagraphStyle("small_white", parent=small, textColor=colors.white)

story = [
    Paragraph("Pre-market Index Options", styles["Title"]),
    Paragraph("Generated: " + data["generated_at"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 10),
]

action_color = {"CALL": "#15803d", "PUT": "#b91c1c", "WATCH": "#b45309"}


def cell(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


def p(text, style=small):
    return Paragraph(str(text), style)


for idx in data["indexes"]:
    story.append(Paragraph(idx["name"], styles["Heading2"]))
    if idx.get("error"):
        story.append(Paragraph(idx["error"], styles["Normal"]))
        story.append(Spacer(1, 12))
        continue

    pd_ = idx["prev_day"]
    story.append(Paragraph(
        f"Gai kali ({pd_['date']}): Low {pd_['low']} — High {pd_['high']} — Close {pd_['close']}",
        styles["Normal"]))

    cs, ts = idx.get("close_signal"), idx.get("today_signal")
    if cs:
        c = action_color.get(cs["action"], "#000000")
        story.append(Paragraph(
            f"Gai kali close nearest: {cs['label']} @ {cs['level_price']} — "
            f'<font color="{c}"><b>{cs["action"]}</b></font>', styles["Normal"]))
    if ts:
        c = action_color.get(ts["action"], "#000000")
        story.append(Paragraph(
            f"Aaje bhav ({idx.get('ltp')}) nearest: {ts['label']} @ {ts['level_price']} — "
            f'<font color="{c}"><b>{ts["action"]}</b></font>', styles["Normal"]))

    if idx.get("expiry"):
        story.append(Paragraph(f"Expiry: {idx['expiry']}", styles["Normal"]))
    if idx.get("is_expiry_day"):
        story.append(Paragraph(
            '<font color="#b91c1c"><b>⚠ Aaje EXPIRY DAY chhe — theta decay bahu zadpathi thashe, '
            'tight target-SL rakho.</b></font>', styles["Normal"]))

    # ---- Index's own level ladder ----
    levels = idx.get("levels") or []
    if levels:
        levels_sorted = sorted(levels, key=lambda x: x["price"], reverse=True)
        half = (len(levels_sorted) + 1) // 2
        left, right = levels_sorted[:half], levels_sorted[half:]
        rows = [[p("Zone", small_white), p("Price", small_white), p("Zone", small_white), p("Price", small_white)]]
        for i in range(max(len(left), len(right))):
            l = left[i] if i < len(left) else {"name": "", "price": ""}
            r = right[i] if i < len(right) else {"name": "", "price": ""}
            rows.append([p(l["name"]), p(l["price"]), p(r["name"]), p(r["price"])])

        lvl_table = Table(rows, colWidths=[95, 45, 95, 45], repeatRows=1)
        lvl_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(Spacer(1, 4))
        story.append(lvl_table)

    # ---- Option table ----
    opts = idx.get("options") or []
    if opts:
        header = [p(h, small_white) for h in ["Type", "Strike", "Symbol", "LTP", "Delta", "Theta", "Near Zone", "SL", "Target"]]
        rows = [header]
        for o in opts:
            if o.get("error"):
                rows.append([p(f"{o['type']} {o['moneyness']}"), p(cell(o.get("strike"))),
                             p("—"), p("—"), p("—"), p("—"), p(o["error"]), p("—"), p("—")])
                continue
            rows.append([
                p(f"{o['type']} {o['moneyness']}"), p(cell(o.get("strike"))), p(o.get("symbol", "—")),
                p(cell(o.get("ltp"))), p(cell(o.get("delta"))), p(cell(o.get("theta"))),
                p(f"{o.get('near_name') or '—'} @ {cell(o.get('near_price'))}"),
                p(cell(o.get("sl"))), p(cell(o.get("target"))),
            ])

        opt_table = Table(rows, colWidths=[45, 40, 95, 40, 35, 35, 100, 40, 40], repeatRows=1)
        opt_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(Spacer(1, 6))
        story.append(opt_table)
    else:
        story.append(Paragraph(idx.get("option_error", "Options na malya"), styles["Normal"]))

    story.append(Spacer(1, 16))

story.append(Paragraph(
    "Note: Delta/Theta live Greeks chhe, thoda modu/agad hoi shake. Aa filter/suggestion chhe, "
    "kharidva-vechvani guarantee ke salah nathi.", styles["Normal"]))

doc = SimpleDocTemplate("Index_Alert.pdf", pagesize=landscape(A4),
                        leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
doc.build(story)
print("PDF ready")
