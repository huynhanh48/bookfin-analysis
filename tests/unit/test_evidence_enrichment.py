"""Mock-only evidence pipeline tests: no external keys, FCData calls or paid inference."""

import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy.dialects import postgresql

from app.adapters.embedding_provider import EmbeddingSpace
from app.adapters.file_storage import LocalFileStorage, validate_query_image
from app.adapters.firecrawl_adapter import FirecrawlExtractionAdapter
from app.adapters.news_search_adapter import canonical_url, publication_time
from app.adapters.openrouter_adapter import OpenRouterAdapter
from app.adapters.rerank_adapter import OpenRouterReranker
from app.adapters.tavily_adapter import TavilySearchAdapter
from app.adapters.vision_adapter import OpenRouterVisionExtractor
from app.core.config import Settings
from app.core.exceptions import InvalidRequestError, UpstreamError
from app.repositories.analysis_repository import AnalysisRepository
from app.schemas.analysis import AnalysisCreate, AnalysisSearch
from app.schemas.evidence import (
    ClaimTemporalScope,
    ExtractionResult,
    TechnicalClaim,
    VerificationStatus,
    VisionObservations,
)
from app.services.analysis_normalization_service import normalize_analysis
from app.services.news_enrichment_service import NewsEnrichmentService
from app.services.rag_enrichment_service import (
    RagEnrichmentService,
    resolve_context,
    signal_window_start,
)
from app.services.rag_query_service import RagQueryService, reciprocal_rank_fusion
from app.services.technical_verification_service import TechnicalVerificationService

AS_OF = datetime(2026, 1, 15, 8, 1, tzinfo=UTC)


class MemoryCache:
    """Small provider-cache test double, matching the production async boundary."""

    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, payload):
        self.values[key] = payload


def claim(kind, text="KDH có phân kỳ dương"):
    return TechnicalClaim(claim_type=kind, original_text=text)


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        openrouter_api_key="test",
        openrouter_max_retries=0,
        rag_enrichment_enabled=True,
        rag_news_enabled=True,
    )


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (40, 20), "white").save(stream, format="PNG")
    return stream.getvalue()


async def test_vision_typed_observations_do_not_self_verify(settings):
    extraction = ExtractionResult(
        observations=VisionObservations(
            symbol_candidates=["KDH"], timeframe="D1", description="KDH daily chart"
        ),
        user_claims=[claim("rsi_bullish_divergence")],
    )
    extraction.user_claims[0].verification_status = VerificationStatus.VERIFIED
    provider = SimpleNamespace(complete=AsyncMock(return_value=extraction.model_dump_json()))
    result = await OpenRouterVisionExtractor(provider).extract(
        "KDH có phân kỳ dương", "data:image/png;base64,abc"
    )
    assert result.user_claims[0].verification_status == "INSUFFICIENT_EVIDENCE"
    assert result.user_claims[0].source_type == "user"
    provider.complete.assert_awaited_once()
    with pytest.raises(ValueError):
        await OpenRouterVisionExtractor(provider).extract("Different original notes", None)


def test_resolution_does_not_invent_symbol_timeframe_or_date():
    case = SimpleNamespace(symbol=None, timeframe=None, analyzed_at=None)
    context, reasons = resolve_context(
        case, VisionObservations(symbol_candidates=["KDH", "FPT"], timeframe="D1"), {}
    )
    assert context["symbol"] is None and context["analysis_at"] is None
    assert "missing_or_ambiguous_context" in reasons
    case = SimpleNamespace(symbol="KDH", timeframe="D1", analyzed_at=AS_OF)
    context, reasons = resolve_context(
        case, VisionObservations(symbol_candidates=["FPT"], timeframe="W1"), {}
    )
    assert {"symbol_conflict", "timeframe_conflict"} <= set(reasons)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("bullish_engulfing", "CONTRADICTED"),
        ("volume_increase", "VERIFIED"),
        ("money_inflow", "NOT_VERIFIABLE"),
        ("rsi_bullish_divergence", "INSUFFICIENT_EVIDENCE"),
    ],
)
def test_claim_verification_keeps_uncertainty(kind, expected):
    original = claim(kind)
    snapshot = {
        "status": "ok",
        "as_of": AS_OF.isoformat(),
        "bullish_engulfing": False,
        "filter_values": {"rvol": 1.8},
        "divergence_events": [],
    }
    result = TechnicalVerificationService().verify([original], snapshot, "technical:test")[0]
    assert result.verification_status == expected
    assert original.verification_status == "INSUFFICIENT_EVIDENCE"
    assert result.evidence_ids == ["technical:test"]


def test_rsi_threshold_and_recent_divergence_verification():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 41.57},
        "divergence_parameters": {"rsi_period": 14},
        "divergence_events": [
            {
                "direction": "bearish",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [
                    {
                        "indicator": "RSI",
                        "rsi_context": {
                            "min": 20.0,
                            "max": 80.0,
                            "oversold_seen": True,
                            "overbought_seen": True,
                        },
                    }
                ],
            }
        ],
    }
    claims = [
        claim("rsi_bearish_divergence"),
        claim("rsi_overbought_in_divergence"),
        claim("rsi_below_30"),
    ]
    verified = TechnicalVerificationService(60).verify(claims, snapshot, "technical:test")
    statuses = {row.claim_type: row.verification_status for row in verified}
    assert statuses["rsi_bearish_divergence"] == "VERIFIED"
    assert statuses["rsi_overbought_in_divergence"] == "VERIFIED"
    assert statuses["rsi_below_30"] == "CONTRADICTED"


def test_rsi_oversold_context_requires_a_bullish_divergence():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 41.57},
        "divergence_events": [
            {
                "direction": "bullish",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [
                    {
                        "indicator": "RSI",
                        "rsi_context": {
                            "min": 22.0,
                            "max": 60.0,
                            "oversold_seen": True,
                            "overbought_seen": False,
                        },
                    }
                ],
            }
        ],
    }
    row = TechnicalVerificationService(60).verify(
        [claim("rsi_oversold_in_divergence")], snapshot, "technical:test"
    )[0]
    assert row.verification_status == "VERIFIED"


