import json
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

with open("results.json") as f:
    data = json.load(f)

order = {"WATCH": 0, "BUY": 1, "SELL": 2}


def sig(s):
    return s["signal"].split()[0]


stocks = sorted(data["stocks"], key=lambda s: (order.get(sig(s), 3), s["symbol"]))

wb = Workbook()
ws = wb.active
ws.title = "Scopevision Screener"

market_text = ""
m = data.get("market")
if m:
    market_text = f"Nifty 50: {m['trend']} (close {m['close']} vs 200 DMA {m['dma200']})"

# Row 1: title, Row 2: last updated, Row 3: market, Row 4: table header
ws.append(["Scopevision Screener"])
ws.append(["Last updated: " + data["last_updated"][:16].replace("T", " ") + " IST"])
ws.append([market_text])

header = ["Symbol", "LTP", "Point", "VHIGH", "VLOW", "Signal", "Short T", "Long T", "SL",
          "Trend", "Vol x", "R:R", "Liquidity", "Avg value (Cr)", "Corp action",
          "From 52W high %", "From 52W low %", "Qty"]
idx = {h: i for i, h in enumerate(header)}
ws.append(header)

for s in stocks:
    ws.append([
        s["symbol"], s["ltp"], s["poc"], s["vah"], s["val"], sig(s),
        s.get("short_target"), s.get("long_target"), s.get("stop_loss"),
        s.get("trend"), s.get("vol_ratio"), s.get("rr"), s.get("liquidity"),
        s.get("avg_value_cr"), "Check" if s.get("corp_action") else "",
        s.get("from_high_pct"), s.get("from_low_pct"), s.get("qty"),
    ])

last_col = get_column_letter(len(header))
ws.merge_cells(f"A1:{last_col}1")
ws["A1"].font = Font(size=14, bold=True)
ws["A1"].alignment = Alignment(horizontal="left")
ws["A2"].font = Font(italic=True, color="6B7280")
ws["A3"].font = Font(bold=True)

head_row = 4
head_fill = PatternFill("solid", fgColor="1F2937")
for cell in ws[head_row]:
    cell.font = Font(bold=True, color="FFFFFF")
    cell.fill = head_fill
    cell.alignment = Alignment(horizontal="center", wrap_text=True)

sig_colors = {"BUY": "15803D", "SELL": "B91C1C", "WATCH": "B45309"}
trend_colors = {"Up": "15803D", "Down": "B91C1C"}
for row in ws.iter_rows(min_row=head_row + 1):
    c = sig_colors.get(row[idx["Signal"]].value)
    if c:
        row[idx["Signal"]].font = Font(bold=True, color=c)
    c = trend_colors.get(row[idx["Trend"]].value)
    if c:
        row[idx["Trend"]].font = Font(bold=True, color=c)
    if row[idx["Liquidity"]].value == "Low":
        row[idx["Liquidity"]].font = Font(bold=True, color="B45309")
    if row[idx["Corp action"]].value:
        row[idx["Corp action"]].font = Font(bold=True, color="B91C1C")

for i in range(1, len(header) + 1):
    ws.column_dimensions[get_column_letter(i)].width = 16 if i == 1 else 12

ws.freeze_panes = f"A{head_row + 1}"
ws.auto_filter.ref = f"A{head_row}:{last_col}{ws.max_row}"

wb.save("Screener_report.xlsx")
print("Excel ready")
