"""Project registry factories bridge native series and backward-compatible API names."""

from typing import Any

import numpy as np

from app.indicators.engine import (
    CustomIndicator,
    IndicatorBatchResult,
    IndicatorFrame,
    TalibIndicator,
)

# Explicit project defaults differ intentionally from upstream MA default 30.
NATIVE_SPECS = {
    "sma": ("SMA", {"period": "timeperiod"}, ("sma",)),
    "ema": ("EMA", {"period": "timeperiod"}, ("ema",)),
    "wma": ("WMA", {"period": "timeperiod"}, ("wma",)),
    "ma": ("MA", {"period": "timeperiod", "matype": "matype"}, ("ma",)),
    "rsi": ("RSI", {"period": "timeperiod"}, ("rsi",)),
    "macd": (
        "MACD",
        {"fast_period": "fastperiod", "slow_period": "slowperiod", "signal_period": "signalperiod"},
        ("macd", "signal_line", "histogram"),
    ),
    "roc": ("ROC", {"period": "timeperiod"}, ("roc",)),
    "atr": ("ATR", {"period": "timeperiod"}, ("atr",)),
    "obv": ("OBV", {}, ("obv",)),
    "mfi": ("MFI", {"period": "timeperiod"}, ("mfi",)),
    "volume_sma": ("SMA", {"period": "timeperiod"}, ("volume_sma",)),
    "ichimoku": (
        "ICHIMOKU",
        {
            "tenkan_period": "tenkanperiod",
            "kijun_period": "kijunperiod",
            "senkou_b_period": "senkoubperiod",
        },
        ("tenkan", "kijun", "senkou_a", "senkou_b"),
    ),
    "plus_di": ("PLUS_DI", {"period": "timeperiod"}, ("plus_di",)),
    "minus_di": ("MINUS_DI", {"period": "timeperiod"}, ("minus_di",)),
    "ad": ("AD", {}, ("ad",)),
    "adosc": ("ADOSC", {"fast_period": "fastperiod", "slow_period": "slowperiod"}, ("adosc",)),
    "rvol_native": ("RVOL", {"period": "timeperiod"}, ("rvol",)),
    "supertrend": (
        "SUPERTREND",
        {"period": "timeperiod", "multiplier": "multiplier"},
        ("supertrend", "trend"),
    ),
}


class IchimokuIndicator(CustomIndicator):
    """TA-Lib Ichimoku with the cloud shifted to the current price bar.

    TA-Lib returns Senkou Span A/B at the calculation date. A price chart
    normally plots those spans forward by Kijun periods, so comparing today's
    close with today's raw spans is look-ahead/alignment bias. Keep raw values
    for transparency and expose ``cloud_a``/``cloud_b`` at the price bar.
    """

    name = "ichimoku"

    def __init__(self, parameters: dict[str, Any]):
        self.parameters = dict(parameters)

    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        kijun = int(self.parameters["kijun_period"])
        native = TalibIndicator(
            "ichimoku",
            "ICHIMOKU",
            {
                "tenkanperiod": self.parameters["tenkan_period"],
                "kijunperiod": kijun,
                "senkoubperiod": self.parameters["senkou_b_period"],
            },
            ("tenkan", "kijun", "senkou_a", "senkou_b"),
        ).calculate(frame)
        series = dict(native.series)
        for source, target in (("senkou_a", "cloud_a"), ("senkou_b", "cloud_b")):
            shifted = np.full(len(native.series[source]), np.nan, dtype=np.float64)
            if len(shifted) > kijun:
                shifted[kijun:] = native.series[source][:-kijun]
            series[target] = shifted
        return IndicatorBatchResult(
            "ichimoku",
            dict(self.parameters),
            series,
            frame.timeframe,
            frame.timestamps,
            native.lookback + kijun,
            events=native.events,
        )