def test_point_rsi_and_historical_rsi_have_separate_temporal_scopes():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    context = {
        "min": 57.72,
        "max": 89.46,
        "rsi_at_first_pivot": 89.46,
        "rsi_at_second_pivot": 76.54,
        "rsi_min_during_window": 57.72,
        "rsi_max_during_window": 89.46,
        "oversold_seen": False,
        "overbought_seen": True,
    }
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "rsi_at_analysis": 41.57,
        "filter_values": {"rsi": 41.57},
        "divergence_parameters": {"rsi_period": 14},
        "divergence_events": [
            {
                "direction": "bearish",
                "start_time": "2025-08-10T17:00:00+00:00",
                "end_time": "2025-08-28T17:00:00+00:00",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [
                    {
                        "indicator": "RSI",
                        "start_value": 89.46,
                        "end_value": 76.54,
                        "rsi_context": context,
                    }
                ],
            }
        ],
    }
    claims = [
        TechnicalClaim(claim_type="rsi_bearish_divergence", original_text="phân kỳ âm"),
        TechnicalClaim(
            claim_type="rsi_overbought", original_text="RSI quá mua tại thời điểm phân tích"
        ),
        TechnicalClaim(
            claim_type="historical_rsi_overbought",
            original_text="RSI quá mua trong quá trình phân kỳ",
            temporal_scope=ClaimTemporalScope(kind="divergence"),
        ),
        TechnicalClaim(
            claim_type="rsi_oversold", original_text="RSI quá bán tại thời điểm phân tích"
        ),
        TechnicalClaim(
            claim_type="historical_rsi_oversold",
            original_text="RSI quá bán trong quá trình phân kỳ",
            temporal_scope=ClaimTemporalScope(kind="divergence"),
        ),
        TechnicalClaim(
            claim_type="historical_rsi_overbought",
            original_text="RSI quá mua trong quá trình phân kỳ",
        ),
        TechnicalClaim(
            claim_type="historical_rsi_overbought",
            original_text="RSI quá mua tháng 1/2025",
            temporal_scope=ClaimTemporalScope(
                kind="explicit",
                start=datetime(2025, 1, 1, tzinfo=UTC),
                end=datetime(2025, 2, 1, tzinfo=UTC),
            ),
        ),
    ]
    verified = TechnicalVerificationService(60, 70, 30).verify(claims, snapshot, "technical:test")
    statuses = [row.verification_status for row in verified]
    assert statuses[0] == "VERIFIED"  # divergence exists even though RSI at analysis is 41.57
    assert statuses[1] == "CONTRADICTED"  # point-in-time overbought is false
    assert statuses[2] == "VERIFIED"  # historical overbought during formation is true
    assert statuses[3] == "CONTRADICTED"  # point-in-time oversold is false
    assert statuses[4] == "NOT_VERIFIABLE"  # no bullish divergence for an oversold claim
    assert statuses[5] == "VERIFIED"  # historical wording can anchor to the detected divergence
    assert statuses[6] == "NOT_VERIFIABLE"  # explicit scope has no matching event


def test_bullish_divergence_with_historical_oversold():
    as_of = datetime(2025, 6, 5, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "rsi_at_analysis": 46.0,
        "filter_values": {"rsi": 46.0},
        "divergence_events": [
            {
                "direction": "bullish",
                "start_time": "2025-04-01T17:00:00+00:00",
                "end_time": "2025-04-20T17:00:00+00:00",
                "confirmed_at": "2025-05-10T17:00:00+00:00",
                "oscillators": [
                    {
                        "indicator": "RSI",
                        "start_value": 22.0,
                        "end_value": 27.0,
                        "rsi_context": {
                            "min": 22.0,
                            "max": 48.0,
                            "rsi_min_during_window": 22.0,
                            "rsi_max_during_window": 48.0,
                            "oversold_seen": True,
                            "overbought_seen": False,
                        },
                    }
                ],
            }
        ],
    }
    claims = [
        TechnicalClaim(claim_type="rsi_oversold", original_text="RSI quá bán hiện tại"),
        TechnicalClaim(
            claim_type="historical_rsi_oversold",
            original_text="RSI quá bán trong quá trình phân kỳ",
            temporal_scope=ClaimTemporalScope(kind="divergence"),
        ),
    ]
    verified = TechnicalVerificationService(60, 70, 30).verify(claims, snapshot, "technical:test")
    assert verified[0].verification_status == "CONTRADICTED"
    assert verified[1].verification_status == "VERIFIED"


def test_divergence_does_not_require_rsi_extreme():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "rsi_at_analysis": 50.0,
        "filter_values": {"rsi": 50.0},
        "divergence_events": [
            {
                "direction": "bearish",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [
                    {
                        "indicator": "RSI",
                        "rsi_context": {
                            "min": 45.0,
                            "max": 62.0,
                            "oversold_seen": False,
                            "overbought_seen": False,
                        },
                    }
                ],
            }
        ],
    }
    row = TechnicalVerificationService(60, 70, 30).verify(
        [TechnicalClaim(claim_type="rsi_bearish_divergence", original_text="phân kỳ âm")],
        snapshot,
        "technical:test",
    )[0]
    assert row.verification_status == "VERIFIED"


def test_rsi_thresholds_are_configurable():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 72.0},
        "rsi_at_analysis": 72.0,
        "divergence_events": [],
    }
    claim = TechnicalClaim(claim_type="rsi_overbought", original_text="RSI quá mua")
    default = TechnicalVerificationService(60, 70, 30).verify([claim], snapshot, "technical:test")[
        0
    ]
    strict = TechnicalVerificationService(60, 75, 25).verify([claim], snapshot, "technical:test")[0]
    assert default.verification_status == "VERIFIED"
    assert strict.verification_status == "CONTRADICTED"


def test_ambiguous_historical_rsi_claim_is_not_forced_to_analysis_timestamp():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 41.57},
        "rsi_at_analysis": 41.57,
        "divergence_events": [],
    }
    point = TechnicalClaim(
        claim_type="rsi_overbought",
        original_text="RSI quá mua trong quá trình phân kỳ",
    )
    historical = TechnicalClaim(
        claim_type="historical_rsi_overbought",
        original_text="RSI quá mua trong quá trình phân kỳ",
    )
    verified = TechnicalVerificationService(60, 70, 30).verify(
        [point, historical], snapshot, "technical:test"
    )
    assert verified[0].verification_status == "NOT_VERIFIABLE"
    assert verified[1].verification_status == "NOT_VERIFIABLE"


def test_divergence_scope_with_explicit_dates_uses_historical_window():
    as_of = datetime(2026, 11, 18, 8, 30, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 43.8},
        "divergence_parameters": {"rsi_period": 14},
        "divergence_events": [
            {
                "direction": "bearish",
                "start_time": "2025-08-10T17:00:00+00:00",
                "end_time": "2025-08-28T17:00:00+00:00",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [{"indicator": "RSI"}],
            }
        ],
    }
    claim = TechnicalClaim(
        claim_type="rsi_bearish_divergence",
        original_text="phân kỳ âm trong giai đoạn 8-9/2025",
        temporal_scope=ClaimTemporalScope(
            kind="divergence",
            start=datetime(2025, 8, 1, tzinfo=UTC),
            end=datetime(2025, 9, 30, 23, 59, 59, tzinfo=UTC),
        ),
    )
    row = TechnicalVerificationService(60).verify([claim], snapshot, "technical:test")[0]
    assert row.verification_status == "VERIFIED"


