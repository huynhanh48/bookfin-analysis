"""Export ten distinct API-confirmed divergence cases as reproducible PNG/text fixtures."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import talib
from PIL import Image, ImageDraw, ImageFont

OUTPUT = Path("deliverables/rag_divergence_10")
SYMBOLS = [
    "SSI",
    "KDH",
    "FPT",
    "HPG",
    "MWG",
    "VCB",
    "CTG",
    "BID",
    "TCB",
    "MBB",
    "ACB",
    "VPB",
    "STB",
    "VND",
    "VCI",
    "HCM",
    "DPG",
    "DIG",
    "DXG",
    "PDR",
    "NVL",
    "VHM",
    "VIC",
    "VRE",
    "GAS",
    "PLX",
    "POW",
    "VNM",
    "MSN",
    "SAB",
    "GEX",
    "DGC",
    "HSG",
    "NKG",
    "FRT",
    "PNJ",
    "REE",
]


def render(symbol, candles, event, path):
    """Draw one matched price/oscillator pair using the recorded independent swing indices."""
    osc = event["oscillators"][0]
    end = next(i for i, c in enumerate(candles) if c["time"] == event["confirmed_at"])
    start = max(0, min(event["start_index"], osc["start_index"]) - 35)
    close = np.array([c["close"] for c in candles], dtype=np.float64)
    values = talib.RSI(close, 14) if osc["indicator"] == "RSI" else talib.MACD(close, 12, 26, 9)[0]
    assert np.isclose(values[osc["start_index"]], osc["start_value"])
    assert np.isclose(values[osc["end_index"]], osc["end_value"])
    sign = 1 if event["direction"] == "bearish" else -1
    assert (event["end_price"] - event["start_price"]) * sign > 0
    assert (osc["end_value"] - osc["start_value"]) * sign < 0
    key = "high" if sign == 1 else "low"
    for prefix in ("start", "end"):
        assert event[prefix + "_price"] == candles[event[prefix + "_index"]][key]
        assert osc[prefix + "_index"] <= end
    image = Image.new("RGB", (1600, 1050), "#0b1220")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 21)
    title = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
    draw.text(
        (35, 15),
        f"{symbol} | D1 | {event['direction'].upper()} {osc['indicator']} divergence",
        font=title,
        fill="white",
    )
    draw.text(
        (35, 58),
        f"FCData / Bookfin engine | confirmed {event['confirmed_at'][:10]} | historical test case",
        font=font,
        fill="#b5c4d8",
    )

    def x(i):
        return 70 + (i - start) * 1350 / max(1, end - start)

    def panel(vals, top, height, name):
        finite = [v for v in vals if np.isfinite(v)]
        lo, hi = min(finite), max(finite)
        span = max(hi - lo, 1e-8)
        lo, hi = lo - span * 0.12, hi + span * 0.12

        def y(v):
            return top + height * (hi - v) / (hi - lo)

        for value in np.linspace(lo, hi, 5):
            draw.line((60, y(value), 1430, y(value)), fill="#263246")
            draw.text((1440, y(value) - 12), f"{value:,.2f}", font=font, fill="#a5b6ce")
        draw.text((65, top + 5), name, font=font, fill="#b5c4d8")
        return y

    py = panel(
        [c[k] for c in candles[start : end + 1] for k in ("high", "low")], 115, 440, "PRICE (VND)"
    )
    width = min(10, 900 / max(1, end - start))
    for i in range(start, end + 1):
        c = candles[i]
        color = "#32d399" if c["close"] >= c["open"] else "#fb7185"
        draw.line((x(i), py(c["high"]), x(i), py(c["low"])), fill=color, width=2)
        draw.rectangle(
            (
                x(i) - width / 2,
                min(py(c["open"]), py(c["close"])),
                x(i) + width / 2,
                max(py(c["open"]), py(c["close"])),
            ),
            fill=color,
        )
    oy = panel(
        values[start : end + 1],
        625,
        270,
        "RSI(14)" if osc["indicator"] == "RSI" else "MACD line (12,26,9)",
    )
    points = [(x(i), oy(values[i])) for i in range(start, end + 1) if np.isfinite(values[i])]
    draw.line(points, fill="#38bdf8", width=3)
    for y, a, b, va, vb in [
        (py, event["start_index"], event["end_index"], event["start_price"], event["end_price"]),
        (oy, osc["start_index"], osc["end_index"], osc["start_value"], osc["end_value"]),
    ]:
        draw.line((x(a), y(va), x(b), y(vb)), fill="#fbbf24", width=5)
        for idx, val in [(a, va), (b, vb)]:
            draw.ellipse((x(idx) - 5, y(val) - 5, x(idx) + 5, y(val) + 5), fill="#fbbf24")
            draw.text((max(65, x(idx) - 80), y(val) - 30), f"{val:,.2f}", font=font, fill="#fbbf24")
    for i in np.linspace(start, end, 6, dtype=int):
        draw.text((max(40, x(i) - 50), 920), candles[i]["time"][:10], font=font, fill="#b5c4d8")
    draw.text(
        (35, 975),
        "One divergence pair: price swings + independent oscillator swings. Dates shown in UTC.",
        font=font,
        fill="#b5c4d8",
    )
    image.save(path)
    return candles[: end + 1]


def main():
    """Read existing market API without changing market data or calling AI providers."""
    OUTPUT.mkdir(parents=True, exist_ok=True)
    records = []
    skipped = []
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=90) as client:
        for symbol in SYMBOLS:
            try:
                response = client.get(
                    f"/api/v1/indicators/{symbol}/divergence", params={"limit": 500}
                )
                response.raise_for_status()
                data = response.json()
                events = data["series"][0]["events"]
                if not events:
                    skipped.append({"symbol": symbol, "reason": "no qualifying event"})
                    continue
                event = max(events, key=lambda e: e["confirmed_at"])
                folder = OUTPUT / symbol
                folder.mkdir(exist_ok=True)
                candles = render(symbol, data["candles"], event, folder / f"{symbol}.png")
                oscillator = event["oscillators"][0]
                confirmed = datetime.fromisoformat(event["confirmed_at"].replace("Z", "+00:00"))
                analyzed = confirmed + timedelta(hours=15, minutes=30)
                direction = "dương" if event["direction"] == "bullish" else "âm"
                thesis = f"{symbol} có phân kỳ {direction} trên đường {oscillator['indicator']}, khung D1. Giá tạo {'đáy thấp hơn' if direction == 'dương' else 'đỉnh cao hơn'}, trong khi chỉ báo tạo {'đáy cao hơn' if direction == 'dương' else 'đỉnh thấp hơn'}."
                metadata = {
                    "symbol": symbol,
                    "timeframe": "1D",
                    "analyzed_at": analyzed.isoformat(),
                    "thesis": thesis,
                    "source": "FCData / Bookfin divergence dataset",
                    "price_basis": "RAW",
                }
                (folder / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2)
                )
                evidence = {
                    "label_origin": "Bookfin algorithm, not independently human annotated",
                    "event": event,
                    "api_as_of": data["as_of"],
                    "series_parameters": {
                        k: v for k, v in data["series"][0].items() if k not in ("events", "values")
                    },
                    "candles": candles,
                    "ta_lib_version": talib.__version__,
                }
                (folder / "evidence.json").write_text(
                    json.dumps(evidence, ensure_ascii=False, indent=2)
                )
                (folder / f"{symbol}.txt").write_text(
                    thesis
                    + "\n\nAnalysis timestamp: "
                    + analyzed.isoformat()
                    + "\nGiá: "
                    + str(event["start_price"])
                    + " → "
                    + str(event["end_price"])
                    + "\nChỉ báo: "
                    + str(oscillator["start_value"])
                    + " → "
                    + str(oscillator["end_value"])
                    + "\nConfirmed at (engine): "
                    + event["confirmed_at"]
                    + "\nNguồn: FCData qua API Bookfin; nhãn do thuật toán, cần review độc lập.\nẢnh chỉ hiển thị đến nến xác nhận; đây là case lịch sử, không khẳng định tín hiệu hiện tại.\n"
                )
                records.append(metadata)
                print(symbol, event["label"], event["confirmed_at"], flush=True)
                if len(records) == 10:
                    break
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": type(exc).__name__})
                print(symbol, type(exc).__name__, flush=True)
    (OUTPUT / "manifest.json").write_text(
        json.dumps({"cases": records, "skipped": skipped}, ensure_ascii=False, indent=2)
    )
    (OUTPUT / "README.md").write_text(
        "# Bộ case phân kỳ lịch sử\n\nMỗi folder có PNG, thesis TXT, metadata dùng ingest và evidence JSON chứa OHLCV + swing points.\nNhãn do bộ phát hiện hiện tại tạo; đã đối chiếu slope và giá trị oscillator bằng TA-Lib. Không dùng làm ground truth độc lập để tuyên bố accuracy của cùng detector.\nMỗi ảnh có một cặp phân kỳ: một đoạn trên giá và một đoạn trên oscillator.\nThời gian trục X và confirmed_at là UTC; analyzed_at đặt sau giờ đóng nến xác nhận của thị trường Việt Nam.\nKhông bao gồm outcome hoặc nến tương lai sau xác nhận.\n"
    )
    assert len(records) == 10, f"Only {len(records)} qualifying symbols available"


if __name__ == "__main__":
    main()
