import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()
story = [
    Paragraph("Pre-market Index Alert", styles["Title"]),
    Paragraph("Generated: " + data["generated_at"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 12),
]

action_color = {"CALL": colors.HexColor("#15803d"), "PUT": colors.HexColor("#b91c1c"), "WATCH": colors.HexColor("#b45309")}


def sig_block(title, sig):
    if not sig:
        return [Paragraph(f"{title}: data na malyu", styles["Normal"])]
    c = action_color.get(sig["action"], colors.black)
    lines = [
        Paragraph(f"<b>{title}</b>: nearest {sig['ratio']} ({sig['label']}) @ {sig['level_price']}", styles["Normal"]),
        Paragraph(f'<font color="{c.hexval()}"><b>{sig["action"]}</b></font>', styles["Normal"]),
    ]
    if sig["sl"] is not None:
        lines.append(Paragraph(f"Index SL: {sig['sl']}   Index Target: {sig['tp']}", styles["Normal"]))
    else:
        lines.append(Paragraph("Confirmation ni wait karo (stall zone)", styles["Normal"]))
    return lines


for idx in data["indexes"]:
    story.append(Paragraph(idx["name"], styles["Heading2"]))
    if idx.get("error"):
        story.append(Paragraph(idx["error"], styles["Normal"]))
        story.append(Spacer(1, 10))
        continue

    pd_ = idx["prev_day"]
    story.append(Paragraph(f"Gai kali ({pd_['date']}): Low {pd_['low']} — High {pd_['high']} — Close {pd_['close']}", styles["Normal"]))
    story += sig_block("Gai kali close", idx["close_signal"])
    if idx.get("ltp"):
        story.append(Spacer(1, 4))
        story += sig_block(f"Aaje bhav ({idx['ltp']})", idx["today_signal"])

    opt = idx.get("option")
    if opt:
        story.append(Spacer(1, 6))
        story.append(Paragraph(f"<b>Suggested option: {opt['symbol']}</b> ({opt['type']}, strike {opt['strike']}, expiry {opt['expiry']})", styles["Normal"]))
        lv = opt.get("levels")
        st = opt.get("sl_target")
        if lv:
            story.append(Paragraph(f"Option LTP: {lv['ltp']}   POC: {lv['poc']}   VAH: {lv['vah']}   VAL: {lv['val']}", styles["Normal"]))
        if st:
            story.append(Paragraph(f"<b>Option SL: {st['sl']}   Option Target: {st['target']}</b>", styles["Normal"]))
        else:
            story.append(Paragraph("Option na potana levels na mali shakya (nano data hoi shake)", styles["Normal"]))
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