def test_divergence_text_month_range_infers_explicit_scope():
    as_of = datetime(2026, 11, 18, 8, 30, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 43.8},
        "divergence_events": [
            {
                "direction": "bearish",
                "start_time": "2025-08-10T17:00:00+00:00",
                "end_time": "2025-08-28T17:00:00+00:00",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
                "oscillators": [{"indicator": "RSI"}],
            }
        ],
    }
    claim = TechnicalClaim(
        claim_type="rsi_bearish_divergence",
        original_text="kiểm tra phân kỳ âm trong giai đoạn 8-9/2025",
        temporal_scope=ClaimTemporalScope(kind="divergence"),
    )
    row = TechnicalVerificationService(60).verify([claim], snapshot, "technical:test")[0]
    assert row.verification_status == "VERIFIED"


def test_divergence_outside_lookback_is_insufficient():
    as_of = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    snapshot = {
        "status": "ok",
        "as_of": as_of.isoformat(),
        "filter_values": {"rsi": 41.57},
        "divergence_events": [
            {
                "direction": "bearish",
                "confirmed_at": "2025-06-01T17:00:00+00:00",
                "oscillators": [{"indicator": "RSI"}],
            }
        ],
    }
    row = TechnicalVerificationService(60).verify(
        [claim("rsi_bearish_divergence")], snapshot, "technical:test"
    )[0]
    assert row.verification_status == "INSUFFICIENT_EVIDENCE"


def test_future_pivot_and_parameter_mismatch_never_verified():
    c = claim("rsi_bullish_divergence")
    snapshot = {
        "status": "ok",
        "as_of": AS_OF.isoformat(),
        "divergence_events": [
            {
                "direction": "bullish",
                "confirmed_at": "2026-01-16T08:00:00+00:00",
                "oscillators": [{"indicator": "RSI"}],
            }
        ],
    }
    assert (
        TechnicalVerificationService().verify([c], snapshot, "id")[0].verification_status
        == "INSUFFICIENT_EVIDENCE"
    )
    c.extracted_parameters = {"rsi_period": 7}
    assert (
        TechnicalVerificationService().verify([c], snapshot, "id")[0].verification_status
        == "INSUFFICIENT_EVIDENCE"
    )


async def test_tavily_sdk_search_extract_format(settings):
    client = SimpleNamespace(
        search=AsyncMock(return_value={"results": [{"url": "https://hnx.vn/a"}]}),
        extract=AsyncMock(return_value={"results": [{"raw_content": "disclosure"}]}),
    )
    adapter = TavilySearchAdapter(settings, client)
    assert len(await adapter.search("KDH", AS_OF)) == 1
    assert client.search.call_args.kwargs["end_date"] == "2026-01-15"
    assert client.search.call_args.kwargs["include_answer"] is False
    window_start = AS_OF - timedelta(days=5)
    await adapter.search("KDH", AS_OF, start_at=window_start, end_at=AS_OF)
    assert client.search.call_args.kwargs["start_date"] == "2026-01-10"
    assert client.search.call_args.kwargs["end_date"] == "2026-01-15"
    assert (await adapter.extract("https://hnx.vn/a"))["raw_content"] == "disclosure"


async def test_firecrawl_uses_verified_async_instance_scrape(settings):
    client = SimpleNamespace(
        scrape=AsyncMock(
            return_value={
                "markdown": "report",
                "metadata": {
                    "published_time": "2026-01-14T03:00:00Z",
                    "modified_time": "2026-01-14T03:00:00Z",
                },
            }
        ),
        search=AsyncMock(
            return_value={
                "news": [
                    {
                        "url": "https://hnx.vn/a",
                        "title": "KDH news",
                        "snippet": "KDH disclosure",
                        "date": "2026-01-14T03:00:00Z",
                    }
                ],
                "web": [{"url": "https://hnx.vn/b", "title": "KDH web"}],
            }
        ),
    )
    adapter = FirecrawlExtractionAdapter(settings, client)
    assert (await adapter.extract("https://hnx.vn/a"))["raw_content"] == "report"
    rows = await adapter.search("KDH", AS_OF)
    assert len(rows) == 2
    assert rows[0]["published_date"] == "2026-01-14T03:00:00Z"
    assert rows[0]["content"] == "KDH disclosure"
    assert client.scrape.call_args.kwargs["formats"] == ["markdown"]
    window_start = AS_OF - timedelta(days=5)
    await adapter.search("KDH", AS_OF, start_at=window_start, end_at=AS_OF)
    query = client.search.call_args.kwargs["query"]
    assert "after:2026-01-10" in query and "before:2026-01-15" in query


async def test_historical_news_dedup_fallback_cache_and_future_exclusion(settings):
    good = {
        "url": "https://hnx.vn/announcement?utm_source=x",
        "title": "KDH disclosure",
        "score": 0.9,
        "published_date": "2026-01-14T03:00:00Z",
    }
    primary = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                good,
                {**good, "url": "https://hnx.vn/announcement"},
                {**good, "url": "https://hnx.vn/future", "published_date": "2026-01-16T03:00:00Z"},
                {**good, "url": "https://hnx.vn/unknown", "published_date": None},
            ]
        ),
        extract=AsyncMock(side_effect=RuntimeError("mock")),
    )
    fallback = SimpleNamespace(
        extract=AsyncMock(
            return_value={"raw_content": "KDH disclosure", "modified_time": "2026-01-14T04:00:00Z"}
        )
    )
    news = NewsEnrichmentService(settings, primary, fallback, MemoryCache())
    articles, warnings = await news.enrich("KDH", AS_OF)
    assert len(articles) == 1 and articles[0].source_type == "official"
    assert articles[0].canonical_url == "https://hnx.vn/announcement"
    assert not warnings
    await news.enrich("KDH", AS_OF)
    primary.search.assert_awaited_once()
    fallback.extract.assert_awaited_once()


@pytest.mark.parametrize(
    "metadata", [{"modified_time": "2026-02-01T03:00:00Z"}, {"modified_time": "bad date"}]
)
async def test_news_updated_after_asof_or_invalid_date_is_excluded(settings, metadata):
    primary = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "url": "https://hnx.vn/a",
                    "title": "KDH disclosure",
                    "score": 0.9,
                    "published_date": "2026-01-14T03:00:00Z",
                }
            ]
        ),
        extract=AsyncMock(return_value={"raw_content": "updated content", **metadata}),
    )
    articles, _ = await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH", AS_OF
    )
    assert articles == []
    primary.extract.assert_awaited_once()


async def test_news_unknown_revision_is_included_with_warning(settings):
    primary = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "url": "https://hnx.vn/a",
                    "title": "KDH disclosure",
                    "score": 0.9,
                    "published_date": "2026-01-14T03:00:00Z",
                }
            ]
        ),
        extract=AsyncMock(return_value={"raw_content": "historical KDH disclosure"}),
    )
    articles, warnings = await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH", AS_OF
    )
    assert len(articles) == 1
    assert "news_revision_unknown" in warnings


