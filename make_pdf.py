import json
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

with open("results.json") as f:
    data = json.load(f)

order = {"WATCH": 0, "BUY": 1, "SELL": 2}


def sig(s):
    return s["signal"].split()[0]


def cell(v, suffix=""):
    return "" if v is None else f"{v}{suffix}"


stocks = sorted(data["stocks"], key=lambda s: (order.get(sig(s), 3), s["symbol"]))

header = ["Symbol", "LTP", "Point", "VHIGH", "VLOW", "Signal", "Short T", "Long T", "SL",
          "Trend", "Vol x", "R:R", "Liq", "Check"]
rows = [header]
for s in stocks:
    rows.append([
        s["symbol"], s["ltp"], s["poc"], s["vah"], s["val"], sig(s),
        cell(s.get("short_target")), cell(s.get("long_target")), cell(s.get("stop_loss")),
        s.get("trend") or "", cell(s.get("vol_ratio"), "x"), cell(s.get("rr")),
        s.get("liquidity") or "", "Check" if s.get("corp_action") else "",
    ])

styles = getSampleStyleSheet()
doc = SimpleDocTemplate("Screener_report.pdf", pagesize=landscape(A4),
                        leftMargin=20, rightMargin=20, topMargin=24, bottomMargin=24)

table = Table(rows, repeatRows=1)
style = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
]
sig_colors = {"BUY": "#15803d", "SELL": "#b91c1c", "WATCH": "#b45309"}
trend_colors = {"Up": "#15803d", "Down": "#b91c1c"}
for i, s in enumerate(stocks, start=1):
    c = sig_colors.get(sig(s))
    if c:
        style.append(("TEXTCOLOR", (5, i), (5, i), colors.HexColor(c)))
    c = trend_colors.get(s.get("trend"))
    if c:
        style.append(("TEXTCOLOR", (9, i), (9, i), colors.HexColor(c)))
    if s.get("liquidity") == "Low":
        style.append(("TEXTCOLOR", (12, i), (12, i), colors.HexColor("#b45309")))
    if s.get("corp_action"):
        style.append(("TEXTCOLOR", (13, i), (13, i), colors.HexColor("#b91c1c")))
table.setStyle(TableStyle(style))

story = [
    Paragraph("Scopevision Screener", styles["Title"]),
    Paragraph("Last updated: " + data["last_updated"][:16].replace("T", " ") + " IST", styles["Normal"]),
]
m = data.get("market")
if m:
    story.append(Paragraph(
        f"Nifty 50: {m['trend']} (close {m['close']} vs 200 DMA {m['dma200']})", styles["Normal"]))
story += [
    Spacer(1, 10),
    table,
    Spacer(1, 8),
    Paragraph(
        "Trend: price vs 50/200 day average. Vol x: latest volume vs 20-day average. "
        "R:R: reward to risk (short target vs SL). Liq: Low = avg daily traded value below Rs 5 Cr. "
        "Check: large one-day gap (possible split/bonus), verify the levels.",
        styles["Normal"]),
]
doc.build(story)
print("PDF ready")
