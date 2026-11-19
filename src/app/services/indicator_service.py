"""Nghiệp vụ chỉ báo: precompute, đọc snapshot và cấu trúc thị trường.

Worker gọi `recompute` để tính trước và lưu snapshot. API gọi `get_indicators`
để đọc kết quả đã tính; chỉ khi chưa có snapshot mới tính ngay trong request và
đánh dấu `computed_on_demand=True`.
"""

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import numpy as np
import talib

from app.core.config import Settings
from app.core.exceptions import InsufficientDataError
from app.core.logging import get_logger
from app.indicators import ENGINE_VERSION, IndicatorOutput, Ohlcv
from app.indicators.custom import MarketStructureIndicator
from app.indicators.engine import FORMULA_VERSION, IndicatorFrame
from app.indicators.market_structure import (
    ENGINE_VERSION as STRUCTURE_ENGINE_VERSION,
)
from app.indicators.market_structure import (
    BreakBias,
    CandleBar,
    ConfirmedTrend,
    MarketStructureConfig,
    PivotType,
    StructureEvent,
    StructureEventType,
    StructureSnapshot,
    SwingLabel,
    SwingPoint,
)
from app.indicators.native import unstable_metadata
from app.indicators.registry import (
    DEFAULT_INDICATOR_REQUESTS,
    compute_indicator,
    get_spec,
    parameters_hash,
)
from app.indicators.swing_divergence import event_alerts
from app.models.candle import Candle
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.schemas.divergence import DivergenceEventOut
from app.schemas.indicators import (
    IndicatorPointOut,
    IndicatorResult,
    IndicatorSeriesOut,
)

logger = get_logger(__name__)

STRUCTURE_INDICATOR = "market_structure"
MIN_CANDLES = 60