async def test_news_unknown_revision_can_still_be_strictly_excluded(settings):
    settings.rag_news_allow_unknown_revision = False
    primary = SimpleNamespace(
        search=AsyncMock(
            return_value=[
                {
                    "url": "https://hnx.vn/a",
                    "title": "KDH disclosure",
                    "score": 0.9,
                    "published_date": "2026-01-14T03:00:00Z",
                }
            ]
        ),
        extract=AsyncMock(return_value={"raw_content": "historical KDH disclosure"}),
    )
    articles, warnings = await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH", AS_OF
    )
    assert articles == []
    assert "historical_content_revision_unconfirmed" in warnings


def test_signal_window_uses_earliest_divergence_and_ignores_future():
    settings = SimpleNamespace(rag_news_signal_window_enabled=True, rag_news_signal_buffer_days=14)
    analysis_at = datetime(2025, 10, 20, 9, 15, tzinfo=UTC)
    technical = {
        "status": "ok",
        "divergence_events": [
            {
                "start_time": "2025-08-10T17:00:00+00:00",
                "confirmed_at": "2025-08-21T17:00:00+00:00",
            },
            {
                "start_time": "2025-09-01T17:00:00+00:00",
                "confirmed_at": "2025-09-11T17:00:00+00:00",
            },
            {
                "start_time": "2025-11-01T17:00:00+00:00",
                "confirmed_at": "2025-11-10T17:00:00+00:00",
            },
        ],
    }
    assert signal_window_start(technical, analysis_at, settings) == datetime(
        2025, 7, 27, 17, 0, tzinfo=UTC
    )
    settings.rag_news_signal_window_enabled = False
    assert signal_window_start(technical, analysis_at, settings) is None
    assert signal_window_start({"status": "insufficient_history"}, analysis_at, settings) is None


async def test_news_signal_window_forwards_dates_and_filters_old_items(settings):
    old = {
        "url": "https://hnx.vn/old",
        "title": "KDH old disclosure",
        "score": 0.9,
        "published_date": "2025-11-01T03:00:00Z",
    }
    recent = {
        "url": "https://hnx.vn/recent",
        "title": "KDH recent disclosure",
        "score": 0.9,
        "published_date": "2026-01-14T03:00:00Z",
    }
    primary = SimpleNamespace(
        search=AsyncMock(return_value=[old, recent]),
        extract=AsyncMock(
            return_value={
                "raw_content": "KDH disclosure",
                "modified_time": "2026-01-14T04:00:00Z",
            }
        ),
    )
    window_start = AS_OF - timedelta(days=60)
    articles, _ = await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH", AS_OF, window_start=window_start
    )
    assert [item.canonical_url for item in articles] == [recent["url"]]
    call = primary.search.await_args
    assert call.kwargs["start_at"] == window_start
    assert call.kwargs["end_at"] == AS_OF


async def test_news_query_includes_company_aliases(settings):
    primary = SimpleNamespace(search=AsyncMock(return_value=[]), extract=AsyncMock())
    await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH",
        AS_OF,
        company="Khang Dien",
        sector="Bất động sản",
        aliases=["KDH Group", "Khang Dien House"],
    )
    query = primary.search.await_args.args[0]
    assert "Khang Dien" in query
    assert "KDH Group" in query
    assert "Khang Dien House" in query


async def test_news_failure_is_optional(settings):
    primary = SimpleNamespace(search=AsyncMock(side_effect=TimeoutError()))
    articles, warnings = await NewsEnrichmentService(settings, primary, None, MemoryCache()).enrich(
        "KDH", AS_OF
    )
    assert not articles and warnings[0].startswith("news_search_failed")


async def test_firecrawl_search_fallback_when_primary_fails(settings):
    good = {
        "url": "https://hnx.vn/fallback-announcement",
        "title": "KDH fallback disclosure",
        "score": 0.8,
        "published_date": "2026-01-14T03:00:00Z",
    }
    primary = SimpleNamespace(
        search=AsyncMock(side_effect=TimeoutError()),
        extract=AsyncMock(
            return_value={
                "raw_content": "KDH fallback disclosure",
                "modified_time": "2026-01-14T04:00:00Z",
            }
        ),
    )
    fallback = SimpleNamespace(
        search=AsyncMock(return_value=[good]),
        extract=AsyncMock(return_value={"raw_content": "unused"}),
    )
    articles, warnings = await NewsEnrichmentService(
        settings, primary, fallback, MemoryCache()
    ).enrich("KDH", AS_OF)
    assert [article.canonical_url for article in articles] == [good["url"]]
    assert "news_fallback_search_used" in warnings
    fallback.search.assert_awaited_once()
    fallback.extract.assert_not_awaited()


def test_publication_timezone_and_url_safety():
    assert publication_time("2026-01-15T12:00:00") is None
    assert publication_time("unknown") is None
    assert publication_time("2026-01-15") > AS_OF
    assert canonical_url("https://hnx.vn/a?utm_campaign=b#fragment") == "https://hnx.vn/a"
    for url in (
        "http://localhost/a",
        "http://127.0.0.1/a",
        "file:///etc/passwd",
        "https://user:secret@example.com/a",
    ):
        with pytest.raises(ValueError):
            canonical_url(url)


def test_normalization_preserves_conflict_and_excludes_outcome():
    c = claim("bullish_engulfing")
    c.verification_status = VerificationStatus.CONTRADICTED
    # Pydantic validation ensures the actual production object is typed.
    c = TechnicalClaim.model_validate(c.model_dump())
    text, summary = normalize_analysis(
        "Thesis gốc",
        {"symbol": "KDH", "analysis_at": AS_OF.isoformat()},
        VisionObservations(description="Observed only", uncertainties=["RSI unreadable"]),
        [c],
        {"filter_values": {"rsi": 58}, "outcome": {"future_return": 999}},
        [],
    )
    assert "Thesis gốc" in text and "CONTRADICTED" in text and "RSI unreadable" in text
    assert "future_return" not in text and summary["CONTRADICTED"] == 1


async def test_rerank_disabled_makes_no_requests(settings):
    provider = SimpleNamespace(settings=settings, client=SimpleNamespace(post=AsyncMock()))
    assert len(await OpenRouterReranker(provider).rerank("q", ["a", "b"], 1)) == 1
    provider.client.post.assert_not_awaited()


async def test_rerank_request_and_index_mapping(settings, respx_mock):
    settings.rag_rerank_enabled = True
    provider = OpenRouterAdapter(settings)
    route = respx_mock.post("https://openrouter.ai/api/v1/rerank").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.5},
                ]
            },
        )
    )
    rows = await OpenRouterReranker(provider).rerank("q", ["a", "b"], 2)
    assert rows[0]["index"] == 1
    assert json.loads(route.calls[0].request.content)["model"] == settings.rag_rerank_model
    await provider.close()


@pytest.mark.parametrize(
    "rows",
    [
        [{"index": 99, "relevance_score": 0.9}],
        [{"index": 0, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.8}],
        [],
    ],
)
async def test_rerank_bad_mapping_rejected(settings, rows):
    settings.rag_rerank_enabled = True
    provider = SimpleNamespace(
        settings=settings, client=SimpleNamespace(post=AsyncMock(return_value={"results": rows}))
    )
    with pytest.raises(UpstreamError):
        await OpenRouterReranker(provider).rerank("q", ["a"], 1)


