"""Strategy layer that enriches base indicator rows with explicit semantics.

The existing numeric wrappers remain the source of values. Each strategy owns one
indicator family and only adds/repairs the assessment fields: directional
status, non-directional state, signal phase, conflicts and warnings. This keeps
the legacy ``IndicatorOutput``/``signal`` contract intact while giving the agent
and UI a richer, testable interpretation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.schemas.agent import IndicatorAssessment


@dataclass(frozen=True)
class AssessmentContext:
    """Evidence needed by every strategy, already bounded to confirmed candles."""

    market: dict[str, Any]
    indicators: dict[str, Any]
    structure: dict[str, Any]
    divergence: list[dict[str, Any]]


def _decimal(value: Any, digits: int = 1) -> str:
    if value is None:
        return "—"
    formatted = f"{float(value):,.{digits}f}"
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


def _indicator(indicators: dict[str, Any], name: str, **params: Any) -> dict[str, Any]:
    for item in indicators.values():
        if item.get("indicator") != name:
            continue
        item_params = item.get("parameters") or {}
        if all(item_params.get(key) == value for key, value in params.items()):
            return item
    return {}


def _value(item: dict[str, Any], key: str) -> Any:
    return (item.get("values") or {}).get(key)


def _previous(item: dict[str, Any], key: str) -> Any:
    return (item.get("previous_values") or {}).get(key)


def _direction(current: Any, previous: Any) -> str:
    if current is None or previous is None:
        return "unknown"
    return "rising" if current > previous else "falling" if current < previous else "flat"


class BaseAssessmentStrategy(ABC):
    """One indicator family; subclasses never calculate new TA-Lib values."""

    key: str

    @abstractmethod
    def enrich(
        self, row: IndicatorAssessment, context: AssessmentContext
    ) -> IndicatorAssessment: ...

    def _with(
        self,
        row: IndicatorAssessment,
        *,
        status: str | None = None,
        note: str | None = None,
        **kwargs: Any,
    ) -> IndicatorAssessment:
        updates: dict[str, Any] = {**kwargs}
        if status is not None:
            updates["status"] = status
        if note is not None:
            updates["note"] = note
        return row.model_copy(update=updates)


class MaStrategy(BaseAssessmentStrategy):
    key = "ma"

    def enrich(self, row, context):
        close = context.market.get("close")
        sma20 = _indicator(context.indicators, "sma", period=20)
        sma50 = _indicator(context.indicators, "sma", period=50)
        ma20, ma50 = _value(sma20, "sma"), _value(sma50, "sma")
        previous20, previous50 = _previous(sma20, "sma"), _previous(sma50, "sma")
        state = {
            "close_vs_ma20": "above" if close and ma20 and close > ma20 else "below",
            "close_vs_ma50": "above" if close and ma50 and close > ma50 else "below",
            "ma_alignment": (
                "bullish"
                if ma20 is not None and ma50 is not None and ma20 > ma50
                else "bearish"
                if ma20 is not None and ma50 is not None and ma20 < ma50
                else "unknown"
            ),
            "ma20_slope": _direction(ma20, previous20),
            "ma50_slope": _direction(ma50, previous50),
        }
        if close and ma20:
            state["distance_to_ma20_pct"] = round((close / ma20 - 1) * 100, 2)
        if close and ma50:
            state["distance_to_ma50_pct"] = round((close / ma50 - 1) * 100, 2)
        cross = "none"
        if (
            ma20 is not None
            and ma50 is not None
            and previous20 is not None
            and previous50 is not None
        ):
            if ma20 > ma50 and previous20 <= previous50:
                cross = "golden_cross"
            elif ma20 < ma50 and previous20 >= previous50:
                cross = "death_cross"
        state["crossover_event"] = cross
        evidence = [row.note] if row.note else []
        return self._with(
            row,
            state=state,
            evidence=evidence,
            signal_phase="indicator_confirmed" if cross != "none" else None,
            confirmation_level="indicator" if cross != "none" else None,
        )


class MacdStrategy(BaseAssessmentStrategy):
    key = "macd"

    def enrich(self, row, context):
        macd = _indicator(context.indicators, "macd")
        line, signal = _value(macd, "macd"), _value(macd, "signal_line")
        histogram = _value(macd, "histogram")
        previous_line = _previous(macd, "macd")
        previous_signal = _previous(macd, "signal_line")
        previous_histogram = _previous(macd, "histogram")
        zero_zone = (
            "positive" if line and line > 0 else "negative" if line and line < 0 else "boundary"
        )
        relation = (
            "above"
            if line is not None and signal is not None and line > signal
            else "below"
            if line is not None and signal is not None and line < signal
            else "equal"
        )
        histogram_direction = _direction(histogram, previous_histogram)
        if histogram is not None and histogram > 0:
            momentum = (
                "bullish_strengthening"
                if histogram_direction == "rising"
                else "bullish_weakening"
                if histogram_direction == "falling"
                else "stable"
            )
        elif histogram is not None and histogram < 0:
            momentum = (
                "bearish_weakening"
                if histogram_direction == "rising"
                else "bearish_strengthening"
                if histogram_direction == "falling"
                else "stable"
            )
        else:
            momentum = "stable"
        cross = "none"
        if line is not None and signal is not None:
            if previous_line is not None and previous_signal is not None:
                if line > signal and previous_line <= previous_signal:
                    cross = "bullish_signal_cross"
                elif line < signal and previous_line >= previous_signal:
                    cross = "bearish_signal_cross"
            if previous_line is not None and line > 0 >= previous_line:
                cross = "zero_cross_up"
            elif previous_line is not None and line < 0 <= previous_line:
                cross = "zero_cross_down"
        if line is not None and line > 0 and relative_above(line, signal):
            status = "positive"
        elif line is not None and line < 0 and relative_below(line, signal):
            status = "negative"
        else:
            status = "neutral"
        if status == "neutral":
            if histogram is not None and histogram > 0:
                direction_text = (
                    "đang mở rộng" if histogram_direction == "rising" else "đang thu hẹp"
                )
                note = (
                    f"MACD dưới 0; động lượng +{_decimal(histogram)} {direction_text} — "
                    "phục hồi sớm, chưa xác nhận xu hướng."
                )
            elif histogram is not None and histogram < 0:
                direction_text = (
                    "đang mở rộng" if histogram_direction == "falling" else "đang thu hẹp"
                )
                note = (
                    f"MACD dưới 0; động lượng {_decimal(histogram)} {direction_text} — "
                    "áp lực giảm chưa được xác nhận đảo chiều."
                )
            else:
                note = "MACD quanh đường 0; chưa có tín hiệu rõ."
        elif status == "positive":
            note = "MACD trên 0 và trên đường tín hiệu — động lượng tăng."
        else:
            note = "MACD dưới 0 và dưới đường tín hiệu — động lượng giảm."
        conflicts = []
        if line is not None and line < 0 < (histogram or 0):
            conflicts.append("MACD dưới 0 nhưng histogram dương: phục hồi chưa được xác nhận.")
        return self._with(
            row,
            status=status,
            note=note,
            state={
                "zero_zone": zero_zone,
                "signal_relation": relation,
                "histogram_direction": histogram_direction,
                "momentum_state": momentum,
                "cross_event": cross,
                "bars_since_cross": 1 if cross != "none" else None,
            },
            evidence=[f"histogram={_decimal(histogram)}", f"MACD={_decimal(line)}"],
            conflicts=conflicts,
            signal_phase="early" if cross != "none" else None,
            confirmation_level="early" if cross != "none" else None,
        )


def relative_above(left: float, right: float | None) -> bool:
    return right is not None and left > right


def relative_below(left: float, right: float | None) -> bool:
    return right is not None and left < right


class RsiStrategy(BaseAssessmentStrategy):
    key = "rsi"

    def enrich(self, row, context):
        rsi = _indicator(context.indicators, "rsi")
        value = _value(rsi, "rsi")
        previous = _previous(rsi, "rsi")
        zone = (
            "oversold"
            if value is not None and value <= 30
            else "weak"
            if value is not None and value < 50
            else "strong"
            if value is not None and value < 70
            else "overbought"
            if value is not None
            else "unknown"
        )
        direction = _direction(value, previous)
        note = (
            f"RSI {_decimal(value)} — dưới 30: quá bán."
            if zone == "oversold"
            else f"RSI {_decimal(value)} — trên 70: quá mua."
            if zone == "overbought"
            else f"RSI {_decimal(value)} — vùng 50–70: tích cực nhưng chưa quá mua."
            if zone == "strong"
            else f"RSI {_decimal(value)} — vùng 30–50: còn yếu."
        )
        return self._with(
            row,
            status="neutral",
            note=note,
            state={"rsi_zone": zone, "momentum_direction": direction},
            evidence=[f"RSI={_decimal(value)}"],
            warnings=["RSI không phải tín hiệu mua/bán độc lập."],
        )


class AdxStrategy(BaseAssessmentStrategy):
    key = "adx"

    def enrich(self, row, context):
        adx = _indicator(context.indicators, "adx")
        value = _value(adx, "adx")
        previous = _previous(adx, "adx")
        plus = _value(_indicator(context.indicators, "plus_di"), "plus_di")
        minus = _value(_indicator(context.indicators, "minus_di"), "minus_di")
        strength = (
            "weak"
            if value is not None and value < 20
            else "transition"
            if value is not None and value < 25
            else "strong"
            if value is not None
            else "unknown"
        )
        direction = (
            "bullish"
            if plus is not None and minus is not None and plus > minus
            else "bearish"
            if plus is not None and minus is not None and minus > plus
            else "neutral"
        )
        status = (
            "neutral"
            if strength != "strong"
            else "positive"
            if direction == "bullish"
            else "negative"
            if direction == "bearish"
            else "neutral"
        )
        strength_text = (
            "dưới 20: xu hướng yếu"
            if strength == "weak"
            else "trong vùng chuyển tiếp 20–25"
            if strength == "transition"
            else "từ 25 trở lên: xu hướng có độ mạnh đáng chú ý"
        )
        note = (
            f"ADX {_decimal(value)} {strength_text}; "
            f"{'+DI > -DI' if direction == 'bullish' else '-DI > +DI' if direction == 'bearish' else 'DI chưa phân định'}."
        )
        return self._with(
            row,
            status=status,
            note=note,
            state={
                "trend_strength": strength,
                "directional_bias": direction,
                "adx_slope": _direction(value, previous),
                "di_spread": round(abs(plus - minus), 2)
                if plus is not None and minus is not None
                else None,
            },
            evidence=[f"ADX={_decimal(value)}", f"+DI={_decimal(plus)}", f"-DI={_decimal(minus)}"],
        )


class IchimokuStrategy(BaseAssessmentStrategy):
    key = "ichimoku"

    def enrich(self, row, context):
        ichimoku = _indicator(context.indicators, "ichimoku")
        close = context.market.get("close")
        a = _value(ichimoku, "cloud_a") or _value(ichimoku, "senkou_a")
        b = _value(ichimoku, "cloud_b") or _value(ichimoku, "senkou_b")
        tenkan, kijun = _value(ichimoku, "tenkan"), _value(ichimoku, "kijun")
        position = "inside_cloud"
        if close is not None and a is not None and b is not None:
            if close > max(a, b):
                position = "above_cloud"
            elif close < min(a, b):
                position = "below_cloud"
        status = (
            "positive"
            if position == "above_cloud"
            else "negative"
            if position == "below_cloud"
            else "neutral"
        )
        note = (
            "Giá trên mây."
            if position == "above_cloud"
            else "Giá dưới mây."
            if position == "below_cloud"
            else "Giá trong mây."
        )
        return self._with(
            row,
            status=status,
            note=note,
            state={
                "price_vs_cloud": position,
                "tenkan_vs_kijun": (
                    "above"
                    if tenkan is not None and kijun is not None and tenkan > kijun
                    else "below"
                    if tenkan is not None and kijun is not None and tenkan < kijun
                    else "equal"
                ),
                "cloud_thickness": round(abs(a - b), 2)
                if a is not None and b is not None
                else None,
                "cloud_direction": (
                    "rising" if a is not None and b is not None and a > b else "falling"
                ),
            },
            evidence=[f"close_vs_cloud={position}"],
        )


class BollingerStrategy(BaseAssessmentStrategy):
    key = "bollinger"

    def enrich(self, row, context):
        bollinger = _indicator(context.indicators, "bollinger")
        percent_b = _value(bollinger, "percent_b")
        upper, middle, lower = (
            _value(bollinger, "upper"),
            _value(bollinger, "middle"),
            _value(bollinger, "lower"),
        )
        previous_upper = _previous(bollinger, "upper")
        previous_middle = _previous(bollinger, "middle")
        previous_lower = _previous(bollinger, "lower")
        bandwidth = (upper - lower) / abs(middle) if upper and lower and middle else None
        previous_bandwidth = (
            (previous_upper - previous_lower) / abs(previous_middle)
            if previous_upper and previous_lower and previous_middle
            else None
        )
        bandwidth_direction = _direction(bandwidth, previous_bandwidth)
        volatility = "squeeze" if bandwidth is not None and bandwidth < 0.05 else "normal"
        band_walk = (
            "upper"
            if percent_b is not None and percent_b >= 0.8
            else "upper_half"
            if percent_b is not None and percent_b >= 0.65
            else "lower"
            if percent_b is not None and percent_b <= 0.2
            else "lower_half"
            if percent_b is not None and percent_b <= 0.4
            else "none"
        )
        close = context.market.get("close")
        distance_lower = (
            (close - lower) / abs(close) * 100
            if close is not None and lower is not None and close
            else None
        )
        distance_upper = (
            (upper - close) / abs(close) * 100
            if close is not None and upper is not None and close
            else None
        )
        trend_text = (
            "đang thu hẹp"
            if bandwidth_direction == "falling"
            else "đang mở rộng"
            if bandwidth_direction == "rising"
            else "đi ngang"
        )
        if band_walk == "lower_half":
            note = (
                f"%B {_decimal(percent_b, 2)} — nửa dưới dải, cách dải dưới "
                f"{_decimal(distance_lower, 1)}%; dải {trend_text}."
            )
        elif band_walk == "upper_half":
            note = (
                f"%B {_decimal(percent_b, 2)} — nửa trên dải, cách dải trên "
                f"{_decimal(distance_upper, 1)}%; dải {trend_text}."
            )
        elif band_walk == "lower":
            note = f"%B {_decimal(percent_b, 2)} — bám dải dưới; dải {trend_text}."
        elif band_walk == "upper":
            note = f"%B {_decimal(percent_b, 2)} — bám dải trên; dải {trend_text}."
        else:
            note = f"%B {_decimal(percent_b, 2)} — giữa hai dải."
        return self._with(
            row,
            status="neutral",
            note=note,
            state={
                "percent_b": round(percent_b, 4) if percent_b is not None else None,
                "bandwidth": round(bandwidth, 4) if bandwidth is not None else None,
                "bandwidth_direction": bandwidth_direction,
                "volatility_state": volatility,
                "band_walk": band_walk,
                "distance_to_lower_pct": round(distance_lower, 2)
                if distance_lower is not None
                else None,
                "distance_to_upper_pct": round(distance_upper, 2)
                if distance_upper is not None
                else None,
                "directional_bias": (
                    "bullish"
                    if percent_b is not None and percent_b > 0.5
                    else "bearish"
                    if percent_b is not None and percent_b < 0.5
                    else "neutral"
                ),
            },
            evidence=[
                f"%B={_decimal(percent_b, 2)}",
                f"bandwidth_direction={bandwidth_direction}",
            ],
            warnings=["%B và bandwidth mô tả biến động/vị trí, không phải tín hiệu mua bán."],
        )


class VolumeStrategy(BaseAssessmentStrategy):
    key = "volume"

    def enrich(self, row, context):
        close, previous_close = context.market.get("close"), context.market.get("previous_close")
        volume, previous_volume = (
            context.market.get("volume"),
            context.market.get("previous_volume"),
        )
        rvol_v1 = _value(_indicator(context.indicators, "rvol"), "rvol")
        rvol_v2 = _value(_indicator(context.indicators, "rvol_v2"), "rvol")
        rvol = rvol_v2 if rvol_v2 is not None else rvol_v1
        volume_state = (
            "expansion"
            if rvol is not None and rvol >= 1.5
            else "contraction"
            if rvol is not None and rvol < 0.8
            else "normal"
        )
        volume_direction = (
            "rising"
            if volume is not None and previous_volume is not None and volume > previous_volume
            else "falling"
            if volume is not None and previous_volume is not None and volume < previous_volume
            else "flat"
        )
        price_direction = (
            "rising"
            if close is not None and previous_close is not None and close > previous_close
            else "falling"
            if close is not None and previous_close is not None and close < previous_close
            else "flat"
        )
        agreement = {
            ("rising", "rising"): "price_up_volume_up",
            ("falling", "rising"): "price_down_volume_up",
            ("rising", "falling"): "price_up_volume_down",
            ("falling", "falling"): "price_down_volume_down",
        }.get((price_direction, volume_direction), "mixed")
        note = (
            f"Khối lượng {_decimal(rvol, 2)}x TB20 — đột biến, cần xem giá đóng cửa để xác nhận."
            if volume_state == "expansion"
            else f"Khối lượng {_decimal(rvol, 2)}x TB20 — yếu so với trung bình 20 phiên."
            if volume_state == "contraction"
            else f"Khối lượng {_decimal(rvol, 2)}x TB20 — "
            + (
                "hơi trên trung bình."
                if rvol is not None and rvol >= 1.0
                else "quanh mức bình thường."
            )
        )
        conflicts = []
        if volume_state == "expansion" and price_direction == "falling":
            conflicts.append(
                "Khối lượng tăng trong phiên giá giảm: chưa thể kết luận mua chủ động."
            )
        return self._with(
            row,
            status="neutral",
            value=f"{_decimal(rvol, 2)}x TB20" if rvol is not None else None,
            note=note,
            state={
                "rvol": round(rvol, 4) if rvol is not None else None,
                "rvol_v1": round(rvol_v1, 4) if rvol_v1 is not None else None,
                "rvol_v2": round(rvol_v2, 4) if rvol_v2 is not None else None,
                "rvol_formula_delta": round(rvol_v2 - rvol_v1, 4)
                if rvol_v1 is not None and rvol_v2 is not None
                else None,
                "volume_state": volume_state,
                "volume_direction": volume_direction,
                "price_direction": price_direction,
                "price_volume_agreement": agreement,
            },
            evidence=[
                f"RVOL_v2={_decimal(rvol, 2)}",
                f"RVOL_v1={_decimal(rvol_v1, 2)}",
                f"volume_direction={volume_direction}",
            ],
            conflicts=conflicts,
            warnings=["RVOL cao/thấp không tự xác định hướng xu hướng."],
        )


class ObvStrategy(BaseAssessmentStrategy):
    key = "obv"

    def enrich(self, row, context):
        obv = _indicator(context.indicators, "obv")
        value, previous = _value(obv, "obv"), _previous(obv, "obv")
        delta = (value - previous) if value is not None and previous is not None else None
        status = (
            "positive" if delta and delta > 0 else "negative" if delta and delta < 0 else "neutral"
        )
        trend = "rising" if delta and delta > 0 else "falling" if delta and delta < 0 else "flat"
        note = (
            "OBV tăng — khối lượng tích lũy theo công thức OBV, không kết luận dòng tiền tổ chức."
            if trend == "rising"
            else "OBV giảm — khối lượng phân phối theo công thức OBV."
            if trend == "falling"
            else "OBV không đổi — chưa có tín hiệu tích lũy/phân phối."
        )
        conflicts = []
        rvol = _value(_indicator(context.indicators, "rvol_v2"), "rvol")
        if rvol is None:
            rvol = _value(_indicator(context.indicators, "rvol"), "rvol")
        if status == "positive" and rvol is not None and rvol < 0.8:
            conflicts.append("OBV tăng nhưng khối lượng dưới bình quân: chưa xác nhận dòng tiền.")
        return self._with(
            row,
            status=status,
            note=note,
            state={"obv_delta": delta, "obv_trend": trend},
            evidence=[f"obv_delta={delta}"],
            conflicts=conflicts,
        )


def _event_phrase(event: dict[str, Any]) -> str:
    """Keep the original structure symbol; append the bar date."""
    code = str(event.get("event_type") or "EVENT")
    date = _short_date(event.get("broken_at") or event.get("confirmed_at"))
    return f"{code} {date}" if date else code


class StructureStrategy(BaseAssessmentStrategy):
    key = "structure"

    def enrich(self, row, context):
        structure = context.structure
        trend = str(structure.get("confirmed_trend") or "SIDEWAYS")
        bias = str(structure.get("break_bias") or "NEUTRAL")
        status = (
            "positive" if trend == "UPTREND" else "negative" if trend == "DOWNTREND" else "neutral"
        )
        note = f"{trend}/{bias}"
        latest = (structure.get("events") or [None])[-1]
        if latest:
            note += f" · {_event_phrase(latest)}"
        note += "."
        phase = "indicator_confirmed" if latest else None
        return self._with(
            row,
            status=status,
            value=f"{trend}/{bias}",
            note=note,
            state={"trend_state": trend.lower(), "trend_bias": bias.lower()},
            evidence=[f"latest_event={latest.get('event_type')}" if latest else "no_event"],
            signal_phase=phase,
            confirmation_level="indicator" if latest else None,
        )


def _post_signal_price_confirmation(
    structure: dict[str, Any], confirmed_at: Any, direction: str
) -> bool:
    """A divergence is price-confirmed only by a later structure break in its direction."""
    if not confirmed_at:
        return False
    try:
        confirmed = datetime.fromisoformat(str(confirmed_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    bullish = {"BOS_BULLISH", "CHOCH_BULLISH", "MSS_BULLISH"}
    bearish = {"BOS_BEARISH", "CHOCH_BEARISH", "MSS_BEARISH"}
    for event in structure.get("events") or []:
        raw = event.get("broken_at") or event.get("confirmed_at")
        if not raw:
            continue
        try:
            broken = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if broken <= confirmed:
            continue
        if direction == "bullish" and event.get("event_type") in bullish:
            return True
        if direction == "bearish" and event.get("event_type") in bearish:
            return True
    return False


class DivergenceStrategy(BaseAssessmentStrategy):
    key = "divergence"

    def enrich(self, row, context):
        events = context.divergence or []
        latest = events[-1] if events else None
        if not latest:
            return self._with(
                row,
                status="neutral",
                note="Không có phân kỳ đã xác nhận.",
                state={"divergence_type": "none"},
                signal_phase=None,
            )
        direction = str(latest.get("direction") or "").lower()
        kind = str(latest.get("kind") or "regular").lower()
        trend = str(context.structure.get("confirmed_trend") or "SIDEWAYS")
        if kind == "regular" and direction == "bullish":
            status = "positive"
        elif kind == "regular" and direction == "bearish":
            status = "negative"
        elif kind == "hidden" and direction == "bullish":
            status = "positive" if trend == "UPTREND" else "neutral"
        elif kind == "hidden" and direction == "bearish":
            status = "negative" if trend == "DOWNTREND" else "neutral"
        else:
            status = "neutral"
        oscillator = ",".join(
            str(item.get("indicator")) for item in latest.get("oscillators") or []
        )
        osc_text = f" ({oscillator})" if oscillator else ""
        if kind == "hidden":
            continuation = "tích lũy/tiếp diễn tăng" if direction == "bullish" else "tiếp diễn giảm"
        else:
            continuation = (
                "tín hiệu đảo chiều tăng" if direction == "bullish" else "tín hiệu đảo chiều giảm"
            )
        note = f"{kind} {direction}{osc_text} — {continuation}; cần giá xác nhận."
        post_confirmation = _post_signal_price_confirmation(
            context.structure, latest.get("confirmed_at"), direction
        )
        phase = "price_confirmed" if post_confirmation else "indicator_confirmed"
        conflicts = []
        if kind == "hidden" and direction == "bullish" and trend != "UPTREND":
            conflicts.append(
                "Hidden bullish trong xu hướng không tăng: không phải đảo chiều đã xác nhận."
            )
        if kind == "hidden" and direction == "bearish" and trend != "DOWNTREND":
            conflicts.append(
                "Hidden bearish trong xu hướng không giảm: chưa phải tín hiệu giảm đã xác nhận."
            )
        return self._with(
            row,
            status=status,
            value=f"{kind} {direction}",
            note=note,
            state={
                "divergence_type": f"{kind}_{direction}",
                "oscillator": oscillator,
                "confirmed_at": _short_date(latest.get("confirmed_at")),
                "price_pivot_confirmed_at": _short_date(latest.get("price_confirmed_at")),
                "post_signal_price_confirmation": post_confirmation,
            },
            evidence=[f"divergence={kind}_{direction}"],
            conflicts=conflicts,
            signal_phase=phase,
            confirmation_level="price" if post_confirmation else "indicator",
        )


def _short_date(value: Any) -> str:
    if not value:
        return ""
    from app.agents.report import _short_date as report_short_date

    return report_short_date(value)


STRATEGIES: dict[str, BaseAssessmentStrategy] = {
    strategy.key: strategy
    for strategy in (
        MaStrategy(),
        MacdStrategy(),
        RsiStrategy(),
        AdxStrategy(),
        IchimokuStrategy(),
        BollingerStrategy(),
        VolumeStrategy(),
        ObvStrategy(),
        StructureStrategy(),
        DivergenceStrategy(),
    )
}


def enrich_rows(
    rows: list[IndicatorAssessment], context: AssessmentContext
) -> list[IndicatorAssessment]:
    """Apply the registry; unknown rows remain untouched for compatibility."""
    return [
        STRATEGIES[row.key].enrich(row, context) if row.key in STRATEGIES else row for row in rows
    ]
