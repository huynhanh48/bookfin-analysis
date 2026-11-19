"""Read-only multi-symbol divergence audit and self-contained chart report."""

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import talib


def chart(candles, event):
    """Render candlesticks, oscillator line and at most the latest evidence pair."""
    n = len(candles)

    def x(i):
        return 40 + i * 920 / max(1, n - 1)

    parts = [
        '<svg viewBox="0 0 1000 350" role="img" aria-label="Price candlesticks and matched indicator swings">'
    ]

    def scale(values, top, height):
        finite = [v for v in values if np.isfinite(v)]
        lo, hi = min(finite), max(finite)
        return lambda v: top + height * (hi - v) / max(hi - lo, 1e-9)

    y = scale([c[k] for c in candles for k in ("high", "low")], 20, 190)
    for i, c in enumerate(candles):
        color = "#34d399" if c["close"] >= c["open"] else "#fb7185"
        parts.append(
            f'<path d="M{x(i):.2f},{y(c["high"]):.2f}V{y(c["low"]):.2f}" stroke="{color}"/>'
        )
        parts.append(
            f'<rect x="{x(i) - 1.4:.2f}" y="{min(y(c["open"]), y(c["close"])):.2f}" width="2.8" height="{max(1, abs(y(c["open"]) - y(c["close"]))):.2f}" fill="{color}"/>'
        )
    close = np.array([c["close"] for c in candles], dtype=float)
    osc_name = event["oscillators"][0]["indicator"] if event else "RSI"
    values = talib.RSI(close, 14) if osc_name == "RSI" else talib.MACD(close, 12, 26, 9)[0]
    oy = scale(values, 250, 75)
    points = " ".join(f"{x(i):.2f},{oy(v):.2f}" for i, v in enumerate(values) if np.isfinite(v))
    parts.append(f'<polyline points="{points}" fill="none" stroke="#38bdf8" stroke-width="1.3"/>')
    parts.append(f'<text x="40" y="242" fill="#94a3b8">{osc_name} — independent line swings</text>')
    if event:
        color = "#34d399" if event["direction"] == "bullish" else "#fb7185"
        a, b = event["start_index"], event["end_index"]
        o = event["oscillators"][0]
        parts.append(
            f'<path d="M{x(a)},{y(event["start_price"])}L{x(b)},{y(event["end_price"])}" stroke="{color}" stroke-width="3"/>'
        )
        parts.append(
            f'<path d="M{x(o["start_index"])},{oy(o["start_value"])}L{x(o["end_index"])},{oy(o["end_value"])}" stroke="{color}" stroke-width="3"/>'
        )
        ci = next(i for i, c in enumerate(candles) if c["time"] == event["confirmed_at"])
        parts.append(
            f'<path d="M{x(ci)},10V335" stroke="#fbbf24" stroke-dasharray="4 4"/><text x="{min(x(ci), 800)}" y="16" fill="#fbbf24">Confirmation bar</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def run():
    """Capture API evidence and verify raw slopes/time conditions without tuning models."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--symbols", default="DPG,FPT,HPG,SSI,VCB,MWG")
    args = parser.parse_args()
    records = []
    cards = []
    with httpx.Client(base_url=args.base_url, timeout=90) as client:
        for symbol in args.symbols.split(","):
            response = client.get(f"/api/v1/indicators/{symbol}/divergence", params={"limit": 500})
            if response.status_code != 200:
                records.append({"symbol": symbol, "status": response.status_code})
                cards.append(
                    f"<section><h2>{html.escape(symbol)}</h2><p>No dataset available (HTTP {response.status_code}); not classified as no divergence.</p></section>"
                )
                continue
            data = response.json()
            series = data["series"][0]
            events = series["events"]
            candles = data["candles"]
            close = np.array([c["close"] for c in candles], dtype=float)
            rsi = talib.RSI(close, 14)
            macd = talib.MACD(close, 12, 26, 9)[0]
            checks = []
            for e in events:
                direction = 1 if e["direction"] == "bearish" else -1
                assert (e["end_price"] - e["start_price"]) * direction > 0
                assert (
                    e["start_price"]
                    == candles[e["start_index"]]["high" if direction == 1 else "low"]
                )
                assert (
                    e["end_price"] == candles[e["end_index"]]["high" if direction == 1 else "low"]
                )
                confirm = next(i for i, c in enumerate(candles) if c["time"] == e["confirmed_at"])
                assert confirm == e["end_index"] + 8
                for o in e["oscillators"]:
                    values = rsi if o["indicator"] == "RSI" else macd
                    assert np.isclose(values[o["start_index"]], o["start_value"])
                    assert np.isclose(values[o["end_index"]], o["end_value"])
                    assert (o["end_value"] - o["start_value"]) * direction < 0
                    assert (
                        abs(o["start_index"] - e["start_index"]) <= 3
                        and abs(o["end_index"] - e["end_index"]) <= 3
                    )
                checks.append(e["id"])
            records.append(
                {
                    "symbol": symbol,
                    "status": 200,
                    "count": len(candles),
                    "checked_event_ids": checks,
                    "data": data,
                }
            )
            latest = events[-1] if events else None
            evidence = (
                html.escape(json.dumps(latest, ensure_ascii=False, indent=2))
                if latest
                else "No qualifying historical pair in this window."
            )
            cards.append(
                f"<section><h2>{symbol} — {len(candles)} bars · {len(events)} historical events</h2><p>Latest confirmed historical pair only. Not a claim of a current trading signal.</p>{chart(candles, latest)}<details><summary>Exact pivot values, timestamps and sources</summary><pre>{evidence}</pre></details><p>Audit: {len(checks)} events passed raw slope, source-value, tolerance and confirmation checks.</p></section>"
            )
            print(symbol, len(candles), "bars", len(events), "events", len(checks), "checked")
    timestamp = datetime.now(UTC).isoformat()
    report = (
        """<!doctype html><html lang="vi"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Divergence review</title><style>body{background:#0b1220;color:#e2e8f0;font:15px system-ui;max-width:1200px;margin:30px auto;padding:16px}section{background:#111c30;border:1px solid #334155;border-radius:12px;padding:20px;margin:20px 0}svg{width:100%;background:#0b1220}p{color:#94a3b8}pre{white-space:pre-wrap;font-size:12px}summary{cursor:pointer;color:#38bdf8}</style><h1>Đối chiếu phân kỳ trên dữ liệu thực</h1><p>Fixed default parameters, no per-symbol tuning. Price: candlestick swings. Oscillator: independent line swings. Gold dashed marker: confirmation. Line beneath price is the evidence oscillator, not another price divergence.</p><p>Historical chart review and deterministic consistency checks — NOT measured trading accuracy or a labeled precision/recall benchmark.</p>"""
        + f"<p>Captured UTC: {timestamp}</p>"
        + "".join(cards)
        + "</html>"
    )
    Path("deliverables/divergence_review/results.json").write_text(
        json.dumps({"captured_at": timestamp, "results": records}, ensure_ascii=False)
    )
    Path("deliverables/divergence_review/index.html").write_text(report)
    Path("fe/public/divergence-review.html").write_text(report)


if __name__ == "__main__":
    run()