def test_rrf_fuses_ranks_without_averaging_similarities():
    scores = reciprocal_rank_fusion([["a", "b"], ["b", "a"], ["b"]])
    assert scores[0][0] == "b"
    assert dict(scores)["b"] == pytest.approx(1 / 62 + 2 / 61)


async def test_sql_filters_are_exact_and_space_isolated():
    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    query = AnalysisSearch(
        query="q",
        symbol="KDH",
        timeframe="1D",
        before=AS_OF,
        conditions=[{"field": "rsi", "operator": "gte", "value": 58}],
    )
    await AnalysisRepository(session).search(
        [1, 0, 0], EmbeddingSpace("text-model", 3, "case-text-v2"), query
    )
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "analysis_case_embeddings.space =" in sql
    assert "analysis_case_embeddings.representation_kind =" in sql
    assert "analysis_cases.analyzed_at <" in sql
    assert "analysis_enrichments.technical_snapshot" in sql
    assert "ORDER BY" in sql and "<=>" in sql


async def test_query_image_validation_never_writes_files(tmp_path, settings):
    import base64

    url = "data:image/png;base64," + base64.b64encode(image_bytes()).decode()
    assert await validate_query_image(url, settings) == url
    assert not list(tmp_path.iterdir())
    with pytest.raises(InvalidRequestError):
        await validate_query_image("data:image/png;base64,invalid", settings)


@pytest.fixture
def pipeline(tmp_path, settings, monkeypatch):
    """Real orchestration with remote and SQL boundaries mocked; original image is real PNG."""
    import app.services.rag_enrichment_service as module

    settings.rag_news_enabled = False
    storage = LocalFileStorage(str(tmp_path))
    source = AnalysisCreate(
        symbol="KDH",
        timeframe="D1",
        analyzed_at=AS_OF,
        thesis="KDH phân kỳ dương, engulfing, volume tăng",
    )
    case = SimpleNamespace(
        id=uuid4(),
        symbol=source.symbol,
        timeframe=source.timeframe,
        analyzed_at=source.analyzed_at,
        thesis=source.thesis,
        chart_path=None,
        ingest_status="PENDING",
    )
    state = SimpleNamespace(
        original_input=source.model_dump(mode="json"),
        observations={},
        resolved_context={},
        technical_snapshot={},
        normalized_text="",
        verification_summary={},
        diagnostics={},
    )
    evidence = SimpleNamespace(
        prefetch=AsyncMock(),
        ensure_state=AsyncMock(return_value=state),
        save_claims=AsyncMock(),
        link_news=AsyncMock(),
    )
    monkeypatch.setattr(module, "EvidenceRepository", lambda _: evidence)
    embeddings = AsyncMock()
    monkeypatch.setattr(AnalysisRepository, "save_embedding", embeddings)
    text = SimpleNamespace(
        space=EmbeddingSpace("text", 3, "case-text-v2"), embed=AsyncMock(return_value=[1, 0, 0])
    )
    multimodal = SimpleNamespace(
        space=EmbeddingSpace("image", 4, "case-image-v2"),
        embed=AsyncMock(return_value=[1, 0, 0, 0]),
    )
    extraction = ExtractionResult(
        observations=VisionObservations(
            symbol_candidates=["KDH"], timeframe="D1", description="Marked chart; RSI unreadable"
        ),
        user_claims=[
            claim("bullish_engulfing", "engulfing"),
            claim("volume_increase", "volume tăng"),
        ],
    )
    runtime = SimpleNamespace(
        settings=settings,
        storage=storage,
        evidence_cache=MemoryCache(),
        provider_semaphore=__import__("asyncio").Semaphore(3),
        vision=SimpleNamespace(extract=AsyncMock(return_value=extraction)),
        technical=SimpleNamespace(
            snapshot=AsyncMock(
                return_value={
                    "status": "ok",
                    "as_of": AS_OF.isoformat(),
                    "filter_values": {"rsi": 58, "rvol": 1.8},
                    "bullish_engulfing": True,
                    "engine_version": "test-v1",
                }
            )
        ),
        news=SimpleNamespace(enrich=AsyncMock(return_value=([], []))),
        case_text_embedding=text,
        case_multimodal_embedding=multimodal,
        cases=SimpleNamespace(ingest_image=AsyncMock(), ingest_text=AsyncMock()),
    )
    session = SimpleNamespace(commit=AsyncMock(), execute=AsyncMock(), add=MagicMock())
    job = SimpleNamespace(
        id=uuid4(),
        text=source.model_dump_json(),
        diagnostics={},
        status="PENDING",
        stage="PENDING",
        progress=0,
        lease_until=None,
    )
    return SimpleNamespace(
        service=RagEnrichmentService(runtime, session),
        runtime=runtime,
        source=source,
        case=case,
        state=state,
        evidence=evidence,
        embeddings=embeddings,
        job=job,
    )


async def test_pipeline_dual_embeddings_original_preservation_and_retry_cache(pipeline):
    p = pipeline
    p.case.chart_path = await p.runtime.storage.save(image_bytes(), ".png")
    await p.service.process(p.job, p.case)
    assert p.job.status == "COMPLETED"
    assert p.case.thesis == p.source.thesis
    assert p.state.verification_summary == {"VERIFIED": 2}
    assert p.embeddings.await_count == 2
    assert {call.kwargs["kind"] for call in p.embeddings.call_args_list} == {"text", "multimodal"}
    assert p.runtime.case_text_embedding.embed.call_args.args[1] is None
    assert p.runtime.case_multimodal_embedding.embed.call_args.args[1].startswith("data:image/png")
    await p.service.process(p.job, p.case)
    p.runtime.vision.extract.assert_awaited_once()
    p.runtime.case_text_embedding.embed.assert_awaited_once()
    p.runtime.case_multimodal_embedding.embed.assert_awaited_once()


async def test_embedding_failure_is_partial(pipeline):
    p = pipeline
    p.case.chart_path = await p.runtime.storage.save(image_bytes(), ".png")
    p.runtime.case_multimodal_embedding.embed.side_effect = UpstreamError("dimension mismatch")
    await p.service.process(p.job, p.case)
    assert p.job.status == "PARTIAL"
    assert p.case.thesis == p.source.thesis
    assert p.embeddings.await_count == 1
    assert any(w.startswith("embedding_failed:") for w in p.job.diagnostics["warnings"])


async def test_pipeline_passes_signal_window_to_news(pipeline):
    p = pipeline
    p.runtime.technical.snapshot.return_value = {
        "status": "ok",
        "as_of": AS_OF.isoformat(),
        "filter_values": {"rsi": 58, "rvol": 1.8},
        "divergence_events": [
            {"start_time": "2026-01-01T17:00:00+00:00", "confirmed_at": AS_OF.isoformat()}
        ],
        "engine_version": "test-v1",
    }
    p.runtime.news.enrich.return_value = ([], [])
    await p.service.process(p.job, p.case)
    assert p.runtime.news.enrich.await_args.kwargs["window_start"] == datetime(
        2025, 12, 18, 17, 0, tzinfo=UTC
    )


