import os
import json
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)


# ============================================================
# CONFIG
# ============================================================

PDF_FILE = "Fib_Open_Alert.pdf"
RESULT_FILE = "fib_alert.json"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

VAH = float(os.environ["FIB_VAH"])
POC = float(os.environ["FIB_POC"])
VAL = float(os.environ["FIB_VAL"])
OPENING = float(os.environ["FIB_OPEN"])

TOLERANCE_PCT = 0.20

FIB_LEVELS = [
    (1.618, "GOLDEN REVERSAL / Extension"),
    (1.272, "STALL ZONE FOR BREAKOUTS"),
    (1.000, "BASE MOVE"),
    (0.786, "Pre-breakout stall"),
    (0.618, "GOLDEN REVERSAL"),
    (0.500, "Mid / Balance"),
    (0.382, "Stronger reaction zone"),
    (0.236, "REVERSAL"),
    (0.000, "BASE MOVE / Demand"),
    (-0.272, "Day Low/High zone"),
    (-1.618, "IMPULSIVE TARGET"),
]


# ============================================================
# FIB CALCULATION
# ============================================================

def calculate_fib_levels(val, vah):
    rng = vah - val

    if rng <= 0:
        raise ValueError("VAH must be greater than VAL")

    result = []

    for ratio, name in FIB_LEVELS:
        price = round(val + ratio * rng, 2)

        result.append({
            "ratio": ratio,
            "name": name,
            "price": price,
        })

    return result


def get_level(fib_levels, ratio):
    for level in fib_levels:
        if abs(level["ratio"] - ratio) < 0.001:
            return level

    return None


# ============================================================
# ANALYSIS
# ============================================================

def analyze(opening, fib_levels, poc):
    tol = opening * (TOLERANCE_PCT / 100)

    lvl_0236 = get_level(fib_levels, 0.236)
    lvl_0618 = get_level(fib_levels, 0.618)
    lvl_1000 = get_level(fib_levels, 1.000)
    lvl_1272 = get_level(fib_levels, 1.272)
    lvl_1618 = get_level(fib_levels, 1.618)

    notes = []
    predictions = []

    # --------------------------------------------------------
    # BIAS
    # --------------------------------------------------------

    if opening > poc:
        bias = "Bullish"
    elif opening < poc:
        bias = "Bearish"
    else:
        bias = "Neutral"

    # --------------------------------------------------------
    # 0.236 STRENGTH
    # --------------------------------------------------------

    if lvl_0236 and opening > lvl_0236["price"]:
        notes.append("Strength: Open above 0.236")

        predictions.append(
            "Price is holding above the 0.236 Fib level."
        )

    # --------------------------------------------------------
    # 0.618 GOLDEN REVERSAL
    # --------------------------------------------------------

    if lvl_0618 and abs(opening - lvl_0618["price"]) <= tol:
        notes.append(
            "Open near 0.618 Golden Reversal"
        )

        predictions.append(
            "Possible reaction / pause near the 0.618 level."
        )

    # --------------------------------------------------------
    # 1.272 STALL ZONE
    # --------------------------------------------------------

    if lvl_1272 and abs(opening - lvl_1272["price"]) <= tol:
        notes.append(
            "Open near 1.272 Stall Zone"
        )

        predictions.append(
            "Decision zone. Breakout may continue; rejection may cause pullback."
        )

    # --------------------------------------------------------
    # 1.618 EXTENSION
    # --------------------------------------------------------

    if lvl_1618 and abs(opening - lvl_1618["price"]) <= tol:
        notes.append(
            "Open near 1.618 Extension"
        )

        predictions.append(
            "Extension / exhaustion zone. Watch for reaction or reversal."
        )

    # --------------------------------------------------------
    # 1.000 BASE MOVE
    # --------------------------------------------------------

    if lvl_1000 and abs(opening - lvl_1000["price"]) <= tol:
        notes.append(
            "Open near 1.000 Base Move"
        )

        predictions.append(
            "Base-move area. Directional reaction may develop."
        )

    # --------------------------------------------------------
    # DEFAULT
    # --------------------------------------------------------

    if not notes:
        notes.append(
            "No major Fib confluence at open"
        )

        predictions.append(
            "Wait for clearer reaction at key Fib levels."
        )

    return {
        "bias": bias,
        "notes": notes,
        "prediction": predictions,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"


def send_telegram_message(text):
    response = requests.post(
        telegram_url("sendMessage"),
        json={
            "chat_id": CHAT_ID,
            "text": text,
        },
        timeout=30,
    )

    response.raise_for_status()


def send_pdf():
    with open(PDF_FILE, "rb") as f:
        response = requests.post(
            telegram_url("sendDocument"),
            data={
                "chat_id": CHAT_ID,
                "caption": "📊 Fib Open Alert PDF",
            },
            files={
                "document": (
                    PDF_FILE,
                    f,
                    "application/pdf",
                )
            },
            timeout=60,
        )

    response.raise_for_status()


# ============================================================
# PDF
# ============================================================

def generate_pdf(fib_levels, analysis):
    styles = getSampleStyleSheet()

    doc = SimpleDocTemplate(
        PDF_FILE,
        pagesize=landscape(A4),
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25,
    )

    story = []

    title = Paragraph(
        "<b>📊 Intraday Fib Open Alert</b>",
        styles["Title"],
    )

    story.append(title)
    story.append(Spacer(1, 10))

    generated = datetime.now().strftime(
        "%d-%b-%Y %H:%M:%S"
    )

    summary_data = [
        ["VAH", "POC", "VAL", "OPEN", "BIAS"],
        [
            f"{VAH:.2f}",
            f"{POC:.2f}",
            f"{VAL:.2f}",
            f"{OPENING:.2f}",
            analysis["bias"],
        ],
    ]

    summary_table = Table(summary_data, colWidths=[100] * 5)

    summary_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f3f4f6")),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ])
    )

    story.append(summary_table)
    story.append(Spacer(1, 15))

    story.append(
        Paragraph(
            f"<b>Generated:</b> {generated}",
            styles["Normal"],
        )
    )

    story.append(Spacer(1, 10))

    # --------------------------------------------------------
    # ANALYSIS
    # --------------------------------------------------------

    story.append(
        Paragraph(
            "<b>Analysis</b>",
            styles["Heading2"],
        )
    )

    for note in analysis["notes"]:
        story.append(
            Paragraph(
                f"• {note}",
                styles["Normal"],
            )
        )

    story.append(Spacer(1, 6))

    story.append(
        Paragraph(
            "<b>Prediction</b>",
            styles["Heading2"],
        )
    )

    for prediction in analysis["prediction"]:
        story.append(
            Paragraph(
                f"• {prediction}",
                styles["Normal"],
            )
        )

    story.append(Spacer(1, 15))

    # --------------------------------------------------------
    # FIB TABLE
    # --------------------------------------------------------

    fib_data = [
        ["Ratio", "Fib Level", "Price"]
    ]

    for level in fib_levels:
        fib_data.append([
            f"{level['ratio']:.3f}",
            level["name"],
            f"{level['price']:.2f}",
        ])

    fib_table = Table(
        fib_data,
        colWidths=[70, 300, 100],
        repeatRows=1,
    )

    fib_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )

    story.append(fib_table)

    doc.build(story)