class CompositeIndicator(CustomIndicator):
    """Compose native calculations then derive project-specific columns without zero fill."""

    def __init__(self, name: str, parameters: dict[str, Any]):
        self.name, self.parameters = name, parameters

    def calculate(self, frame: IndicatorFrame) -> IndicatorBatchResult:
        """Evaluate ADX triple, band derivatives or legacy inclusive-average RVOL."""
        self.validate(frame)
        p = self.parameters
        if self.name == "adx":
            results = [
                TalibIndicator(key, fn, {"timeperiod": p["period"]}, [key]).calculate(frame)
                for key, fn in [("adx", "ADX"), ("plus_di", "PLUS_DI"), ("minus_di", "MINUS_DI")]
            ]
            values = {k: v for r in results for k, v in r.series.items()}
            lookback = max(r.lookback for r in results)
        elif self.name in ("bollinger", "bb_width"):
            result = TalibIndicator(
                self.name,
                "BBANDS",
                {
                    "timeperiod": p["period"],
                    "nbdevup": p["stddev"],
                    "nbdevdn": p["stddev"],
                    "matype": 0,
                },
                ["upper", "middle", "lower"],
            ).calculate(frame)
            upper, middle, lower = (result.series[k] for k in ("upper", "middle", "lower"))
            if self.name == "bollinger":
                pct = np.full(len(upper), np.nan)
                np.divide(
                    frame.ohlcv.close - lower, upper - lower, out=pct, where=(upper - lower) > 0
                )
                values = {**result.series, "percent_b": pct}
            else:
                width = np.full(len(upper), np.nan)
                np.divide(
                    upper - lower, middle, out=width, where=np.isfinite(middle) & (middle != 0)
                )
                values = {"bb_width": width}
            lookback = result.lookback
        else:
            result = TalibIndicator(
                "rvol", "SMA", {"timeperiod": p["period"]}, ["average"], ["volume"]
            ).calculate(frame)
            ratio = np.full(len(frame.ohlcv), np.nan)
            avg = result.series["average"]
            np.divide(frame.ohlcv.volume, avg, out=ratio, where=np.isfinite(avg) & (avg != 0))
            values = {"rvol": ratio}
            lookback = result.lookback
        return IndicatorBatchResult(
            self.name, dict(p), values, frame.timeframe, frame.timestamps, lookback
        )


def build_indicator(name: str, parameters: dict, benchmark=None):
    """Construct a native, composite or legacy-custom adapter with explicit configuration."""
    from app.indicators.custom import (
        FunctionIndicator,
        MACDDivergenceIndicator,
        RSIDivergenceIndicator,
    )
    from app.indicators.registry import _legacy_compute_indicator

    if name == "ichimoku":
        return IchimokuIndicator(parameters)
    if name in NATIVE_SPECS:
        function, mapping, outputs = NATIVE_SPECS[name]
        params = {target: parameters[key] for key, target in mapping.items()}
        return TalibIndicator(
            name, function, params, outputs, ["volume"] if name == "volume_sma" else None
        )
    if name in ("adx", "bollinger", "bb_width", "rvol"):
        return CompositeIndicator(name, parameters)
    cls = FunctionIndicator
    if name == "divergence":
        cls = (
            MACDDivergenceIndicator if parameters.get("enable_macd", 1) else RSIDivergenceIndicator
        )
    return cls(
        name,
        parameters,
        lambda data: _legacy_compute_indicator(
            name,
            data,
            parameters,
            benchmark=benchmark[: len(data)] if benchmark is not None else None,
        ),
    )


def interpret_signal(name, values, previous, close):
    """Separate project interpretation thresholds from native numeric calculations."""

    def valid(key):
        return values.get(key) is not None

    if name == "rsi" and valid("rsi"):
        return (
            "overbought"
            if values["rsi"] >= 70
            else "oversold"
            if values["rsi"] <= 30
            else "neutral"
        )
    if name == "mfi" and valid("mfi"):
        return (
            "overbought"
            if values["mfi"] >= 80
            else "oversold"
            if values["mfi"] <= 20
            else "neutral"
        )
    if name == "adx" and valid("adx"):
        return "trending" if values["adx"] >= 25 else "weak_trend"
    if name in ("rvol", "rvol_native") and valid("rvol"):
        return "above_average" if values["rvol"] >= 1.5 else "normal"
    if name == "macd" and valid("histogram") and previous.get("histogram") is not None:
        now, before = values["histogram"], previous["histogram"]
        return (
            "bullish_cross"
            if now > 0 and before <= 0
            else "bearish_cross"
            if now < 0 and before >= 0
            else "above_signal"
            if now > 0
            else "below_signal"
        )
    if name == "ichimoku":
        cloud_a = values.get("cloud_a", values.get("senkou_a"))
        cloud_b = values.get("cloud_b", values.get("senkou_b"))
        if cloud_a is not None and cloud_b is not None:
            return (
                "above_cloud"
                if close > max(cloud_a, cloud_b)
                else "below_cloud"
                if close < min(cloud_a, cloud_b)
                else "inside_cloud"
            )
    return None


def default_registry():
    """Expose all existing/native/custom factories through the project Registry contract."""
    from app.indicators.engine import IndicatorRegistry
    from app.indicators.registry import INDICATORS

    registry = IndicatorRegistry()
    for name, spec in INDICATORS.items():

        def make(*, _name=name, _defaults=spec.default_parameters, **params):
            return build_indicator(_name, {**_defaults, **params})

        registry.register(name, make)
    from app.indicators.custom import BreakoutIndicator, MarketStructureIndicator

    registry.register("market_structure", MarketStructureIndicator)
    registry.register("breakout", BreakoutIndicator)
    return registry
