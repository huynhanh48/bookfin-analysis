"""Durable asynchronous enrichment, composing existing providers and repositories."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

from sqlalchemy import delete

from app.adapters.file_storage import image_data_url
from app.core.logging import get_logger
from app.core.provider_metrics import collect_provider_events, summarize_provider_events
from app.models.analysis_evidence import AnalysisEvidence
from app.repositories.evidence_repository import EvidenceRepository, content_key
from app.schemas.evidence import ExtractionResult, Stage
from app.services.analysis_normalization_service import normalize_analysis
from app.services.technical_verification_service import TechnicalVerificationService

logger = get_logger(__name__)


def resolve_context(case, observations, original: dict) -> tuple[dict, list[str]]:
    """User context wins; conflicts and missing decision time are reviewable, not guessed."""
    warnings = []
    candidates = {s.upper() for s in observations.symbol_candidates}
    symbol = case.symbol or (next(iter(candidates)) if len(candidates) == 1 else None)
    if case.symbol and candidates and case.symbol not in candidates:
        warnings.append("symbol_conflict")
    aliases = {"D1": "1D", "1D": "1D", "W1": "1W", "1W": "1W"}
    requested = aliases.get((case.timeframe or "").upper())
    observed = aliases.get((observations.timeframe or "").upper())
    timeframe = requested or observed
    if requested and observed and requested != observed:
        warnings.append("timeframe_conflict")
    if (
        case.analyzed_at
        and observations.chart_observed_at
        and observations.chart_observed_at > case.analyzed_at
    ):
        warnings.append("chart_after_analysis_timestamp")
    if case.analyzed_at and case.analyzed_at > datetime.now(UTC):
        warnings.append("analysis_timestamp_in_future")
    if not symbol or not timeframe or not case.analyzed_at:
        warnings.append("missing_or_ambiguous_context")
    if symbol and (
        len(symbol) > 20 or not symbol.replace(".", "").replace("_", "").replace("-", "").isalnum()
    ):
        warnings.append("invalid_symbol_candidate")
    exchange = original.get("exchange") or observations.exchange
    if exchange and observations.exchange and exchange.upper() != observations.exchange.upper():
        warnings.append("exchange_conflict")
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "analysis_at": case.analyzed_at.isoformat() if case.analyzed_at else None,
        "exchange": exchange,
        "price_basis": original.get("price_basis", "RAW"),
        "company": original.get("company_name", ""),
        "company_aliases": original.get("company_aliases", []) or [],
        "sector": original.get("sector", ""),
    }, warnings


def terminal_stage(blocking_warnings: list[str]) -> Stage:
    """Only required-stage failures make a case PARTIAL; optional news never does."""
    return Stage.PARTIAL if blocking_warnings else Stage.COMPLETED


def signal_window_start(technical_result: dict, analysis_at: datetime, settings) -> datetime | None:
    """Start news search at the earliest confirmed divergence formation, with a buffer."""
    if not getattr(settings, "rag_news_signal_window_enabled", True):
        return None
    if technical_result.get("status") != "ok":
        return None
    cutoff = analysis_at - timedelta(
        days=int(getattr(settings, "rag_divergence_lookback_days", 60))
    )
    starts = []
    for event in technical_result.get("divergence_events", []):
        raw_confirmed = event.get("confirmed_at")
        raw_start = event.get("start_time")
        if not raw_confirmed or not raw_start:
            continue
        try:
            confirmed = datetime.fromisoformat(str(raw_confirmed).replace("Z", "+00:00"))
            start = datetime.fromisoformat(str(raw_start).replace("Z", "+00:00"))
        except ValueError:
            continue
        if confirmed > analysis_at or confirmed < cutoff or start > analysis_at:
            continue
        starts.append(start)
    if not starts:
        return None
    return min(starts) - timedelta(days=int(getattr(settings, "rag_news_signal_buffer_days", 14)))


class RagEnrichmentService:
    """One job owns its SQL session; parallel branches use separate market/cache sessions."""

    def __init__(self, runtime, session):
        self.runtime, self.session = runtime, session
        self.repository = EvidenceRepository(session)
        self._commit_seconds = 0.0

    async def stage(self, job, case, stage: Stage, progress: int, **diagnostics) -> None:
        """Commit each checkpoint so polling and lease recovery survive worker failures."""
        job.status, job.stage, job.progress = stage.value, stage.value, progress
        case.ingest_status = stage.value
        job.diagnostics = {**(job.diagnostics or {}), **diagnostics}
        started = time.perf_counter()
        await self.session.commit()
        self._commit_seconds += time.perf_counter() - started
        logger.info(
            "enrichment_stage",
            extra={
                "job_id": str(job.id),
                "case_id": str(case.id),
                "stage": stage.value,
                **diagnostics,
            },
        )

    async def process(self, job, case) -> None:
        """Collect provider usage for this job, even when an early review return occurs."""
        with collect_provider_events() as events:
            try:
                await self._process(job, case)
            finally:
                job.diagnostics = {
                    **(job.diagnostics or {}),
                    "provider_usage": summarize_provider_events(events),
                }
                await self.session.commit()

    async def _process(self, job, case) -> None:
        """Preserve sources first; optional news/MM/index failures produce explicit PARTIAL."""
        started = time.monotonic()
        wall_started = time.perf_counter()
        timings: dict[str, object] = {}
        created = getattr(job, "created_at", None)
        if isinstance(created, datetime):
            timings["queue_wait_seconds"] = max(0.0, (datetime.now(UTC) - created).total_seconds())
        original = json.loads(job.text)
        state = await self.repository.ensure_state(case.id, original)
        await self.stage(job, case, Stage.EXTRACTING, 10)
        image = (
            await image_data_url(self.runtime.storage, case.chart_path) if case.chart_path else None
        )
        key = content_key(
            "vision-extraction-v4-joint",
            case.chart_path,
            case.thesis,
            self.runtime.settings.rag_vision_model or self.runtime.settings.rag_llm_model,
        )
        vision_started = time.perf_counter()
        extracted = await self.runtime.evidence_cache.get(key)
        timings["vision_cache_hit"] = extracted is not None
        timing = time.monotonic()
        if extracted is None:
            async with self.runtime.provider_semaphore:
                result = await self.runtime.vision.extract(case.thesis, image)
            extracted = result.model_dump(mode="json")
            await self.runtime.evidence_cache.put(key, extracted)
        timings["extraction_seconds"] = time.perf_counter() - vision_started
        extraction = ExtractionResult.model_validate(extracted)
        image_notes: list[str] = []
        if image and not extraction.observations.chart_observed_at:
            image_notes.append("chart_time_unverified")
        state.observations = extraction.observations.model_dump(mode="json")
        context, review = resolve_context(case, extraction.observations, original)
        state.resolved_context = context
        claims = extraction.user_claims + extraction.observations.claims
        for index, claim in enumerate(claims):
            claim.claim_id = uuid5(
                case.id, f"{index}:{claim.source_type}:{claim.claim_type}:{claim.original_text}"
            )
        # Store UUIDs once; retries reuse the cached extraction and therefore source claim IDs.
        if review:
            await self.repository.save_claims(case.id, claims)
            state.diagnostics = {"review_reasons": review}
            await self.stage(job, case, Stage.NEEDS_REVIEW, 100, review_reasons=review)
            job.lease_until = None
            await self.session.commit()
            return
        case.symbol = case.symbol or context["symbol"]
        case.timeframe = context["timeframe"]
        await self.stage(
            job, case, Stage.ENRICHING, 25, extraction_latency_seconds=time.monotonic() - timing
        )
        timing = time.monotonic()

        branch_timings = {}

        async def technical():
            tick = time.monotonic()
            result = await self.runtime.technical.snapshot(
                context["symbol"],
                context["timeframe"],
                case.analyzed_at,
                price_basis=context["price_basis"],
                exchange=context["exchange"],
            )
            branch_timings["technical_retrieval_latency_seconds"] = time.monotonic() - tick
            return result

        async def news(window_start: datetime | None):
            tick = time.monotonic()
            result = await self.runtime.news.enrich(
                context["symbol"],
                case.analyzed_at,
                context["company"],
                context["sector"],
                aliases=context.get("company_aliases", []),
                window_start=window_start,
            )
            branch_timings["news_enrichment_latency_seconds"] = time.monotonic() - tick
            return result

        warnings = []
        independent_news = None
        if not self.runtime.settings.rag_news_signal_window_enabled:
            independent_news = asyncio.create_task(news(None))
        try:
            technical_result = await technical()
        except asyncio.CancelledError:
            if independent_news is not None:
                independent_news.cancel()
                await asyncio.gather(independent_news, return_exceptions=True)
            raise
        except Exception as exc:
            technical_result = {
                "status": "insufficient_history",
                "reason": "Technical provider failed.",
                "error_code": type(exc).__name__,
            }
            warnings.append("technical_provider_failed")
        news_window_start = signal_window_start(
            technical_result, case.analyzed_at, self.runtime.settings
        )
        news_warnings: list[str] = []
        try:
            articles, raw_news_warnings = (
                await independent_news
                if independent_news is not None
                else await news(news_window_start)
            )
            # News is optional context. Missing/failed news is reported separately and
            # never turns an otherwise complete technical/embedding case into PARTIAL.
            news_warnings = [w for w in raw_news_warnings if w != "news_disabled"]
        except Exception:
            articles = []
            news_warnings.append("news_provider_failed")
        timings["technical_retrieval_seconds"] = branch_timings.get(
            "technical_retrieval_latency_seconds"
        )
        timings["news_enrichment_seconds"] = branch_timings.get("news_enrichment_latency_seconds")
        technical_result = json.loads(json.dumps(technical_result, default=str))
        from app.agents.research import RULE_VERSION, case_fingerprint

        if technical_result.get("status") == "ok":
            technical_result["research_fingerprint"] = case_fingerprint(technical_result)
            technical_result["research_rule_version"] = RULE_VERSION
        state.technical_snapshot = technical_result
        await self.repository.link_news(case.id, articles)
        await self.stage(
            job, case, Stage.VERIFYING, 50, evidence_latency_seconds=time.monotonic() - timing
        )
        evidence_id = f"technical:{case.id}:evidence-v1"
        await self.session.execute(
            delete(AnalysisEvidence).where(
                AnalysisEvidence.case_id == case.id, AnalysisEvidence.news_id.is_(None)
            )
        )
        self.session.add(
            AnalysisEvidence(
                id=uuid4(),
                case_id=case.id,
                source_type="technical",
                payload={
                    "evidence_id": evidence_id,
                    "as_of": technical_result.get("as_of"),
                    "engine_version": technical_result.get("engine_version"),
                    "source": technical_result.get("source"),
                },
            )
        )
        verification_started = time.monotonic()
        claims = TechnicalVerificationService(
            getattr(self.runtime.settings, "rag_divergence_lookback_days", 60),
            getattr(self.runtime.settings, "rag_rsi_overbought", 70.0),
            getattr(self.runtime.settings, "rag_rsi_oversold", 30.0),
        ).verify(claims, technical_result, evidence_id)
        await self.repository.save_claims(case.id, claims)
        await self.stage(
            job,
            case,
            Stage.NORMALIZING,
            65,
            technical_verification_latency_seconds=time.monotonic() - verification_started,
        )
        normalization_started = time.perf_counter()
        text, summary = normalize_analysis(
            case.thesis, context, extraction.observations, claims, technical_result, articles
        )
        timings["normalization_seconds"] = time.perf_counter() - normalization_started
        state.normalized_text, state.verification_summary = text, summary
        state.diagnostics = {
            "warnings": warnings,
            "news_warnings": news_warnings,
            "notes": image_notes,
            "timings": timings,
            "news_window_start": (
                news_window_start.isoformat() if news_window_start is not None else None
            ),
            "versions": {
                "normalization": "evidence-v1",
                "llm": self.runtime.settings.rag_llm_model,
                "vision": self.runtime.settings.rag_vision_model
                or self.runtime.settings.rag_llm_model,
            },
        }
        if technical_result.get("status") == "needs_review":
            await self.stage(
                job, case, Stage.NEEDS_REVIEW, 100, review_reasons=[technical_result.get("reason")]
            )
            job.lease_until = None
            await self.session.commit()
            return
        if technical_result.get("status") != "ok":
            warnings.append("technical_insufficient_history")
        await self.stage(job, case, Stage.INDEXING, 75)
        timing = time.monotonic()
        index_started_wall = time.perf_counter()

        async def embed(provider, content, image_value, kind):
            tick = time.perf_counter()
            digest = content_key(content, image_value)
            cache_key = content_key("embedding", provider.space.key, digest)
            cached = await self.runtime.evidence_cache.get(cache_key)
            if cached:
                vector = provider.space.validate(cached["vector"])
            else:
                async with self.runtime.provider_semaphore:
                    vector = await provider.embed(content, image_value)
                await self.runtime.evidence_cache.put(cache_key, {"vector": vector})
            return provider, vector, digest, kind, time.perf_counter() - tick, cached is not None

        tasks = [embed(self.runtime.case_text_embedding, text, None, "text")]
        if image:
            chart_text = f"{context['symbol']} {context['timeframe']}\n{extraction.observations.description}\nUser chart notes (unverified): {case.thesis[:4000]}"
            tasks.append(
                embed(self.runtime.case_multimodal_embedding, chart_text, image, "multimodal")
            )
        embedded = await asyncio.gather(*tasks, return_exceptions=True)
        from app.repositories.analysis_repository import AnalysisRepository

        repository = AnalysisRepository(self.session)
        for result in embedded:
            if isinstance(result, BaseException):
                warnings.append(f"embedding_failed:{type(result).__name__}")
                continue
            provider, vector, digest, kind, duration, cache_hit = result
            timings[f"{kind}_embedding_seconds"] = duration
            timings[f"{kind}_embedding_cache_hit"] = cache_hit
            await repository.save_embedding(
                case.id,
                provider.space,
                vector,
                kind=kind,
                content_hash=digest,
                image_hash=case.chart_path.split(".")[0] if case.chart_path else None,
            )
        job.diagnostics = {
            **job.diagnostics,
            "embedding_latency_seconds": time.monotonic() - timing,
        }
        knowledge_document_id = (
            f"case-{case.id}-{content_key('evidence-v1', text, case.chart_path)[:24]}"
        )
        state.diagnostics = {
            **state.diagnostics,
            "rag_document_id": knowledge_document_id,
            "rag_namespace": "cases_evidence_v1",
        }
        # Indexing an image through RAGAnything triggers multimodal processing and a
        # second Vision pass. Normalized text already contains the vision description;
        # RAG_CASE_INDEX_IMAGE=false keeps multimodal embedding while avoiding duplicate
        # graph work. Benchmark both modes before changing the default.
        graph_started = time.perf_counter()
        index_image = bool(getattr(self.runtime.settings, "rag_case_index_image", True))
        timings["case_graph_image_indexed"] = False
        try:
            if not self.runtime.settings.rag_case_graph_enabled:
                timings["case_graph_skipped"] = True
            elif case.chart_path and index_image:
                await self.runtime.cases.ingest_image(
                    knowledge_document_id,
                    self.runtime.storage.path(case.chart_path),
                    text,
                    f"analysis://{case.id}",
                )
            else:
                await self.runtime.cases.ingest_text(
                    knowledge_document_id, text, f"analysis://{case.id}"
                )
            timings["case_graph_image_indexed"] = bool(
                self.runtime.settings.rag_case_graph_enabled and case.chart_path and index_image
            )
        except Exception as exc:
            warnings.append(f"knowledge_index_failed:{type(exc).__name__}")
        timings["case_graph_index_seconds"] = time.perf_counter() - graph_started
        timings["embedding_and_index_seconds"] = time.perf_counter() - index_started_wall
        elapsed = time.monotonic() - started
        timings["total_wall_clock_seconds"] = time.perf_counter() - wall_started
        timings["stage_commit_seconds"] = self._commit_seconds
        state.diagnostics = {
            **state.diagnostics,
            "warnings": warnings,
            "news_warnings": news_warnings,
            "status_reason": "blocking_warnings" if warnings else "completed",
            "total_latency_seconds": elapsed,
            "timings": timings,
        }
        terminal = terminal_stage(warnings)
        await self.stage(
            job,
            case,
            terminal,
            100,
            embedding_and_index_latency_seconds=time.monotonic() - timing,
            total_ingestion_latency_seconds=elapsed,
            timings=timings,
            warnings=warnings,
            news_warnings=news_warnings,
            status_reason="blocking_warnings" if warnings else "completed",
            news_window_start=(
                news_window_start.isoformat() if news_window_start is not None else None
            ),
        )
        job.lease_until = None
        await self.session.commit()