# ============================================================
# JSON
# ============================================================

def save_json(fib_levels, analysis):
    data = {
        "generated_at": datetime.now().isoformat(),
        "input": {
            "vah": VAH,
            "poc": POC,
            "val": VAL,
            "opening": OPENING,
        },
        "analysis": analysis,
        "fib_levels": fib_levels,
    }

    with open(
        RESULT_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False,
        )


# ============================================================
# MAIN
# ============================================================

def main():
    print("==============================================")
    print("FIB ANALYSIS STARTED")
    print("==============================================")

    print(f"VAH    : {VAH}")
    print(f"POC    : {POC}")
    print(f"VAL    : {VAL}")
    print(f"OPEN   : {OPENING}")

    fib_levels = calculate_fib_levels(
        VAL,
        VAH,
    )

    analysis = analyze(
        OPENING,
        fib_levels,
        POC,
    )

    print(f"Bias: {analysis['bias']}")

    save_json(
        fib_levels,
        analysis,
    )

    generate_pdf(
        fib_levels,
        analysis,
    )

    # --------------------------------------------------------
    # TELEGRAM TEXT
    # --------------------------------------------------------

    lines = [
        "📊 Fib Open Alert",
        "",
        f"VAH: {VAH:.2f}",
        f"POC: {POC:.2f}",
        f"VAL: {VAL:.2f}",
        f"OPEN: {OPENING:.2f}",
        "",
        f"Bias: {analysis['bias']}",
        "",
        "Notes:",
    ]

    for note in analysis["notes"]:
        lines.append(f"• {note}")

    lines.append("")
    lines.append("Prediction:")

    for prediction in analysis["prediction"]:
        lines.append(f"• {prediction}")

    send_telegram_message("\n".join(lines))

    send_pdf()

    print("PDF sent to Telegram")
    print("==============================================")


if __name__ == "__main__":
    main()