async def test_technical_insufficient_history_is_partial(pipeline):
    p = pipeline
    p.runtime.technical.snapshot.return_value = {
        "status": "insufficient_history",
        "reason": "Fewer than 60 closed candles.",
    }
    await p.service.process(p.job, p.case)
    assert p.job.status == "PARTIAL"
    assert "technical_insufficient_history" in p.job.diagnostics["warnings"]


async def test_pipeline_records_non_overlapping_timings_and_provider_usage(pipeline):
    p = pipeline
    await p.service.process(p.job, p.case)
    timings = p.job.diagnostics["timings"]
    assert timings["total_wall_clock_seconds"] >= 0
    assert timings["case_graph_index_seconds"] >= 0
    assert "text_embedding_seconds" in timings
    assert "provider_usage" in p.job.diagnostics


async def test_news_failure_is_non_blocking_and_completed(pipeline):
    p = pipeline
    p.runtime.news.enrich.side_effect = TimeoutError()
    await p.service.process(p.job, p.case)
    assert p.job.status == "COMPLETED"
    assert p.job.diagnostics["warnings"] == []
    assert p.job.diagnostics["news_warnings"] == ["news_provider_failed"]


async def test_missing_news_is_non_blocking_and_completed(pipeline):
    p = pipeline
    p.runtime.news.enrich.return_value = ([], ["no_dated_historical_sources"])
    await p.service.process(p.job, p.case)
    assert p.job.status == "COMPLETED"
    assert p.job.diagnostics["news_warnings"] == ["no_dated_historical_sources"]


async def test_ambiguity_stops_before_paid_embeddings(pipeline):
    p = pipeline
    p.case.symbol = None
    p.runtime.vision.extract.return_value = ExtractionResult(
        observations=VisionObservations(symbol_candidates=["KDH", "FPT"], timeframe="D1")
    )
    await p.service.process(p.job, p.case)
    assert p.job.status == "NEEDS_REVIEW"
    p.runtime.technical.snapshot.assert_not_awaited()
    p.runtime.case_text_embedding.embed.assert_not_awaited()


async def test_reused_vision_cache_claim_ids_are_case_scoped(pipeline):
    p = pipeline
    await p.service.process(p.job, p.case)
    first_ids = [c.claim_id for c in p.evidence.save_claims.call_args.args[1]]
    p.case.id = uuid4()
    await p.service.process(p.job, p.case)
    second_ids = [c.claim_id for c in p.evidence.save_claims.call_args.args[1]]
    assert not set(first_ids) & set(second_ids)
    p.runtime.vision.extract.assert_awaited_once()


async def test_grounded_answer_rejects_invented_citations(settings):
    runtime = SimpleNamespace(
        provider=SimpleNamespace(
            complete=AsyncMock(
                return_value=json.dumps(
                    {"answer": "Made up [unknown]", "citation_ids": ["unknown"]}
                )
            )
        )
    )
    service = RagQueryService(runtime, MagicMock())
    service.retrieve = AsyncMock(
        return_value=(
            [
                {
                    "id": "case-id",
                    "case": object(),
                    "text": "Unverified thesis",
                    "sources": [{"evidence_id": "case:1"}],
                    "verification_summary": {},
                }
            ],
            {},
            {},
        )
    )
    with pytest.raises(UpstreamError):
        await service.answer(AnalysisSearch(query="what?"))
    runtime.provider.complete.return_value = json.dumps(
        {"answer": "Unverified thesis [case:1]", "citation_ids": ["case:1"]}
    )
    result = await service.answer(AnalysisSearch(query="what?"))
    assert result["sources"] == [{"evidence_id": "case:1"}]


async def test_multimodal_retrieval_uses_both_spaces_and_filters_graph(pipeline, monkeypatch):
    import app.services.rag_query_service as module

    p = pipeline
    runtime = p.runtime
    runtime.settings.rag_case_graph_enabled = True
    runtime.reranker = SimpleNamespace(rerank=AsyncMock())
    runtime.cases.retrieve = AsyncMock(
        return_value={
            "chunks": [{"file_path": "analysis://" + str(uuid4()), "content": "future case"}]
        }
    )
    runtime.knowledge = SimpleNamespace(retrieve=AsyncMock())
    case = SimpleNamespace(
        id=p.case.id,
        symbol="KDH",
        timeframe="1D",
        analyzed_at=AS_OF,
        thesis="Original",
        indicator_snapshot={},
        market_context={},
        conclusion="",
        source="user",
        chart_path=None,
        ingest_status="COMPLETED",
        created_at=AS_OF,
        updated_at=AS_OF,
    )
    repository = SimpleNamespace(
        search=AsyncMock(return_value=[(case, 0.9)]),
        structured_search=AsyncMock(return_value=[case]),
        eligible=AsyncMock(return_value=None),
    )
    evidence = SimpleNamespace(
        prefetch=AsyncMock(),
        state=AsyncMock(
            return_value=SimpleNamespace(
                normalized_text="Normalized", verification_summary={"VERIFIED": 1}
            )
        ),
        sources=AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(module, "AnalysisRepository", lambda _: repository)
    monkeypatch.setattr(module, "EvidenceRepository", lambda _: evidence)
    result = await module.RagQueryService(runtime, MagicMock()).search(
        AnalysisSearch(
            query="KDH",
            image="in-memory-test-double",
            symbol="KDH",
            before=datetime(2026, 2, 1, tzinfo=UTC),
        )
    )
    assert len(result.data) == 1
    assert result.case_ids == [case.id]
    assert result.verification_summary == {"VERIFIED": 1}
    assert repository.search.await_count == 2
    assert set(result.data[0].channel_similarities) == {"case-text-v2", "case-image-v2"}
    runtime.knowledge.retrieve.assert_not_awaited()
    repository.eligible.assert_awaited_once()
    runtime.reranker.rerank.assert_not_awaited()
    assert "future case" not in str(result.model_dump())


@pytest.mark.parametrize("attempts,status", [(1, "PENDING"), (3, "FAILED")])
async def test_worker_failure_is_bounded_retry_and_logs_no_secret(
    pipeline, monkeypatch, caplog, attempts, status
):
    from contextlib import asynccontextmanager

    import app.services.rag_enrichment_service as module
    import app.workers.rag_worker as worker

    p = pipeline
    p.job.case_id = p.case.id
    p.job.namespace = "cases"
    p.job.attempts = attempts
    session = SimpleNamespace(rollback=AsyncMock(), commit=AsyncMock())

    @asynccontextmanager
    async def factory():
        yield session

    jobs = SimpleNamespace(claim=AsyncMock(return_value=p.job), get=AsyncMock(return_value=p.job))
    monkeypatch.setattr(worker, "SessionFactory", factory)
    monkeypatch.setattr(worker, "RagRepository", lambda _: jobs)
    monkeypatch.setattr(
        worker, "AnalysisRepository", lambda _: SimpleNamespace(get=AsyncMock(return_value=p.case))
    )
    monkeypatch.setattr(
        module.RagEnrichmentService,
        "process",
        AsyncMock(side_effect=UpstreamError("secret-api-key-do-not-log")),
    )
    p.runtime.knowledge = p.runtime.cases
    p.runtime.mirror = None
    assert await worker.process_one(p.runtime)
    assert p.job.status == status and p.case.ingest_status == status
    assert p.job.error_code == "UpstreamError"
    assert "secret-api-key-do-not-log" not in caplog.text


async def test_claim_lease_recovers_every_inflight_stage():
    from app.repositories.rag_repository import RagRepository

    session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
        commit=AsyncMock(),
    )
    assert await RagRepository(session).claim(600) is None
    statement = session.execute.call_args.args[0]
    sql = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    for stage in ("EXTRACTING", "ENRICHING", "VERIFYING", "NORMALIZING", "INDEXING"):
        assert stage in sql
    assert "SKIP LOCKED" in sql


