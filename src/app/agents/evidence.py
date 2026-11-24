"""Read one bounded, confirmed technical snapshot from the existing engine."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import numpy as np
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.agents.research import RULE_VERSION
from app.core.config import Settings
from app.core.database import session_scope
from app.core.logging import get_logger
from app.fcdata.client import FcDataClient
from app.indicators import Ohlcv
from app.indicators.custom import MarketStructureIndicator
from app.indicators.engine import IndicatorFrame
from app.indicators.registry import compute_indicator
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.schemas.structure import StructureSnapshotOut
from app.services.indicator_service import (
    IndicatorSeriesSet,
    IndicatorService,
    planned_requests,
)
from app.services.market_service import MarketService
from app.services.provisional_bar import ProvisionalBarProvider

logger = get_logger(__name__)

REQUESTS: tuple[tuple[str, dict[str, int | float]], ...] = (
    ("sma", {"period": 20}),
    ("sma", {"period": 50}),
    ("rsi", {"period": 14}),
    ("macd", {"fast_period": 12, "slow_period": 26, "signal_period": 9}),
    ("adx", {"period": 14}),
    ("plus_di", {"period": 14}),
    ("minus_di", {"period": 14}),
    ("ichimoku", {"tenkan_period": 9, "kijun_period": 26, "senkou_b_period": 52}),
    ("bollinger", {"period": 20, "stddev": 2.0}),
    ("volume_sma", {"period": 20}),
    ("rvol", {"period": 20}),
    ("obv", {}),
    ("divergence", {}),
)


class MarketEvidenceLoader:
    """Keep ORM sessions short and reuse indicator/structure implementations."""

    def __init__(
        self, settings: Settings, fcdata: FcDataClient | None = None, redis: Redis | None = None
    ):
        self._settings = settings
        self._fcdata = fcdata
        self._redis = redis

    async def load(
        self, symbol: str, timeframe: str, analyzed_at: datetime, include_live: bool = False
    ) -> dict[str, Any]:
        """Compute a causal view; no candle opening after the requested time enters it."""
        if analyzed_at > datetime.now(UTC):
            raise ValueError("analyzed_at cannot be in the future")
        async with session_scope() as session:
            service = IndicatorService(
                settings=self._settings,
                market_repository=MarketRepository(session),
                indicator_repository=IndicatorRepository(session),
            )
            candles = await service.load_confirmed_series_candles(
                symbol, timeframe, 500, analyzed_at
            )
            revision = hashlib.sha256(
                repr(
                    [
                        (
                            c.candle_open_time,
                            c.open,
                            c.high,
                            c.low,
                            c.close,
                            c.volume,
                            c.source,
                            c.price_basis,
                        )
                        for c in candles
                    ]
                ).encode()
            ).hexdigest()
            cache_key = f"agent:technical:v2:{RULE_VERSION}:{symbol}:{timeframe}:{revision}"
            evidence = None
            if self._redis is not None:
                try:
                    raw = await self._redis.get(cache_key)
                    if raw is not None:
                        evidence = json.loads(raw)
                except (RedisError, ValueError):
                    logger.warning("agent_technical_cache_read_failed")
            if evidence is None:
                series_set = await service.build_confirmed_series(
                    symbol, timeframe, candles, planned_requests(timeframe, REQUESTS)
                )
                evidence = self._snapshot(symbol, timeframe, series_set)
                if self._redis is not None:
                    try:
                        await self._redis.set(cache_key, json.dumps(evidence), ex=300)
                    except RedisError:
                        logger.warning("agent_technical_cache_write_failed")
        market = evidence["market_snapshot"]
        market["market_status"] = "CLOSED_ONLY" if include_live else "HISTORICAL"
        if include_live and self._fcdata is not None:
            try:
                async with session_scope() as session:
                    quote = await MarketService(
                        settings=self._settings,
                        repository=MarketRepository(session),
                        redis=self._redis,
                        fcdata=self._fcdata,
                    ).get_quote(symbol)
                quote_fresh_at_cutoff = (
                    quote.as_of is not None
                    and quote.as_of <= analyzed_at
                    and (analyzed_at - quote.as_of).total_seconds()
                    <= self._settings.quote_stale_after_seconds
                )
                if quote.price is not None and not quote.is_stale and quote_fresh_at_cutoff:
                    market["market_status"] = "LIVE"
                    market["realtime_price"] = quote.price
                    market["realtime_timestamp"] = (
                        quote.as_of.isoformat() if quote.as_of is not None else None
                    )
                    market["realtime_source"] = quote.source
            except Exception as exc:
                logger.warning(
                    "agent_live_quote_unavailable",
                    extra={"symbol": symbol, "error_type": type(exc).__name__},
                )
        if include_live and timeframe == "1D" and self._fcdata is not None:
            market_time = analyzed_at.astimezone(self._settings.market_tz)
            if (
                market_time.time() < self._settings.market_close
                and candles[-1].price_basis == "RAW"
            ):
                try:
                    async with asyncio.timeout(4):
                        live = await ProvisionalBarProvider(self._settings, self._fcdata).fetch(
                            symbol
                        )
                    if live and candles[-1].candle_open_time < live.open_time <= analyzed_at:
                        fields = ("open", "high", "low", "close", "volume")
                        ohlcv = Ohlcv(
                            *(
                                np.asarray(
                                    [
                                        *(float(getattr(candle, field)) for candle in candles),
                                        float(getattr(live.bar, field)),
                                    ],
                                    dtype=np.float64,
                                )
                                for field in fields
                            )
                        )
                        market["provisional"] = {
                            "status": "PROVISIONAL",
                            "candle_open_time": live.open_time.isoformat(),
                            "observed_at": datetime.now(UTC).isoformat(),
                            **{field: float(getattr(live.bar, field)) for field in fields},
                            "indicator_values": {
                                name + str(params): compute_indicator(name, ohlcv, params).values
                                for name, params in planned_requests(timeframe, REQUESTS)
                                if name not in {"divergence", "ichimoku"}
                            },
                        }
                except Exception as exc:
                    logger.warning(
                        "agent_provisional_unavailable", extra={"error_type": type(exc).__name__}
                    )
        return evidence

    @staticmethod
    def _snapshot(symbol: str, timeframe: str, series_set: IndicatorSeriesSet) -> dict[str, Any]:
        """Project and cache confirmed technical evidence only."""
        candles = series_set.candles
        latest = candles[-1]
        previous = candles[-2] if len(candles) > 1 else None
        frame = IndicatorFrame(
            Ohlcv(
                *(
                    np.asarray(
                        [float(getattr(candle, column)) for candle in candles], dtype=np.float64
                    )
                    for column in ("open", "high", "low", "close", "volume")
                )
            ),
            [candle.candle_open_time for candle in candles],
            timeframe,
        )
        structure = MarketStructureIndicator().snapshot(frame, symbol=symbol)
        structure_data = StructureSnapshotOut.model_validate(structure, from_attributes=True)
        values = {
            item.key: {
                "indicator": item.indicator,
                "parameters": item.parameters,
                "values": item.points[-1].values if item.points else {},
                "previous_values": item.points[-2].values if len(item.points) > 1 else {},
                "signal": item.points[-1].signal if item.points else None,
            }
            for item in series_set.series
        }
        divergence = next(
            (item for item in series_set.series if item.indicator == "divergence"), None
        )
        locals_ = (
            []
            if divergence is None
            else [
                event
                for event in divergence.events
                if event.confirmed_at <= latest.candle_open_time
            ]
        )
        structural = (
            []
            if divergence is None
            else [
                event
                for event in divergence.structural_divergences
                if event.confirmed_at <= latest.candle_open_time
            ]
        )
        members = {member for event in structural for member in event.member_ids}
        events = sorted(
            [event.model_dump(mode="json") for event in locals_ if event.id not in members]
            + [event.model_dump(mode="json") for event in structural],
            key=lambda event: event["confirmed_at"],
        )
        market = {
            "symbol": symbol,
            "timeframe": timeframe,
            "candle_status": "CONFIRMED",
            "data_timestamp": latest.candle_open_time.isoformat(),
            "open": float(latest.open),
            "high": float(latest.high),
            "low": float(latest.low),
            "close": float(latest.close),
            "volume": latest.volume,
            "previous_close": float(previous.close) if previous else None,
            "previous_volume": previous.volume if previous else None,
            "price_basis": latest.price_basis,
            "source": latest.source,
            "candles_used": len(candles),
            "market_status": "HISTORICAL",
            "realtime_price": None,
        }
        return {
            "market_snapshot": market,
            "indicator_values": values,
            "structure": {
                "status": structure_data.status,
                "confirmed_trend": structure_data.confirmed_trend,
                "break_bias": structure_data.break_bias,
                "swings": [s.model_dump(mode="json") for s in structure_data.swings[-6:]],
                "events": [e.model_dump(mode="json") for e in structure_data.events[-6:]],
                "engine_version": structure_data.engine_version,
            },
            "divergence_events": events[-10:],
        }