# Bộ chỉ báo mặc định cho biểu đồ: đủ để thấy xu hướng, dải biến động và động lượng.
CHART_INDICATOR_REQUESTS: tuple[tuple[str, dict[str, int | float]], ...] = (
    ("sma", {"period": 20}),
    ("sma", {"period": 50}),
    ("ema", {"period": 20}),
    ("bollinger", {"period": 20, "stddev": 2.0}),
    ("rsi", {"period": 14}),
    ("macd", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("volume_sma", {"period": 20}),
    ("divergence", {}),
)


@dataclass(frozen=True, slots=True)
class IndicatorSet:
    """Tập kết quả chỉ báo của một mã.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến được tính.
        candles_used: Số nến đã dùng.
        computed_on_demand: True khi phải tính ngay trong request.
        results: Danh sách kết quả chỉ báo.
    """

    symbol: str
    timeframe: str
    as_of: datetime | None
    candles_used: int
    computed_on_demand: bool
    results: list[IndicatorResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class IndicatorSeriesSet:
    """Nến và chuỗi chỉ báo căn cùng trục thời gian.

    Attributes:
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.
        as_of: Thời điểm nến gần nhất.
        candles: Nến đã đóng, tăng dần theo thời gian.
        series: Chuỗi chỉ báo tính trên chính chuỗi nến đó.
    """

    symbol: str
    timeframe: str
    as_of: datetime | None
    candles: list[Candle]
    series: list[IndicatorSeriesOut] = field(default_factory=list)


def _swing_to_dict(swing: SwingPoint) -> dict[str, Any]:
    """Chuyển swing thành dict để lưu JSONB."""
    return {
        "kind": swing.kind.value,
        "label": swing.label.value if swing.label else None,
        "price": swing.price,
        "pivot_time": swing.pivot_time.isoformat(),
        "confirmed_at": swing.confirmed_at.isoformat(),
        "index": swing.index,
    }


def _swing_from_dict(data: dict[str, Any]) -> SwingPoint:
    """Dựng lại swing từ payload JSONB."""
    label = data.get("label")
    return SwingPoint(
        kind=PivotType(data["kind"]),
        label=SwingLabel(label) if label else None,
        price=float(data["price"]),
        pivot_time=datetime.fromisoformat(data["pivot_time"]),
        confirmed_at=datetime.fromisoformat(data["confirmed_at"]),
        index=int(data["index"]),
    )


def _event_to_dict(event: StructureEvent) -> dict[str, Any]:
    """Chuyển sự kiện cấu trúc thành dict để lưu JSONB."""
    return {
        "event_type": event.event_type.value,
        "pivot_time": event.pivot_time.isoformat(),
        "confirmed_at": event.confirmed_at.isoformat(),
        "broken_at": event.broken_at.isoformat(),
        "price": event.price,
        "break_level": event.break_level,
        "observed_price": event.observed_price,
        "bias_before": event.bias_before.value,
        "bias_after": event.bias_after.value,
        "is_displacement": event.is_displacement,
    }


def _event_from_dict(data: dict[str, Any]) -> StructureEvent:
    """Dựng lại sự kiện cấu trúc từ payload JSONB."""
    return StructureEvent(
        event_type=StructureEventType(data["event_type"]),
        pivot_time=datetime.fromisoformat(data["pivot_time"]),
        confirmed_at=datetime.fromisoformat(data["confirmed_at"]),
        broken_at=datetime.fromisoformat(data["broken_at"]),
        price=float(data["price"]),
        break_level=float(data["break_level"]),
        observed_price=float(data["observed_price"]),
        bias_before=BreakBias(data["bias_before"]),
        bias_after=BreakBias(data["bias_after"]),
        is_displacement=bool(data.get("is_displacement", False)),
    )


def structure_snapshot_to_payload(snapshot: StructureSnapshot) -> dict[str, Any]:
    """Chuyển snapshot cấu trúc thành payload JSONB.

    Args:
        snapshot: Snapshot cần lưu.

    Returns:
        Dict có thể serialize sang JSON.
    """
    return {
        "as_of": snapshot.as_of.isoformat() if snapshot.as_of else None,
        "break_bias": snapshot.break_bias.value,
        "confirmed_trend": snapshot.confirmed_trend.value,
        "last_swing_high": _swing_to_dict(snapshot.last_swing_high)
        if snapshot.last_swing_high
        else None,
        "last_swing_low": _swing_to_dict(snapshot.last_swing_low)
        if snapshot.last_swing_low
        else None,
        "protected_high": _swing_to_dict(snapshot.protected_high)
        if snapshot.protected_high
        else None,
        "protected_low": _swing_to_dict(snapshot.protected_low) if snapshot.protected_low else None,
        "swings": [_swing_to_dict(swing) for swing in snapshot.swings],
        "events": [_event_to_dict(event) for event in snapshot.events],
        "engine_version": snapshot.engine_version,
        "config_hash": snapshot.config_hash,
        "formula_version": FORMULA_VERSION,
        "ta_lib_version": talib.__version__,
        "ta_lib_c_version": talib.__ta_version__.decode(),
        "unstable_periods": unstable_metadata(),
        "candle_finality": snapshot.candle_finality,
        "data_quality": snapshot.data_quality,
    }


def structure_snapshot_from_payload(
    payload: dict[str, Any],
    *,
    symbol: str,
    timeframe: str,
) -> StructureSnapshot:
    """Dựng lại snapshot cấu trúc từ payload JSONB.

    Args:
        payload: Payload đã lưu trong `indicator_snapshots`.
        symbol: Mã chứng khoán.
        timeframe: Khung thời gian.

    Returns:
        `StructureSnapshot` tương ứng.
    """
    swings = tuple(_swing_from_dict(item) for item in payload.get("swings", []))
    events = tuple(_event_from_dict(item) for item in payload.get("events", []))
    as_of_raw = payload.get("as_of")
    high = payload.get("last_swing_high")
    low = payload.get("last_swing_low")
    protected_high = payload.get("protected_high")
    protected_low = payload.get("protected_low")

    return StructureSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        as_of=datetime.fromisoformat(as_of_raw) if as_of_raw else None,
        break_bias=BreakBias(payload.get("break_bias", BreakBias.NEUTRAL.value)),
        confirmed_trend=ConfirmedTrend(
            payload.get("confirmed_trend", ConfirmedTrend.SIDEWAYS.value)
        ),
        last_swing_high=_swing_from_dict(high) if high else None,
        last_swing_low=_swing_from_dict(low) if low else None,
        protected_high=_swing_from_dict(protected_high) if protected_high else None,
        protected_low=_swing_from_dict(protected_low) if protected_low else None,
        swings=swings,
        events=events,
        latest_event=events[-1] if events else None,
        engine_version=payload.get("engine_version", STRUCTURE_ENGINE_VERSION),
        config_hash=payload.get("config_hash", ""),
        candle_finality=payload.get("candle_finality", "closed"),
        data_quality=dict(payload.get("data_quality", {})),
    )


class IndicatorService:
    """Nghiệp vụ chỉ báo và cấu trúc thị trường."""

    def __init__(
        self,
        *,
        settings: Settings,
        market_repository: MarketRepository,
        indicator_repository: IndicatorRepository,
    ) -> None:
        """Khởi tạo service.

        Args:
            settings: Cấu hình ứng dụng.
            market_repository: Repository đọc nến.
            indicator_repository: Repository chỉ báo và sự kiện cấu trúc.
        """
        self._settings = settings
        self._market_repository = market_repository
        self._indicator_repository = indicator_repository

    async def get_indicators(self, symbol: str, timeframe: str = "1D") -> IndicatorSet:
        """Đọc bộ chỉ báo đã precompute, tính bù khi thiếu.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.

        Returns:
            `IndicatorSet` của nến mới nhất.

        Raises:
            InsufficientDataError: Khi chưa có nến và cũng không tính được.
        """
        ticker = symbol.upper()
        stored = await self._indicator_repository.get_snapshots(
            ticker, timeframe, engine_version=ENGINE_VERSION
        )
        stored = [
            row
            for row in stored
            if row.indicator != STRUCTURE_INDICATOR
            and row.payload.get("unstable_periods", {}) == unstable_metadata()
        ]
        expected = {
            (name, parameters_hash({**get_spec(name).default_parameters, **params}))
            for name, params in DEFAULT_INDICATOR_REQUESTS
        }
        available = {
            (
                row.indicator,
                parameters_hash(
                    {
                        k: v
                        for k, v in row.payload.get("parameters", {}).items()
                        if k != "algorithm_version"
                    }
                ),
            )
            for row in stored
        }
        if not expected <= available:
            result = await self.recompute(ticker, timeframe)
            return IndicatorSet(
                symbol=result.symbol,
                timeframe=result.timeframe,
                as_of=result.as_of,
                candles_used=result.candles_used,
                computed_on_demand=True,
                results=result.results,
            )

        candle_time = stored[0].candle_time
        results = [
            IndicatorResult(
                symbol=ticker,
                timeframe=timeframe,
                as_of=candle_time,
                indicator=row.indicator,
                parameters=row.payload.get("parameters", {}),
                values=row.payload.get("values", {}),
                signal=row.payload.get("signal"),
                warmup=int(row.payload.get("warmup", 0)),
                status=row.payload.get("status", "ok"),
                engine_version=row.engine_version,
                events=row.payload.get("events", []),
            )
            for row in stored
        ]
        return IndicatorSet(
            symbol=ticker,
            timeframe=timeframe,
            as_of=candle_time,
            candles_used=0,
            computed_on_demand=False,
            results=results,
        )

    async def get_series(
        self,
        symbol: str,
        timeframe: str = "1D",
        *,
        requests: Sequence[tuple[str, dict[str, int | float]]] | None = None,
        limit: int = 250,
    ) -> IndicatorSeriesSet:
        """Tính chuỗi thời gian của các chỉ báo trên nến đã đóng.

        Mỗi điểm được tính bằng chính engine dùng cho snapshot, nên giá trị tại
        nến cuối trùng với kết quả `/indicators/{symbol}`. Cách tính là chạy
        native theo batch; custom giữ prefix causal khi cần, tối đa 1000 nến.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            requests: Danh sách (tên chỉ báo, tham số); mặc định dùng bộ chỉ báo
                biểu đồ.
            limit: Số nến tối đa.

        Returns:
            `IndicatorSeriesSet` gồm nến và chuỗi chỉ báo.

        Raises:
            InsufficientDataError: Khi mã chưa có nến đã đóng.
        """
        ticker = symbol.upper()
        planned = tuple(requests or CHART_INDICATOR_REQUESTS)
        candles = await self._market_repository.get_candles(ticker, timeframe, limit=limit)
        candles = self._divergence_closed_candles(candles)
        if not candles:
            raise InsufficientDataError(f"Mã {ticker} chưa có nến {timeframe} đã đóng.")

        ohlcv = self._build_ohlcv(candles)
        IndicatorFrame(ohlcv, [c.candle_open_time for c in candles], timeframe).validate()
        needs_benchmark = any(get_spec(name).input_kind == "benchmark" for name, _ in planned)
        benchmark = await self._benchmark_array(timeframe, candles) if needs_benchmark else None
        series = [
            self._build_series(name, parameters, ohlcv, candles, benchmark)
            for name, parameters in planned
        ]
        return IndicatorSeriesSet(
            symbol=ticker,
            timeframe=timeframe,
            as_of=candles[-1].candle_open_time,
            candles=candles,
            series=series,
        )

    def _divergence_closed_candles(
        self, candles: list[Candle], *, as_of: datetime | None = None
    ) -> list[Candle]:
        """Exclude mutable D1/W1 tails using the VN 15:00 session cutoff.

        Holidays without an official exchange-calendar feed are handled
        conservatively: W1 waits until Friday 15:00, even for a short week.
        Applied to all persisted indicators; market storage remains unchanged.
        """
        now = (
            datetime.now(tz=self._settings.market_tz)
            if as_of is None
            else as_of.astimezone(self._settings.market_tz)
        )
        closed = []
        for candle in candles:
            day = candle.candle_open_time.astimezone(self._settings.market_tz).date()
            if candle.timeframe == "1W":
                day += timedelta(days=4 - day.weekday())
            cutoff = datetime.combine(day, time(15), tzinfo=self._settings.market_tz)
            if cutoff > now:
                break
            closed.append(candle)
        return closed

    def _build_series(
        self,
        name: str,
        parameters: dict[str, int | float],
        ohlcv: Ohlcv,
        candles: list[Candle],
        benchmark: np.ndarray | None,
    ) -> IndicatorSeriesOut:
        """Tính một chuỗi chỉ báo trên từng tiền tố của chuỗi nến."""
        spec = get_spec(name)
        merged: dict[str, Any] = {**spec.default_parameters, **parameters}
        points: list[IndicatorPointOut] = []
        if name == "divergence":
            closed = self._divergence_closed_candles(candles)
            output = compute_indicator(name, self._build_ohlcv(closed), merged)
            events = divergence_event_views(output, closed)
            by_time: dict[datetime, list[DivergenceEventOut]] = {}
            for event in events:
                by_time.setdefault(event.confirmed_at, []).append(event)
            for candle in closed[output.warmup - 1 :]:
                active = by_time.get(candle.candle_open_time, [])
                values: dict[str, float | None] = dict.fromkeys(
                    ("bullish_regular", "bearish_regular", "bullish_hidden", "bearish_hidden"), 0.0
                )
                for event in active:
                    values[f"{event.direction}_regular"] = 1.0
                points.append(
                    IndicatorPointOut(
                        time=candle.candle_open_time,
                        values=values,
                        signal=f"{active[0].direction}_regular" if active else None,
                    )
                )
            return IndicatorSeriesOut(
                key=f"divergence({parameters_hash(output.parameters)})",
                indicator=name,
                parameters=output.parameters,
                points=points,
                events=events,
            )
        from app.indicators import last_value
        from app.indicators.factory import NATIVE_SPECS, build_indicator, interpret_signal

        if name in NATIVE_SPECS or name in ("adx", "bollinger", "bb_width", "rvol"):
            frame = IndicatorFrame(
                ohlcv, [c.candle_open_time for c in candles], candles[0].timeframe
            )
            result = build_indicator(name, merged, benchmark).calculate(frame)
            previous: dict[str, float | None] = {}
            for index, candle in enumerate(candles):
                values = {
                    key: last_value(array[index : index + 1])
                    for key, array in result.series.items()
                }
                if index >= result.lookback and any(v is not None for v in values.values()):
                    points.append(
                        IndicatorPointOut(
                            time=candle.candle_open_time,
                            values=values,
                            signal=interpret_signal(
                                name, values, previous, float(ohlcv.close[index])
                            ),
                        )
                    )
                previous = values
            return IndicatorSeriesOut(
                key=f"{name}({', '.join(f'{key}={value}' for key, value in merged.items())})",
                indicator=name,
                parameters=merged,
                points=points,
            )
        for index, candle in enumerate(candles):
            if index + 1 < spec.warmup:
                continue
            prefix = Ohlcv(
                open=ohlcv.open[: index + 1],
                high=ohlcv.high[: index + 1],
                low=ohlcv.low[: index + 1],
                close=ohlcv.close[: index + 1],
                volume=ohlcv.volume[: index + 1],
            )
            prefix_benchmark = benchmark[: index + 1] if benchmark is not None else None
            output = compute_indicator(name, prefix, merged, benchmark=prefix_benchmark)
            if not any(value is not None for value in output.values.values()):
                continue
            points.append(
                IndicatorPointOut(
                    time=candle.candle_open_time,
                    values=output.values,
                    signal=output.signal,
                )
            )
        return IndicatorSeriesOut(
            key=f"{name}({', '.join(f'{key}={value}' for key, value in merged.items())})",
            indicator=name,
            parameters=merged,
            points=points,
        )

    async def recompute(
        self, symbol: str, timeframe: str = "1D", *, end: datetime | None = None
    ) -> IndicatorSet:
        """Tính lại chỉ báo và cấu trúc, lưu snapshot mới.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.
            end: Aware replay cutoff; only bars closed by this time are eligible.

        Returns:
            `IndicatorSet` vừa tính.

        Raises:
            InsufficientDataError: Khi chưa nạp đủ nến.
        """
        if end is not None and (end.tzinfo is None or end.utcoffset() is None):
            raise ValueError("Historical replay end must have a timezone")
        ticker = symbol.upper()
        candles = await self._market_repository.get_candles(
            ticker, timeframe, limit=self._settings.compute_lookback, end=end
        )
        candles = self._divergence_closed_candles(candles, as_of=end)
        if len(candles) < MIN_CANDLES:
            raise InsufficientDataError(
                f"Mã {ticker} chỉ có {len(candles)} nến, cần tối thiểu {MIN_CANDLES}."
            )

        ohlcv = self._build_ohlcv(candles)
        IndicatorFrame(ohlcv, [c.candle_open_time for c in candles], timeframe).validate()
        benchmark = await self._benchmark_array(timeframe, candles)
        digest = hashlib.sha256()
        for key in ("open", "high", "low", "close", "volume"):
            digest.update(getattr(ohlcv, key).tobytes())
        digest.update("|".join(c.candle_open_time.isoformat() for c in candles).encode())
        if benchmark is not None:
            digest.update(benchmark.tobytes())
        input_hash = digest.hexdigest()
        stored = await self._indicator_repository.get_snapshots(
            ticker, timeframe, engine_version=ENGINE_VERSION
        )
        reusable = {
            (
                row.indicator,
                parameters_hash(
                    {
                        k: v
                        for k, v in row.payload.get("parameters", {}).items()
                        if k != "algorithm_version"
                    }
                ),
            ): row
            for row in stored
            if row.payload.get("input_hash") == input_hash
            and row.payload.get("unstable_periods", {}) == unstable_metadata()
        }
        outputs = []
        candle_time = candles[-1].candle_open_time
        for name, params in DEFAULT_INDICATOR_REQUESTS:
            merged: dict[str, Any] = {**get_spec(name).default_parameters, **params}
            cached = reusable.get((name, parameters_hash(merged)))
            if cached is not None:
                payload = cached.payload
                output = IndicatorOutput(
                    name,
                    payload["parameters"],
                    payload["values"],
                    payload["warmup"],
                    payload.get("signal"),
                    payload.get("raw_events", []),
                )
            else:
                output = compute_indicator(name, ohlcv, params, benchmark=benchmark)
                await self._persist_output(
                    ticker, timeframe, candle_time, output, candles, input_hash=input_hash
                )
            outputs.append(output)

        previous_structure = await self._indicator_repository.get_latest_snapshot(
            ticker,
            timeframe,
            indicator=STRUCTURE_INDICATOR,
            engine_version=STRUCTURE_ENGINE_VERSION,
        )
        if (
            previous_structure is not None
            and previous_structure.payload.get("input_hash") == input_hash
            and previous_structure.payload.get("config_hash") == MarketStructureConfig().config_hash
        ):
            snapshot = structure_snapshot_from_payload(
                previous_structure.payload, symbol=ticker, timeframe=timeframe
            )
        else:
            snapshot = self._compute_structure(ticker, timeframe, candles)
            await self._persist_structure(
                ticker, timeframe, candle_time, snapshot, input_hash=input_hash
            )

        logger.info(
            "indicators_recomputed",
            extra={
                "symbol": ticker,
                "timeframe": timeframe,
                "candles": len(candles),
                "indicators": len(outputs),
                "events": len(snapshot.events),
            },
        )
        return IndicatorSet(
            symbol=ticker,
            timeframe=timeframe,
            as_of=candle_time,
            candles_used=len(candles),
            computed_on_demand=True,
            results=[
                self._to_result(ticker, timeframe, candle_time, output, candles)
                for output in outputs
            ],
        )

    async def get_structure(self, symbol: str, timeframe: str = "1D") -> StructureSnapshot:
        """Đọc snapshot cấu trúc mới nhất, tính lại nếu đã cũ.

        Args:
            symbol: Mã chứng khoán.
            timeframe: Khung thời gian.

        Returns:
            `StructureSnapshot` của nến đã đóng mới nhất.

        Raises:
            InsufficientDataError: Khi chưa nạp đủ nến.
        """
        ticker = symbol.upper()
        candles = await self._market_repository.get_candles(
            ticker, timeframe, limit=self._settings.compute_lookback
        )
        candles = self._divergence_closed_candles(candles)
        if not candles:
            raise InsufficientDataError(f"Mã {ticker} chưa có nến {timeframe} đã đóng.")

        latest_time = candles[-1].candle_open_time
        stored = await self._indicator_repository.get_latest_snapshot(
            ticker,
            timeframe,
            indicator=STRUCTURE_INDICATOR,
            engine_version=STRUCTURE_ENGINE_VERSION,
        )
        if (
            stored is not None
            and stored.candle_time == latest_time
            and stored.payload.get("unstable_periods", {}) == unstable_metadata()
        ):
            return structure_snapshot_from_payload(
                stored.payload, symbol=ticker, timeframe=timeframe
            )

        snapshot = self._compute_structure(ticker, timeframe, candles)
        await self._persist_structure(ticker, timeframe, latest_time, snapshot)
        return snapshot

    def _build_ohlcv(self, candles: list[Candle]) -> Ohlcv:
        """Chuyển nến ORM thành mảng numpy cho engine chỉ báo."""
        return Ohlcv(
            open=np.asarray([float(row.open) for row in candles], dtype=np.float64),
            high=np.asarray([float(row.high) for row in candles], dtype=np.float64),
            low=np.asarray([float(row.low) for row in candles], dtype=np.float64),
            close=np.asarray([float(row.close) for row in candles], dtype=np.float64),
            volume=np.asarray([float(row.volume) for row in candles], dtype=np.float64),
        )

    async def _benchmark_array(self, timeframe: str, candles: list[Candle]) -> np.ndarray | None:
        """Căn chuỗi giá chỉ số tham chiếu theo đúng phiên của cổ phiếu."""
        benchmark_rows = await self._market_repository.get_candles(
            self._settings.benchmark_symbol,
            timeframe,
            limit=self._settings.compute_lookback,
            end=candles[-1].candle_open_time + timedelta(microseconds=1),
        )
        if not benchmark_rows:
            return None
        by_time = {row.candle_open_time: float(row.close) for row in benchmark_rows}
        values: list[float] = []
        for row in candles:
            close = by_time.get(row.candle_open_time)
            if close is None:
                return None
            values.append(close)
        return np.asarray(values, dtype=np.float64)

    def _compute_structure(
        self,
        ticker: str,
        timeframe: str,
        candles: list[Candle],
    ) -> StructureSnapshot:
        """Chạy engine cấu trúc trên nến đã đóng."""
        bars = [
            CandleBar(
                time=row.candle_open_time,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
            )
            for row in candles
        ]
        engine = MarketStructureIndicator(MarketStructureConfig())
        return engine.analyze(bars, symbol=ticker, timeframe=timeframe, is_final_candle=True)

    async def _persist_output(
        self,
        ticker: str,
        timeframe: str,
        candle_time: datetime,
        output: IndicatorOutput,
        candles: list[Candle] | None = None,
        *,
        input_hash: str | None = None,
    ) -> None:
        """Lưu một kết quả chỉ báo vào bảng snapshot."""
        await self._indicator_repository.upsert_snapshot(
            symbol=ticker,
            timeframe=timeframe,
            candle_time=candle_time,
            indicator=output.indicator,
            params_hash=parameters_hash({**output.parameters, "_unstable": unstable_metadata()}),
            engine_version=ENGINE_VERSION,
            payload={
                "input_hash": input_hash,
                "unstable_periods": unstable_metadata(),
                "formula_version": FORMULA_VERSION,
                "ta_lib_version": talib.__version__,
                "ta_lib_c_version": talib.__ta_version__.decode(),
                "parameters": output.parameters,
                "values": output.values,
                "signal": output.signal,
                "warmup": output.warmup,
                "status": output.status,
                "raw_events": output.events,
                "events": [
                    e.model_dump(mode="json") for e in divergence_event_views(output, candles or [])
                ],
            },
        )

    async def _persist_structure(
        self,
        ticker: str,
        timeframe: str,
        candle_time: datetime,
        snapshot: StructureSnapshot,
        *,
        input_hash: str | None = None,
    ) -> None:
        """Lưu sự kiện cấu trúc và snapshot tổng hợp."""
        rows = [
            {
                "symbol": ticker,
                "timeframe": timeframe,
                "event_type": event.event_type.value,
                "pivot_time": event.pivot_time,
                "confirmed_at": event.confirmed_at,
                "event_time": event.broken_at,
                "break_level": event.break_level,
                "observed_price": event.observed_price,
                "bias_before": event.bias_before.value,
                "bias_after": event.bias_after.value,
                "config_hash": snapshot.config_hash,
                "engine_version": snapshot.engine_version,
                "payload": _event_to_dict(event),
                "idempotency_key": self._event_key(ticker, timeframe, event, snapshot.config_hash),
            }
            for event in snapshot.events
        ]
        await self._indicator_repository.upsert_structure_events(rows)
        await self._indicator_repository.upsert_snapshot(
            symbol=ticker,
            timeframe=timeframe,
            candle_time=candle_time,
            indicator=STRUCTURE_INDICATOR,
            params_hash=snapshot.config_hash,
            engine_version=snapshot.engine_version,
            payload={
                **structure_snapshot_to_payload(snapshot),
                "input_hash": input_hash,
                "parameters": MarketStructureConfig().to_dict(),
            },
        )

    @staticmethod
    def _event_key(
        ticker: str,
        timeframe: str,
        event: StructureEvent,
        config_hash: str,
    ) -> str:
        """Sinh khoá chống trùng cho một sự kiện cấu trúc."""
        raw = "|".join(
            [
                ticker,
                timeframe,
                event.event_type.value,
                event.pivot_time.isoformat(),
                event.broken_at.isoformat(),
                config_hash,
            ]
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _to_result(
        ticker: str,
        timeframe: str,
        candle_time: datetime,
        output: IndicatorOutput,
        candles: list[Candle] | None = None,
    ) -> IndicatorResult:
        """Chuyển `IndicatorOutput` thành schema trả cho client."""
        return IndicatorResult(
            symbol=ticker,
            timeframe=timeframe,
            as_of=candle_time,
            indicator=output.indicator,
            parameters=output.parameters,
            values=output.values,
            signal=output.signal,
            warmup=output.warmup,
            status=output.status,
            engine_version=ENGINE_VERSION,
            events=divergence_event_views(output, candles or []),
        )


def divergence_event_views(
    output: IndicatorOutput, candles: list[Candle]
) -> list[DivergenceEventOut]:
    """Map window-local indices to chart timestamps and stable deduplication IDs."""
    if output.indicator != "divergence" or not candles:
        return []
    results = []
    for raw in output.events:
        event = dict(raw)
        oscillators = [
            {
                **oscillator,
                "start_time": candles[oscillator["start_index"]].candle_open_time,
                "end_time": candles[oscillator["end_index"]].candle_open_time,
            }
            for oscillator in raw["oscillators"]
        ]
        start = candles[raw["start_index"]].candle_open_time
        end = candles[raw["end_index"]].candle_open_time
        confirmed = candles[raw["confirmed_index"]].candle_open_time
        names = " + ".join(o["indicator"] for o in oscillators)
        identity = f"{start.isoformat()}:{end.isoformat()}:{raw['direction']}:{names}:{parameters_hash(output.parameters)}"
        event.pop("price_confirmed_index")
        event.pop("confirmed_index")
        results.append(
            DivergenceEventOut(
                **{
                    **event,
                    "id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                    "start_time": start,
                    "end_time": end,
                    "price_confirmed_at": candles[raw["price_confirmed_index"]].candle_open_time,
                    "confirmed_at": confirmed,
                    "oscillators": oscillators,
                    "alerts": event_alerts(raw),
                    "label": names + (" Bear" if raw["direction"] == "bearish" else " Bull"),
                }
            )
        )
    return results