async def test_analysis_ingestion_idempotent_original_text_chart_metadata(tmp_path):
    from app.services.analysis_memory_service import AnalysisMemoryService

    stored = {}
    queued = {}

    async def add(case):
        stored[case.id] = case

    async def get(identifier, *args, **kwargs):
        return stored.get(identifier)

    async def enqueue(**values):
        fingerprint = values["fingerprint"]
        if fingerprint not in queued:
            queued[fingerprint] = SimpleNamespace(**values)
        return queued[fingerprint]

    session = SimpleNamespace(commit=AsyncMock(), get=AsyncMock(return_value=None))
    repository = SimpleNamespace(
        session=session,
        lock_fingerprint=AsyncMock(),
        add=AsyncMock(side_effect=add),
        get=AsyncMock(side_effect=get),
    )
    service = AnalysisMemoryService(
        repository,
        SimpleNamespace(enqueue=AsyncMock(side_effect=enqueue)),
        SimpleNamespace(),
        LocalFileStorage(str(tmp_path)),
    )
    payload = AnalysisCreate(
        symbol="KDH",
        timeframe="D1",
        analysis_at=AS_OF,
        thesis="  Thesis gốc\nkhông sửa.  ",
        indicator_snapshot={"user_rsi": 58},
    )
    first = await service.ingest_analysis_case(payload, (image_bytes(), ".png"))
    second = await service.ingest_analysis_case(payload, (image_bytes(), ".png"))
    assert first.id == second.id and first.job_id == second.job_id
    assert first.thesis == payload.thesis and first.indicator_snapshot == payload.indicator_snapshot
    assert len(stored) == len(queued) == 1
    assert await service.storage.read(first.chart_path) == image_bytes()


async def test_legacy_image_search_never_silently_ignores_image():
    from app.core.exceptions import InvalidRequestError
    from app.services.analysis_memory_service import AnalysisMemoryService

    query = AnalysisSearch(image="data:image/png;base64,abc")
    repository = SimpleNamespace(session=MagicMock(), search=AsyncMock(return_value=[]))
    text_only = SimpleNamespace(
        space=EmbeddingSpace("text", 3), multimodal=False, embed=AsyncMock()
    )
    service = AnalysisMemoryService(repository, SimpleNamespace(), text_only, MagicMock())
    with pytest.raises(InvalidRequestError):
        await service.search_similar_analysis_cases(query)
    text_only.embed.assert_not_awaited()

    multimodal = SimpleNamespace(
        space=EmbeddingSpace("image", 3, "case-image-v2"),
        multimodal=True,
        embed=AsyncMock(return_value=[1, 0, 0]),
    )
    service = AnalysisMemoryService(repository, SimpleNamespace(), multimodal, MagicMock())
    assert await service.search_similar_analysis_cases(query) == []
    multimodal.embed.assert_awaited_once_with("chart similarity", query.image)


async def test_multimodal_api_file_is_query_only(client):
    from app.core.rag_runtime import get_query_service
    from app.main import app

    response = {
        "answer": "No evidence",
        "case_ids": [],
        "sources": [],
        "verification_summary": {},
        "retrieval_metadata": {},
        "rerank_metadata": {},
    }
    service = SimpleNamespace(answer=AsyncMock(return_value=response))
    app.dependency_overrides[get_query_service] = lambda: service
    try:
        result = await client.post(
            "/api/v1/rag/query/multimodal",
            data={"metadata": "{}"},
            files={"image": ("chart.png", image_bytes(), "image/png")},
        )
        assert result.status_code == 200
        assert service.answer.call_args.args[0].image.startswith("data:image/png;base64,")
        result = await client.post(
            "/api/v1/rag/query/multimodal",
            data={"metadata": "{}"},
            files={"image": ("bad.png", b"not-image", "image/png")},
        )
        assert result.status_code == 400
        service.answer.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_query_service, None)


async def test_technical_provider_uses_existing_engine_and_historical_repo(monkeypatch):
    from contextlib import asynccontextmanager
    from pathlib import Path

    import app.adapters.technical_evidence_adapter as module

    rows = json.loads(Path("tests/fixtures/dpg_divergence_ohlcv.json").read_text())["candles"]
    candles = [
        SimpleNamespace(
            candle_open_time=datetime.fromisoformat(r["time"].replace("Z", "+00:00")),
            timeframe="1D",
            **{k: r[k] for k in ("open", "high", "low", "close", "volume")},
        )
        for r in rows
    ]
    latest = candles[-1].candle_open_time
    # At 14:59 VN only preceding daily bar is known, even if DB has final future values.
    from zoneinfo import ZoneInfo

    as_of = latest.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).replace(hour=14, minute=59)
    session = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(exchange="HOSE")))

    @asynccontextmanager
    async def factory():
        yield session

    repository = SimpleNamespace(get_candles=AsyncMock(return_value=candles))
    monkeypatch.setattr(module, "MarketRepository", lambda _: repository)
    adapter = module.TechnicalEvidenceAdapter(Settings(_env_file=None), factory)
    snapshot = await adapter.snapshot("DPG", "1D", as_of, price_basis="RAW", exchange="HOSE")
    assert snapshot["status"] == "ok" and snapshot["bars"] == len(candles) - 1
    assert snapshot["as_of"] == candles[-2].candle_open_time.isoformat()
    assert snapshot["engulfing_function"] == "CDLENGULFING"
    assert snapshot["rsi_at_analysis"] == snapshot["filter_values"]["rsi"]
    assert snapshot["engine_version"].startswith("ind-2")
    assert repository.get_candles.call_args.kwargs["end"] == as_of
    assert repository.get_candles.call_args.kwargs["price_basis"] == "RAW"


