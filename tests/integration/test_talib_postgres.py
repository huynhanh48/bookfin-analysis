"""Read-only native parity against local persisted OHLCV; no market/provider requests."""

import numpy as np
import pytest
import talib
from numpy.testing import assert_allclose

from app.core.config import Settings
from app.core.database import session_scope
from app.indicators.engine import IndicatorFrame
from app.indicators.factory import build_indicator
from app.indicators.registry import INDICATORS
from app.repositories.market_repository import MarketRepository
from app.services.indicator_service import IndicatorService

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("symbol", ["FPT", "DPG", "HPG"])
async def test_persisted_market_arrays_match_native(symbol):
    """Compare full numeric arrays on each symbol without recalculating market indicators."""
    async with session_scope() as session:
        repository = MarketRepository(session)
        candles = await repository.get_candles(symbol, "1D", limit=400)
        service = IndicatorService(
            settings=Settings(_env_file=None),
            market_repository=repository,
            indicator_repository=None,
        )
        candles = service._divergence_closed_candles(candles)
        if len(candles) < 60:
            pytest.skip(f"{symbol} has fewer than 60 closed local candles")
        frame = IndicatorFrame(service._build_ohlcv(candles), [c.candle_open_time for c in candles])
        for name in ("rsi", "macd", "atr", "obv", "mfi"):
            adapter = build_indicator(name, INDICATORS[name].default_parameters)
            result = adapter.calculate(frame)
            reference = getattr(talib, adapter.function)(
                *(getattr(frame.ohlcv, k) for k in adapter.required_inputs), **adapter.parameters
            )
            expected = reference if isinstance(reference, tuple) else (reference,)
            for actual, direct in zip(result.series.values(), expected, strict=True):
                assert_allclose(actual, direct, rtol=1e-12, atol=1e-10, equal_nan=True)
                assert actual.dtype == np.float64
