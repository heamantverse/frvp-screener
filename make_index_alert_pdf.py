import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("index_alert.json") as f:
    data = json.load(f)

styles = getSampleStyleSheet()

# ---- Helper styles/functions used by the options table ----
small_white = ParagraphStyle(
    "small_white",
    parent=styles["Normal"],
    fontSize=8,
    leading=10,
    textColor=colors.white,
)

small_normal = ParagraphStyle(
    "small_normal",
    parent=styles["Normal"],
    fontSize=8,
    leading=10,
)

zone_style = ParagraphStyle(
    "zone_style",
    parent=styles["Normal"],
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#7c2d12"),
)


def p(text, style=None):
    """Wrap text in a Paragraph, default to small_normal style."""
    return Paragraph(str(text), style or small_normal)


def cell(val):
    """Format a table cell value, showing em-dash for missing data."""
    return "—" if val is None else str(val)


def zone_lines(zone):
    """zone = {"notes": [...], "predictions": [...]} -> list of Paragraphs for PDF body."""
    if not zone or not zone.get("notes"):
        return []
    out = []
    for note, pred in zip(zone["notes"], zone["predictions"]):
        out.append(Paragraph(f"⚡ <b>{note}</b> — {pred}", zone_style))
    return out


def zone_cell_text(zone):
    """zone -> compact string for table cell (Notes / Prediction columns)."""
    if not zone or not zone.get("notes"):
        return "—", "—"
    notes = "; ".join(zone["notes"])
    preds = "; ".join(zone["predictions"])
    return notes, preds


story = [
    Paragraph("Pre-market Index Alert", styles["Title"]),
    Paragraph("Generated: " + data["generated_at"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 12),
]

action_color = {"CALL": colors.HexColor("#15803d"), "PUT": colors.HexColor("#b91c1c"), "WATCH": colors.HexColor("#b45309")}


def sig_block(title, sig):
    if not sig:
        return [Paragraph(f"{title}: data na malyu", styles["Normal"])]
    action = sig.get("action")
    c = action_color.get(action, colors.black)
    lines = [
        Paragraph(f"<b>{title}</b>: nearest {sig.get('ratio')} ({sig.get('label')}) @ {sig.get('level_price')}", styles["Normal"]),
        Paragraph(f'<font color="{c.hexval()}"><b>{action}</b></font>', styles["Normal"]),
    ]
    sl = sig.get("sl")
    tp = sig.get("tp")
    if sl is not None:
        lines.append(Paragraph(f"Index SL: {sl}   Index Target: {tp}", styles["Normal"]))
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

        # ---- Zone analysis (pattern-based notes + prediction) for index ----
        zlines = zone_lines(idx.get("today_zone"))
        if zlines:
            story.append(Spacer(1, 4))
            story.append(Paragraph("Zone Analysis:", styles["Normal"]))
            story += zlines

    # ---- Option table ----
    opts = idx.get("options") or []
    if opts:
        header = [p(h, small_white) for h in
                   ["Type", "Strike", "Symbol", "LTP", "Delta", "Theta", "Near Zone", "Zone Notes", "Prediction"]]
        rows = [header]
        for o in opts:
            if o.get("error"):
                rows.append([p(f"{o['type']} {o['moneyness']}"), p(cell(o.get("strike"))),
                             p("—"), p("—"), p("—"), p("—"), p(o["error"]), p("—"), p("—")])
                continue
            notes_txt, pred_txt = zone_cell_text({
                "notes": o.get("zone_notes") or [],
                "predictions": o.get("zone_prediction") or [],
            })
            rows.append([
                p(f"{o['type']} {o['moneyness']}"), p(cell(o.get("strike"))), p(o.get("symbol", "—")),
                p(cell(o.get("ltp"))), p(cell(o.get("delta"))), p(cell(o.get("theta"))),
                p(f"{o.get('near_name') or '—'} @ {cell(o.get('near_price'))}"),
                p(notes_txt), p(pred_txt),
            ])

        opt_table = Table(rows, colWidths=[38, 32, 80, 28, 28, 28, 90, 80, 95], repeatRows=1)
        opt_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(Spacer(1, 6))
        story.append(opt_table)
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
