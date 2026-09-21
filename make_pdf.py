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


stocks = sorted(data["stocks"], key=lambda s: (order.get(sig(s), 3), s["symbol"]))

header = ["Symbol", "LTP", "Point", "VHIGH", "VLOW", "Signal", "Short T", "Long T", "SL"]
rows = [header]
for s in stocks:
    rows.append([
        s["symbol"], s["ltp"], s["poc"], s["vah"], s["val"], sig(s),
        s["short_target"], s["long_target"], s["stop_loss"],
    ])

styles = getSampleStyleSheet()
doc = SimpleDocTemplate("Screener_report.pdf", pagesize=landscape(A4),
                        leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)

table = Table(rows, repeatRows=1)
style = [
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTSIZE", (0, 0), (-1, -1), 9),
    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")]),
]
for i, s in enumerate(stocks, start=1):
    c = {"BUY": "#15803d", "SELL": "#b91c1c", "WATCH": "#b45309"}.get(sig(s))
    if c:
        style.append(("TEXTCOLOR", (5, i), (5, i), colors.HexColor(c)))
table.setStyle(TableStyle(style))

doc.build([
    Paragraph("Scopevision Screener", styles["Title"]),
    Paragraph("Last updated: " + data["last_updated"][:16].replace("T", " ") + " IST", styles["Normal"]),
    Spacer(1, 12),
    table,
])
print("PDF ready")
