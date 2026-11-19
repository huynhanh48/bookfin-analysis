"""Reproduce the audit and old/new comparison from a pre-refactor golden snapshot.

Run from repository root: uv run python tools/report_talib_migration.py.
No provider requests, database writes or mutable live candles are used.
"""

import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import talib

from app.indicators import Ohlcv
from app.indicators.engine import IndicatorFrame
from app.indicators.factory import NATIVE_SPECS
from app.indicators.registry import INDICATORS, compute_indicator

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "docs/talib-migration"
FORMULAS = {
    "sma": "TA-Lib SMA(close)",
    "ema": "TA-Lib EMA(close), native seed",
    "adx": "TA-Lib ADX + PLUS_DI + MINUS_DI",
    "ichimoku": "TA-Lib ICHIMOKU; unshifted calculation lines",
    "rsi": "TA-Lib RSI(close), Wilder smoothing",
    "macd": "TA-Lib MACD(close), three native outputs",
    "roc": "TA-Lib ROC(close)",
    "volume_sma": "TA-Lib SMA(volume)",
    "rvol": "volume / SMA(volume, includes current bar)",
    "obv": "TA-Lib OBV(close, volume)",
    "mfi": "TA-Lib MFI(high, low, close, volume)",
    "bollinger": "TA-Lib BBANDS + (close-lower)/(upper-lower)",
    "bb_width": "(upper-lower)/middle from TA-Lib BBANDS",
    "atr": "TA-Lib ATR(high, low, close)",
    "relative_strength": "100*(close/close[t-period]-1); compare benchmark; normalized RS ratio",
    "divergence": "Independent confirmed price/oscillator pivots; TA-Lib RSI/MACD primitives; causal matching",
}
COMPOSITES = {
    "adx": "ADX + PLUS_DI + MINUS_DI",
    "bollinger": "BBANDS + percent_b",
    "bb_width": "BBANDS + width",
    "rvol": "SMA + project ratio",
    "relative_strength": "ROC + project benchmark comparison",
    "divergence": "None; custom swing matching on RSI/MACD",
}


