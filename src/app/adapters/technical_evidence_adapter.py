"""Point-in-time evidence through the existing market repository and indicator engine."""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Protocol

import talib

from app.core.config import Settings
from app.core.database import SessionFactory
from app.indicators import ENGINE_VERSION
from app.indicators.custom import MarketStructureIndicator
from app.indicators.engine import IndicatorFrame, TalibIndicator
from app.indicators.native import unstable_metadata
from app.indicators.registry import compute_indicator
from app.repositories.evidence_repository import EvidenceCache, content_key
from app.repositories.indicator_repository import IndicatorRepository
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import (
    IndicatorService,
    divergence_event_views,
    structure_snapshot_to_payload,
)


class TechnicalEvidenceProvider(Protocol):
    """Return only historical closed-bar measurements and calculation provenance."""

    async def snapshot(
        self,
        symbol: str,
        timeframe: str,
        analysis_at: datetime,
        *,
        price_basis: str,
        exchange: str | None = None,
    ) -> dict: ...


class TechnicalEvidenceAdapter:
    """Reuse FCData ingestion if opted in; never reimplement formulas or weekly aggregation."""

    def __init__(
        self, settings: Settings, session_factory=SessionFactory, cache: EvidenceCache | None = None
    ):
        self.settings, self.session_factory = settings, session_factory
        self.cache = cache

    async def snapshot(
        self,
        symbol: str,
        timeframe: str,
        analysis_at: datetime,
        *,
        price_basis: str = "RAW",
        exchange: str | None = None,
    ) -> dict:
        """Bound OHLCV at analysis time, validate adjustment basis and honor pivot confirmation."""
        if analysis_at.tzinfo is None or analysis_at.utcoffset() is None:
            raise ValueError("analysis_at must have a timezone")
        if price_basis != "RAW":
            return {
                "status": "needs_review",
                "reason": "Historical adjusted-price vintage is not recorded by the existing market store; RAW or a verified point-in-time vintage is required.",
            }
        if timeframe not in ("1D", "1W"):
            return {
                "status": "insufficient_history",
                "reason": "Only existing D1/W1 engine timeframes are supported.",
            }
        async with self.session_factory() as session:
            repository = MarketRepository(session)
            candles = await repository.get_candles(
                symbol,
                timeframe,
                limit=self.settings.compute_lookback,
                end=analysis_at,
                price_basis=price_basis,
            )
            if (
                len(candles) < 60
                and self.settings.rag_fetch_missing_market_data
                and timeframe == "1D"
                and price_basis == "RAW"
            ):
                from app.fcdata.client import FcDataClient
                from app.services.ingestion_service import IngestionService

                async with FcDataClient(self.settings) as client:
                    await IngestionService(
                        settings=self.settings, repository=repository, fcdata=client
                    ).backfill_symbol(
                        symbol,
                        timeframe=timeframe,
                        start=analysis_at.date()
                        - timedelta(days=self.settings.backfill_lookback_days),
                        end=analysis_at.date(),
                    )
                candles = await repository.get_candles(
                    symbol,
                    timeframe,
                    limit=self.settings.compute_lookback,
                    end=analysis_at,
                    price_basis=price_basis,
                )
            service = IndicatorService(
                settings=self.settings,
                market_repository=repository,
                indicator_repository=IndicatorRepository(session),
            )
            candles = service._divergence_closed_candles(candles, as_of=analysis_at)
            if not candles:
                return {
                    "status": "insufficient_history",
                    "reason": "No closed candles at the requested timestamp/basis.",
                }
            # Capture source exchange if the catalog has it; mismatch is a review condition.
            from app.models.symbol import Symbol

            catalog = await session.get(Symbol, symbol)
            source_exchange = getattr(catalog, "exchange", None)
            if exchange and (not source_exchange or exchange.upper() != source_exchange.upper()):
                return {
                    "status": "needs_review",
                    "reason": "Requested exchange conflicts with FCData catalog.",
                }
            frame = IndicatorFrame(
                service._build_ohlcv(candles), [c.candle_open_time for c in candles], timeframe
            )
            frame.validate()
            key = content_key(
                "technical-v3",
                ENGINE_VERSION,
                talib.__version__,
                unstable_metadata(),
                symbol,
                timeframe,
                analysis_at,
                price_basis,
                exchange,
                [
                    (
                        c.candle_open_time.isoformat(),
                        *[
                            str(getattr(c, name))
                            for name in ("open", "high", "low", "close", "volume")
                        ],
                    )
                    for c in candles
                ],
            )
            if self.cache:
                cached = await self.cache.get(key)
                if cached:
                    return cached
            result = await asyncio.to_thread(
                self._calculate, frame, candles, symbol, analysis_at, price_basis, source_exchange
            )
            result = json.loads(json.dumps(result, default=str))
            if self.cache:
                await self.cache.put(key, result)
            return result

    def _calculate(self, frame, candles, symbol, analysis_at, price_basis, exchange) -> dict:
        """Use installed engine adapters; CPU work stays off FastAPI's event loop."""
        outputs: dict[str, Any] = {}
        for name in (
            "sma",
            "ema",
            "rsi",
            "macd",
            "adx",
            "atr",
            "bollinger",
            "volume_sma",
            "rvol",
            "obv",
        ):
            result = compute_indicator(name, frame.ohlcv)
            outputs[name] = {
                "values": result.values,
                "parameters": result.parameters,
                "status": result.status,
                "warmup": result.warmup,
            }
        divergence = compute_indicator("divergence", frame.ohlcv)
        events = [e.model_dump(mode="json") for e in divergence_event_views(divergence, candles)]
        structure_indicator = MarketStructureIndicator()
        structure = structure_snapshot_to_payload(structure_indicator.snapshot(frame, symbol))
        engulfing = TalibIndicator("engulfing", "CDLENGULFING", {}).calculate(frame)
        # JSON snapshots contain native Python scalars and no NaN.
        values = {
            "rsi": outputs["rsi"]["values"].get("rsi"),
            "adx": outputs["adx"]["values"].get("adx"),
            "atr": outputs["atr"]["values"].get("atr"),
            "rvol": outputs["rvol"]["values"].get("rvol"),
            "obv": outputs["obv"]["values"].get("obv"),
            "macd": outputs["macd"]["values"].get("macd"),
            "macd_histogram": outputs["macd"]["values"].get("histogram"),
        }
        return {
            "status": "ok" if len(frame.ohlcv) >= 60 else "insufficient_history",
            "reason": ""
            if len(frame.ohlcv) >= 60
            else "Fewer than 60 closed candles for the complete technical evidence bundle.",
            "symbol": symbol,
            "timeframe": frame.timeframe,
            "analysis_at": analysis_at.isoformat(),
            "as_of": frame.timestamps[-1].isoformat(),
            "bars": len(frame.ohlcv),
            "price_basis": price_basis,
            "exchange": exchange,
            "timezone": str(self.settings.market_tz),
            "source": "FCData/OHLCV repository",
            "engine_version": ENGINE_VERSION,
            "rsi_at_analysis": values.get("rsi"),
            "ta_lib_version": talib.__version__,
            "unstable_periods": unstable_metadata(),
            "indicators": outputs,
            "filter_values": values,
            "divergence_events": events,
            "divergence_parameters": divergence.parameters,
            "market_structure": structure,
            "structure_parameters": structure_indicator.parameters,
            "structure_events": structure["events"],
            "bullish_engulfing": bool(next(iter(engulfing.series.values()))[-1] > 0)
            if len(frame.ohlcv) > engulfing.lookback
            else None,
            "bearish_engulfing": bool(next(iter(engulfing.series.values()))[-1] < 0)
            if len(frame.ohlcv) > engulfing.lookback
            else None,
            "engulfing_parameters": {},
            "engulfing_function": "CDLENGULFING",
        }