async def test_missing_market_data_uses_mocked_existing_fcdata_ingestion(monkeypatch):
    from contextlib import asynccontextmanager

    import app.adapters.technical_evidence_adapter as module
    import app.fcdata.client as fcdata
    import app.services.ingestion_service as ingestion

    @asynccontextmanager
    async def factory():
        yield SimpleNamespace()

    repository = SimpleNamespace(get_candles=AsyncMock(return_value=[]))
    monkeypatch.setattr(module, "MarketRepository", lambda _: repository)
    backfill = AsyncMock()
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock()
    monkeypatch.setattr(fcdata, "FcDataClient", lambda _: client)
    monkeypatch.setattr(
        ingestion, "IngestionService", lambda **kwargs: SimpleNamespace(backfill_symbol=backfill)
    )
    adapter = module.TechnicalEvidenceAdapter(
        Settings(_env_file=None, rag_fetch_missing_market_data=True), factory
    )
    result = await adapter.snapshot("KDH", "1D", AS_OF, price_basis="RAW")
    assert result["status"] == "insufficient_history"
    backfill.assert_awaited_once()
    assert backfill.call_args.kwargs["end"] == AS_OF.date()


def test_migration_has_isolated_model_indexes_and_preserves_source_tables(monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "evidence_migration", "alembic/versions/d3e4f5a6b7c8_evidence_enrichment.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = MagicMock()
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    sql = "\n".join(str(call.args[0]) for call in operations.execute.call_args_list)
    assert "CREATE TABLE analysis_enrichments" in sql
    indexes = [
        call.args[0] for call in operations.execute.call_args_list if "USING hnsw" in call.args[0]
    ]
    assert len(indexes) == 4 and all(
        "space =" in text and "representation_kind" in text for text in indexes
    )
    assert "DROP TABLE" not in sql
    assert all(call.args[0] == "analysis_cases" for call in operations.alter_column.call_args_list)


async def test_graph_only_result_hydrates_its_own_case(pipeline, monkeypatch):
    import app.services.rag_query_service as module

    p = pipeline
    p.runtime.settings.rag_case_graph_enabled = True
    p.case.indicator_snapshot = {}
    p.case.market_context = {}
    p.case.conclusion = ""
    p.case.source = "user"
    p.case.created_at = AS_OF
    p.case.updated_at = AS_OF
    p.case.ingest_status = "COMPLETED"
    repository = SimpleNamespace(
        search=AsyncMock(return_value=[]),
        structured_search=AsyncMock(return_value=[]),
        eligible=AsyncMock(return_value=p.case),
    )
    evidence = SimpleNamespace(
        prefetch=AsyncMock(), state=AsyncMock(return_value=None), sources=AsyncMock(return_value=[])
    )
    monkeypatch.setattr(module, "AnalysisRepository", lambda _: repository)
    monkeypatch.setattr(module, "EvidenceRepository", lambda _: evidence)
    p.runtime.cases.retrieve = AsyncMock(
        return_value={"chunks": [{"file_path": f"analysis://{p.case.id}", "content": "graph hit"}]}
    )
    p.runtime.knowledge = SimpleNamespace(retrieve=AsyncMock())
    result = await module.RagQueryService(p.runtime, MagicMock()).search(
        AnalysisSearch(query="KDH", symbol="KDH")
    )
    assert result.data[0].case.id == p.case.id


@pytest.mark.parametrize("fail_open", [True, False])
async def test_lightrag_swallowed_rerank_error_still_obeys_policy(settings, fail_open):
    from app.adapters.rag_anything_adapter import RagAnythingAdapter

    settings.rag_rerank_enabled = True
    settings.rag_rerank_fail_open = fail_open
    reranker = OpenRouterReranker(
        SimpleNamespace(
            settings=settings, client=SimpleNamespace(post=AsyncMock(side_effect=TimeoutError()))
        )
    )
    adapter = RagAnythingAdapter(settings, MagicMock(), MagicMock())

    async def library_call():
        from contextlib import suppress

        with suppress(UpstreamError):
            await reranker.hook("q", ["a"], 1)  # Installed library swallows failures.
        return {"status": "success"}

    if fail_open:
        _, events = await adapter._with_rerank_diagnostics(library_call())
        assert events[0]["failed"] and events[0]["fail_open"]
    else:
        with pytest.raises(UpstreamError):
            await adapter._with_rerank_diagnostics(library_call())


def test_evaluation_metrics_leave_unmeasured_live_results_null():
    from pathlib import Path

    from tools.evaluate_evidence_rag import evaluate, ranking_metrics

    metrics = ranking_metrics(["wrong", "b", "a"], {"a": 3, "b": 1}, 3)
    assert metrics["recall_at_k"] == 1 and metrics["reciprocal_rank"] == 0.5
    assert 0 < metrics["ndcg_at_k"] < 1
    dataset = json.loads(Path("tests/fixtures/evidence_rag/financial_eval.json").read_text())
    report = evaluate(dataset, [])
    assert report["claim_count"] == 9
    assert report["deterministic_claim_accuracy"] == 1
    assert report["mrr"] is None and report["measured_api_cost_usd"] is None


async def test_joint_extraction_preserves_notes_and_caches_identical_inputs(settings):
    from app.adapters.vision_adapter import CachedVisionExtractor
    from app.schemas.evidence import ExtractionResult, VisionObservations

    values = {}

    async def get(key):
        return values.get(key)

    async def put(key, value):
        values[key] = value

    extractor = SimpleNamespace(
        provider=SimpleNamespace(settings=settings),
        extract=AsyncMock(
            return_value=ExtractionResult(observations=VisionObservations(description="Chart"))
        ),
    )
    cached = CachedVisionExtractor(extractor, SimpleNamespace(get=get, put=put))
    await cached.extract("first note", "image")
    await cached.extract("second note", "image")
    await cached.extract("second note", "image")
    assert extractor.extract.await_count == 2
    assert extractor.extract.await_args_list[0].args == ("first note", "image")
    assert extractor.extract.await_args_list[1].args == ("second note", "image")


async def test_default_case_ingestion_skips_graph(pipeline):
    p = pipeline
    p.runtime.settings.rag_case_graph_enabled = False
    await p.service.process(p.job, p.case)
    p.runtime.cases.ingest_text.assert_not_awaited()
    p.runtime.cases.ingest_image.assert_not_awaited()


async def test_empty_chart_description_is_rejected_before_caching():
    result = ExtractionResult(observations=VisionObservations())
    provider = SimpleNamespace(complete=AsyncMock(return_value=result.model_dump_json()))
    with pytest.raises(ValueError, match="empty description"):
        await OpenRouterVisionExtractor(provider).extract("RSI bearish divergence", "image")


async def test_skipped_graph_never_reports_image_indexed(pipeline):
    p = pipeline
    p.runtime.settings.rag_case_graph_enabled = False
    p.runtime.settings.rag_case_index_image = True
    await p.service.process(p.job, p.case)
    assert p.job.diagnostics["timings"]["case_graph_image_indexed"] is False
    assert p.job.diagnostics["timings"]["case_graph_skipped"] is True
