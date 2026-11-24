"""Replay twenty historical sessions through the realtime screener worker."""

from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.test_screener_realtime import FakeClient, FakeRedis, settings

from app.core.database import session_scope
from app.repositories.market_repository import MarketRepository
from app.schemas.screener import ScreenerConditions, ScreenerRealtimeEvent
from app.services import screener_realtime_service

pytestmark = pytest.mark.integration


async def test_replay_twenty_sessions(monkeypatch):
    config = settings()
    symbols = ["VHM", "SSI", "HAG"]
    async with session_scope() as db:
        candles = await MarketRepository(db).recent_candles_by_symbol(
            symbols, limit=550, end=datetime(2026, 11, 20, 17, tzinfo=UTC)
        )
    days = sorted({bar.time for bar in candles["VHM"]})[-20:]
    assert len(days) == 20
    seen = set()
    replayed = 0
    for day in days:
        date = day.astimezone(config.market_tz).date()
        cutoff = datetime.combine(date, datetime.min.time(), config.market_tz) + timedelta(hours=11)

        class ReplayClock(datetime):
            @classmethod
            def now(cls, tz=None, replay_at=cutoff):
                return replay_at.astimezone(tz)

        monkeypatch.setattr(screener_realtime_service, "datetime", ReplayClock)
        today = {s: next((b for b in candles[s] if b.time == day), None) for s in symbols}
        history = {s: [b for b in candles[s] if b.time < day][-520:] for s in symbols}

        async def load(target, limit, end, closed=history):
            return {s: closed[s][-limit:] for s in target}

        fcdata = FakeClient(
            [
                {
                    "Symbol": s,
                    "TradingDate": date.strftime("%d/%m/%Y"),
                    "ClosePrice": b.close,
                    "TotalMatchVol": b.volume,
                }
                for s, b in today.items()
                if b is not None
            ]
        )
        redis = FakeRedis()
        worker = screener_realtime_service.ScreenerRealtimeProducer(
            config, symbols=symbols, redis=redis, fcdata=fcdata, history_loader=load
        )
        worker._conditions = ScreenerConditions.model_validate(
            {
                "min_price": {"enabled": True, "value": 1},
                **{
                    name: {"enabled": False}
                    for name in ("min_volume", "relative_volume", "price_change", "price_vs_ma5")
                },
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "basis": "confirmed",
                    "days": 20,
                },
            }
        )
        await worker._refresh_history(force=True)
        expected_divergence = {
            s for s in symbols if today[s] is not None and worker._divergence_events.get(s)
        }
        await worker._evaluate(symbols)
        worker._last_publish = float("-inf")
        await worker._publish_if_needed()
        event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
        assert {r.symbol for r in event.rows} == expected_divergence
        for row in event.rows:
            b = today[row.symbol]
            h = history[row.symbol]
            mean_volume = sum(x.volume for x in h[-10:]) / 10
            mean_price = (sum(x.close for x in h[-4:]) + b.close) / 5
            assert row.status == "LIVE"
            assert row.price == b.close
            assert row.volume == b.volume
            assert row.change_percent == pytest.approx(100 * (b.close / h[-1].close - 1))
            assert row.ma5 == pytest.approx(mean_price)
            assert row.relative_volume == pytest.approx(b.volume / mean_volume)
            assert row.divergence
            seen.add(row.symbol)
            replayed += 1
    assert seen
    print(f"20 sessions, {replayed} matching rows, symbols={sorted(seen)}")


async def test_find_peak_near_thirtieth_session_and_replay(monkeypatch):
    from collections import defaultdict

    from app.services.screener_engine import extract_divergence_events

    config = settings()
    async with session_scope() as db:
        repo = MarketRepository(db)
        rows, _ = await repo.list_symbols(active_only=True, limit=1000)
        candles = await repo.recent_candles_by_symbol(
            [row.symbol for row in rows],
            limit=550,
            end=datetime(2026, 11, 20, 17, tzinfo=UTC),
        )
    calendar = sorted({b.time for b in candles["VHM"]})
    anchor = calendar[-30]
    nearby = calendar[-35:-24]
    buckets = {basis: defaultdict(dict) for basis in ("pivot", "confirmed")}
    for symbol, bars in candles.items():
        for event in extract_divergence_events(bars, days=70):
            pivot = bars[event.end_index].time
            confirmed = bars[event.confirmed_index].time
            if pivot in nearby:
                buckets["pivot"][pivot][symbol] = confirmed
            if confirmed in nearby:
                buckets["confirmed"][confirmed][symbol] = confirmed

    print(f"anchor={anchor.astimezone(config.market_tz).date()}; universe={len(candles)}")
    for basis, days in buckets.items():
        peak = max(nearby, key=lambda day: len(days[day]))
        expected = days[peak]
        assert expected
        print(
            basis,
            [(d.astimezone(config.market_tz).date().isoformat(), len(days[d])) for d in nearby],
        )
        print(basis, "peak", peak.astimezone(config.market_tz).date(), sorted(expected))
        symbols = sorted(expected)
        target_date = peak.astimezone(config.market_tz).date()
        redis = FakeRedis()
        clock = [peak]

        class ReplayClock(datetime):
            @classmethod
            def now(cls, tz=None, current=clock):
                return current[0].astimezone(tz)

        monkeypatch.setattr(screener_realtime_service, "datetime", ReplayClock)

        async def load(target, limit, end):
            return {
                s: [b for b in candles[s] if b.time <= ReplayClock.now(UTC)][-limit:]
                for s in target
            }

        fcdata = FakeClient(
            [
                {
                    "Symbol": s,
                    "TradingDate": target_date.strftime("%d/%m/%Y"),
                    "ClosePrice": b.close,
                    "TotalMatchVol": b.volume,
                }
                for s in symbols
                for b in candles[s]
                if b.time == peak
            ]
        )
        worker = screener_realtime_service.ScreenerRealtimeProducer(
            config, symbols=symbols, redis=redis, fcdata=fcdata, history_loader=load
        )
        worker._conditions = ScreenerConditions.model_validate(
            {
                **{
                    k: {"enabled": False}
                    for k in (
                        "min_volume",
                        "min_price",
                        "relative_volume",
                        "price_change",
                        "price_vs_ma5",
                    )
                },
                "divergence": {
                    "enabled": True,
                    "direction": "any",
                    "kind": "any",
                    "basis": basis,
                    "window": "range",
                    "date_from": target_date,
                    "date_to": target_date,
                },
            }
        )
        # Simulate same worker before and after close, within its 300-second history TTL.
        checkpoints = [(peak, 11), (peak, 16)]
        if basis == "pivot":
            checkpoints.append((max(expected.values()), 16))
        for session, hour in checkpoints:
            replay_date = session.astimezone(config.market_tz).date()
            clock[0] = datetime.combine(
                replay_date, datetime.min.time(), config.market_tz
            ) + timedelta(hours=hour)
            await worker._refresh_history(symbols)
            await worker._evaluate(symbols)
            worker._last_publish = float("-inf")
            await worker._publish_if_needed()
            event = ScreenerRealtimeEvent.model_validate_json(redis.published[-1][1])
            wanted = {
                s
                for s, confirmed in expected.items()
                if confirmed < session or (hour == 16 and confirmed == session)
            }
            assert {row.symbol for row in event.rows} == wanted
            assert all(row.divergence for row in event.rows)
            print(basis, hour, "matches", len(event.rows))
