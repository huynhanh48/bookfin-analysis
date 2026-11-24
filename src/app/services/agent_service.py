"""Lifecycle and checkpoint access for the Phase 1 stock research graph."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from langchain_core.messages import BaseMessage
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import START
from sqlalchemy.engine import make_url

from app.agents.evidence import MarketEvidenceLoader
from app.agents.graph import (
    OpenRouterReportSynthesizer,
    UnconfiguredReportSynthesizer,
    build_analysis_graph,
)
from app.agents.research import ResearchContext, resolve_request
from app.agents.state import AnalysisState
from app.core.config import Settings
from app.core.logging import get_logger
from app.core.redis import get_redis_client
from app.fcdata.client import FcDataClient
from app.schemas.agent import AnalysisResponse, AnalysisStep, AnalyzeRequest
from app.schemas.realtime import RealtimeEvent
from app.services.realtime_service import RealtimeService

logger = get_logger(__name__)


def checkpoint_dsn(database_url: str) -> str:
    """Convert SQLAlchemy asyncpg URL into the psycopg URL required by LangGraph."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


@dataclass(frozen=True)
class AgentChatAnswer:
    """Chat answer with an optional frontend-ready report card."""

    answer: str
    report: dict[str, Any] | None = None


class AgentService:
    """Run a graph with independent DB connections and persist steps by thread ID."""

    def __init__(
        self,
        settings: Settings,
        realtime: RealtimeService | None = None,
        fcdata: FcDataClient | None = None,
    ):
        self._settings = settings
        self.rag_runtime = None
        self._realtime = realtime
        self._fcdata = fcdata
        self._tasks: set[asyncio.Task[None]] = set()
        self._pending: dict[str, dict[str, Any]] = {}
        self._semaphore = asyncio.Semaphore(settings.agent_max_concurrency)

    @asynccontextmanager
    async def _checkpointer(self) -> AsyncIterator[AsyncPostgresSaver | None]:
        if not self._settings.agent_checkpoint_enabled:
            yield None
            return
        async with AsyncPostgresSaver.from_conn_string(
            checkpoint_dsn(self._settings.database_url)
        ) as saver:
            yield saver

    def _graph(self, saver: AsyncPostgresSaver | None, *, need_model: bool = True):
        key = self._settings.openrouter_api_key
        synthesizer = (
            OpenRouterReportSynthesizer(
                api_key=key,
                base_url=self._settings.openrouter_base_url,
                model=self._settings.agent_model,
                timeout=self._settings.agent_timeout_seconds,
            )
            if key and need_model
            else UnconfiguredReportSynthesizer()
        )
        return build_analysis_graph(
            MarketEvidenceLoader(
                self._settings,
                self._fcdata if need_model else None,
                get_redis_client() if need_model else None,
            ),
            synthesizer,
            saver,
            ResearchContext(self.rag_runtime, self._settings) if need_model else None,
        )

    async def _initial_state(
        self,
        request: AnalyzeRequest,
        user_id: str,
        analysis_id: UUID,
        *,
        follow_up: str | None = None,
        default_symbol: str | None = None,
    ) -> AnalysisState:
        symbols, timeframe, cutoff = await resolve_request(
            request,
            self._settings.market_tz,
            follow_up=follow_up,
            default_symbol=default_symbol,
        )
        analyzed_at = cutoff or datetime.now(UTC)
        return {
            "analysis_id": str(analysis_id),
            "user_id": user_id,
            "symbol": symbols[0],
            "symbols": symbols,
            "timeframe": timeframe,
            "analyzed_at": analyzed_at.isoformat(),
            "question": request.question,
            "include_live": cutoff is None,
            "warnings": [],
            "historical_evidence": [],
            "news_evidence": [],
            "verification_results": [],
            "execution_metadata": {"phase": 1, "agent_model": self._settings.agent_model},
        }

    async def answer_chat(
        self,
        request: AnalyzeRequest,
        user_id: str,
        history: list[BaseMessage],
        *,
        follow_up: str,
        default_symbol: str | None = None,
    ) -> AgentChatAnswer:
        """Reuse the research graph with the chat database as the transcript store."""
        initial = await self._initial_state(
            request, user_id, uuid4(), follow_up=follow_up, default_symbol=default_symbol
        )
        initial["chat_history"] = history
        async with self._semaphore:
            result = await self._graph(None).ainvoke(initial)
        return AgentChatAnswer(
            answer=str(result["markdown"]),
            report=result.get("report_card"),
        )

    async def start(self, request: AnalyzeRequest, user_id: str) -> AnalysisResponse:
        """Persist admission before 202; a process crash can then be resumed."""
        if len(self._pending) >= self._settings.agent_max_pending:
            raise RuntimeError("agent_queue_full")
        analysis_id = uuid4()
        initial = await self._initial_state(request, user_id, analysis_id)
        async with self._checkpointer() as saver:
            graph = self._graph(saver, need_model=False)
            await graph.aupdate_state(
                {"configurable": {"thread_id": str(analysis_id)}}, initial, as_node=START
            )
        self._pending[str(analysis_id)] = dict(initial)
        task = asyncio.create_task(
            self._execute(str(analysis_id), None), name=f"agent:{analysis_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return AnalysisResponse(
            analysis_id=analysis_id,
            symbol=initial["symbol"],
            symbols=initial["symbols"],
            timeframe=initial["timeframe"],
            status="running",
            analyzed_at=datetime.fromisoformat(initial["analyzed_at"]),
        )

    async def _execute(self, analysis_id: str, initial: dict[str, Any] | None) -> None:
        config = {"configurable": {"thread_id": analysis_id}}
        try:
            async with self._semaphore, self._checkpointer() as saver:
                graph = self._graph(saver)
                async for updates in graph.astream(initial, config, stream_mode="updates"):
                    for node, update in updates.items():
                        if node != "__interrupt__":
                            elapsed_ms = (
                                (update or {}).get("execution_metadata", {}).get(f"{node}_ms")
                            )
                            await self._publish(
                                analysis_id, node, "completed", elapsed_ms=elapsed_ms
                            )
                await self._publish(analysis_id, "graph", "completed")
        except Exception as exc:
            logger.exception("agent_analysis_failed", extra={"analysis_id": analysis_id})
            await self._publish(analysis_id, "graph", "failed", type(exc).__name__)
        finally:
            self._pending.pop(analysis_id, None)

    async def _publish(
        self,
        analysis_id: str,
        node: str,
        status: str,
        error_type: str | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        if self._realtime is None:
            return
        data: dict[str, Any] = {"analysis_id": analysis_id, "node": node, "status": status}
        if error_type:
            data["error_type"] = error_type
        if elapsed_ms is not None:
            data["elapsed_ms"] = elapsed_ms
        await self._realtime.publish(
            RealtimeEvent(type="agent_progress", channel=f"agent:{analysis_id}", data=data)
        )

    async def get(self, analysis_id: UUID, user_id: str) -> AnalysisResponse | None:
        """Project the latest authorized checkpoint without exposing internal graph keys."""
        key = str(analysis_id)
        pending = self._pending.get(key)
        if not self._settings.agent_checkpoint_enabled:
            return None
        async with self._checkpointer() as saver:
            graph = self._graph(saver, need_model=False)
            snapshot = await graph.aget_state({"configurable": {"thread_id": key}})
        if not snapshot.values or snapshot.values.get("user_id") != user_id:
            return None
        status = (
            "completed"
            if snapshot.values.get("final_report")
            else ("running" if pending else "failed")
        )
        result = self._response(snapshot.values, status)
        if status == "failed":
            result.warnings.append("run_incomplete_resume_available")
        return result

    def _response(self, values: dict[str, Any], status: str) -> AnalysisResponse:
        return AnalysisResponse.model_validate(
            {
                **values,
                "status": status,
                "analyzed_at": values["analyzed_at"],
            }
        )

    async def steps(self, analysis_id: UUID, user_id: str) -> list[AnalysisStep] | None:
        """Return checkpoint history only after owner authorization."""
        if await self.get(analysis_id, user_id) is None:
            return None
        async with self._checkpointer() as saver:
            graph = self._graph(saver, need_model=False)
            result = []
            async for snapshot in graph.aget_state_history(
                {"configurable": {"thread_id": str(analysis_id)}}, limit=50
            ):
                result.append(
                    AnalysisStep(
                        nodes=list(snapshot.next),
                        created_at=datetime.fromisoformat(snapshot.created_at)
                        if snapshot.created_at
                        else None,
                    )
                )
        return result

    async def resume(self, analysis_id: UUID, user_id: str) -> AnalysisResponse | None:
        """Resume a failed/interrupted checkpoint; a completed run remains immutable."""
        current = await self.get(analysis_id, user_id)
        if current is None:
            return None
        if current.status == "completed" or str(analysis_id) in self._pending:
            return current
        if len(self._pending) >= self._settings.agent_max_pending:
            raise RuntimeError("agent_queue_full")
        self._pending[str(analysis_id)] = {
            "analysis_id": str(analysis_id),
            "user_id": user_id,
            "symbol": current.symbol,
            "timeframe": current.timeframe,
            "analyzed_at": current.analyzed_at.isoformat(),
        }
        task = asyncio.create_task(self._execute(str(analysis_id), None))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return current.model_copy(update={"status": "running"})

    async def close(self) -> None:
        """Finish shutdown without leaving unobserved background tasks."""
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
