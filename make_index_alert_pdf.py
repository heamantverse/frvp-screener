import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()
story = [
    Paragraph("Pre-market Index Options", styles["Title"]),
    Paragraph("Generated: " + data["generated_at"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 10),
]

action_color = {"CALL": "#15803d", "PUT": "#b91c1c", "WATCH": "#b45309"}


def cell(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


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

    opts = idx.get("options") or []
    if opts:
        header = ["Type", "Strike", "Symbol", "LTP", "Delta", "Theta", "Near Zone", "SL", "Target"]
        rows = [header]
        for o in opts:
            if o.get("error"):
                rows.append([f"{o['type']} {o['moneyness']}", cell(o.get("strike")), "—", "—", "—", "—", o["error"], "—", "—"])
                continue
            rows.append([
                f"{o['type']} {o['moneyness']}", cell(o.get("strike")), o.get("symbol", "—"),
                cell(o.get("ltp")), cell(o.get("delta")), cell(o.get("theta")),
                f"{o.get('near_name') or '—'} @ {cell(o.get('near_price'))}",
                cell(o.get("sl")), cell(o.get("target")),
            ])

        table = Table(rows, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
        ]
        table.setStyle(TableStyle(style))
        story.append(Spacer(1, 4))
        story.append(table)
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