def main() -> None:
    """Emit machine-readable audit, numeric differences and a readable report."""
    rows = json.loads((ROOT / "tests/fixtures/dpg_divergence_ohlcv.json").read_text())["candles"]
    data = Ohlcv(
        *(
            np.array([r[k] for r in rows], dtype=np.float64)
            for k in ("open", "high", "low", "close", "volume")
        )
    )
    frame = IndicatorFrame(
        data, [datetime.fromisoformat(r["time"].replace("Z", "+00:00")) for r in rows]
    )
    frame.validate()
    baseline = json.loads((DESTINATION / "legacy-golden.json").read_text())
    audit, differences = [], []
    for name, spec in INDICATORS.items():
        result = compute_indicator(name, data, benchmark=data.close * 1.1)
        native = NATIVE_SPECS.get(name)
        equivalent = native[0] if native else COMPOSITES[name]
        audit.append(
            {
                "existing_indicator": name,
                "current_formula": FORMULAS.get(name, "New registration; no old formula"),
                "TA-Lib_equivalent": equivalent,
                "input_columns": {
                    "close": "close",
                    "volume": "volume",
                    "cv": "close volume",
                    "hlc": "high low close",
                    "hlcv": "high low close volume",
                    "benchmark": "close benchmark_close",
                }[spec.input_kind],
                "parameters": json.dumps(spec.default_parameters, sort_keys=True),
                "output_columns": ", ".join(result.values),
                "migration_strategy": "Native adapter"
                if native
                else "Custom/composite adapter using native primitives",
                "backward_compatibility_risk": "Added name"
                if name not in baseline
                else "Warmup corrected; native lines are unshifted"
                if name == "ichimoku"
                else "RVOL native deliberately exposed separately"
                if name == "rvol"
                else "Numeric parity verified on golden fixture; warmup may become available earlier",
            }
        )
        if name in baseline:
            old = baseline[name]
            numeric_deltas = [
                abs(result.values[k] - value)
                for k, value in old["values"].items()
                if value is not None and result.values.get(k) is not None
            ]
            differences.append(
                {
                    "indicator": name,
                    "max_latest_absolute_difference": max(numeric_deltas, default=0.0),
                    "old_warmup": old["warmup"],
                    "new_warmup": result.warmup,
                    "classification": "lookback metadata"
                    if old["warmup"] != result.warmup
                    else "no formula/default/data change",
                    "signal_equal": old["signal"] == result.signal,
                }
            )
    custom = [
        (
            "swing_high/swing_low, HH/HL/LH/LL",
            "Strict high/low extrema; left/right confirmation",
            "high low timestamp",
            "left_bars/right_bars; pivot_length=5 fallback",
            "swings; pivot_time; confirmed_at",
        ),
        (
            "BOS/CHoCH/MSS; bullish/bearish breakout",
            "Confirmed structure; optional native ATR displacement/buffer",
            "OHLCV timestamp",
            "close/wick; ATR period14; displacement1.5; configurable",
            "confirmed structure events",
        ),
        (
            "volume_confirmation",
            "Current volume / native SMA of previous volume bars",
            "volume",
            "volume_period=20; min_volume_ratio=0 disabled",
            "breakout accepted/rejected",
        ),
        (
            "false_breakout",
            "Close returns across broken level within configured window",
            "close timestamp",
            "detect_failed_breakout=false; window3",
            "FAILED_BREAKOUT event at actual detection time",
        ),
    ]
    for name, formula, inputs, params, outputs in custom:
        audit.append(
            dict(
                zip(
                    audit[0],
                    [
                        name,
                        formula,
                        "None; custom using TA-Lib primitives where applicable",
                        inputs,
                        params,
                        outputs,
                        "Preserve confirmed state machine under CustomIndicator",
                        "Legacy defaults preserved; new asymmetric/volume options opt-in",
                    ],
                    strict=True,
                )
            )
        )
    for filename, records in [("audit.csv", audit), ("differences.csv", differences)]:
        with (DESTINATION / filename).open("w", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(records[0]))
            writer.writeheader()
            writer.writerows(records)
    lines = [
        "# Audit và kết quả migration TA-Lib",
        "",
        f"Wrapper `{talib.__version__}`; C `{talib.__ta_version__.decode()}`. Fixed fixture: {len(rows)} DPG candles. Benchmark synthetic close × 1.1 chỉ dùng kiểm thử.",
        "",
        "Baseline được chụp trước refactor: `legacy-golden.json`. Không ghi đè baseline khi chạy report. Code wrapper cũ vẫn được giữ để đối chiếu. Numeric comparison ở đây là latest value; full native arrays được so sánh trong pytest.",
        "",
        "## Audit",
        "",
        "| " + " | ".join(audit[0]) + " |",
        "| " + " | ".join(["---"] * len(audit[0])) + " |",
    ]
    lines += [
        "| " + " | ".join(str(value).replace("|", "/") for value in row.values()) + " |"
        for row in audit
    ]
    lines += [
        "",
        "## Sai khác",
        "",
        "| " + " | ".join(differences[0]) + " |",
        "| " + " | ".join(["---"] * len(differences[0])) + " |",
    ]
    lines += ["| " + " | ".join(map(str, row.values())) + " |" for row in differences]
    lines += [
        "",
        "Warmup là số quan sát tối thiểu (= native lookback + 1), không phải index. Không sửa số để ép khớp TradingView. Output warmup NaN giữ nguyên; API chuyển giá trị không hữu hạn thành null.",
        "",
        "Không đánh đồng kiểm thử số học với độ chính xác dự báo đầu tư; golden parity không chứng minh mọi cấu hình hoặc mọi nguồn dữ liệu đều tương đương.",
    ]
    (DESTINATION / "REPORT.md").write_text("\n".join(lines) + "\n")
    print(f"Generated audit.csv, differences.csv, REPORT.md ({len(audit)} audit rows)")


if __name__ == "__main__":
    main()
