"""Provider HTTP contracts and library adapter delegation; no paid API requests."""

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import AsyncOpenAI

from app.adapters.embedding_provider import EmbeddingSpace
from app.adapters.openrouter_adapter import OpenRouterAdapter, OpenRouterEmbeddingAdapter
from app.adapters.rag_adapter import RagAnswer
from app.adapters.rag_anything_adapter import (
    NO_CONTEXT_ANSWER,
    RagAnythingAdapter,
    is_empty_result,
)
from app.core.config import Settings
from app.core.exceptions import ConfigurationError, UpstreamError
from app.core.provider_metrics import collect_provider_events, summarize_provider_events
from app.services.rag_service import RagService


@pytest.fixture
def provider():
    """An SDK client whose transport is mocked by respx in every network test."""
    return OpenRouterAdapter(
        Settings(_env_file=None, openrouter_api_key="unit-test-key", openrouter_max_retries=0),
        AsyncOpenAI(
            api_key="unit-test-key", base_url="https://openrouter.ai/api/v1", max_retries=0
        ),
    )


async def test_joint_embedding_format(provider, respx_mock):
    route = respx_mock.post("https://openrouter.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [1, 2, 3]}]})
    )
    adapter = OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("test/model", 3), True)
    assert await adapter.embed("breakout", "data:image/png;base64,aA==") == [1, 2, 3]
    body = json.loads(route.calls[0].request.content)
    assert body["input"][0]["content"][0] == {"type": "text", "text": "breakout"}
    assert body["input"][0]["content"][1]["type"] == "image_url"
    await provider.close()


async def test_batch_embedding_uses_one_request_and_preserves_order(provider, respx_mock):
    route = respx_mock.post("https://openrouter.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0, 1, 0]},
                    {"index": 0, "embedding": [1, 0, 0]},
                ]
            },
        )
    )
    adapter = OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("test/model", 3))
    result = await adapter.batch(["first", "second"])
    assert result.tolist() == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    body = json.loads(route.calls[0].request.content)
    assert body["input"] == ["first", "second"]
    assert route.call_count == 1
    await provider.close()


async def test_batch_embedding_count_mismatch_is_rejected(provider, respx_mock):
    respx_mock.post("https://openrouter.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [1, 0, 0]}]})
    )
    adapter = OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("test/model", 3))
    with pytest.raises(UpstreamError, match="batch embedding"):
        await adapter.batch(["first", "second"])
    await provider.close()


@pytest.mark.parametrize("vector", [[1, 2], [0, 0, 0], [1, float("inf"), 2]])
async def test_dimension_and_vector_validation(vector):
    with pytest.raises(UpstreamError):
        EmbeddingSpace("model", 3).validate(vector)


async def test_provider_events_capture_duration_and_usage(provider, respx_mock):
    respx_mock.post("https://openrouter.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [1, 2, 3]}],
                "usage": {"prompt_tokens": 4, "total_tokens": 4, "cost": 0.000001},
            },
        )
    )
    adapter = OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("test/model", 3))
    with collect_provider_events() as events:
        await adapter.embed("hello")
    summary = summarize_provider_events(events)
    assert summary["requests"] == 1
    assert summary["prompt_tokens"] == 4
    assert summary["estimated_cost"] == pytest.approx(0.000001)
    assert events[0]["purpose"] == "embedding"
    assert events[0]["duration_seconds"] >= 0
    await provider.close()


async def test_provider_failure_sanitized(provider, respx_mock):
    respx_mock.post("https://openrouter.ai/api/v1/embeddings").mock(
        return_value=httpx.Response(429, json={"error": {"message": "secret-upstream-body"}})
    )
    with pytest.raises(UpstreamError, match="OpenRouter embedding") as error:
        await OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("m", 3)).embed("text")
    assert "secret" not in str(error.value)
    await provider.close()


async def test_vision_callback(provider, respx_mock):
    route = respx_mock.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "test",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "chart explanation"},
                        "finish_reason": "stop",
                        "index": 0,
                    }
                ],
            },
        )
    )
    assert await provider.complete("describe", image_data="aA==") == "chart explanation"
    assert b"data:image/png;base64,aA==" in route.calls[0].request.content
    await provider.close()


async def test_catalog_rejects_text_model_for_images(provider, respx_mock):
    respx_mock.get("https://openrouter.ai/api/v1/embeddings/models").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "text", "architecture": {"input_modalities": ["text"]}}]}
        )
    )
    with pytest.raises(ConfigurationError):
        await OpenRouterEmbeddingAdapter(provider, EmbeddingSpace("text", 3), True).validate_model()
    await provider.close()


@pytest.fixture
def rag():
    """Inject initialized library doubles without initializing PostgreSQL."""
    adapter = RagAnythingAdapter(Settings(_env_file=None), MagicMock(), MagicMock())
    adapter._rag = MagicMock()
    adapter._rag.insert_content_list = AsyncMock()
    adapter._rag.lightrag.doc_status.get_by_id = AsyncMock(return_value={"status": "processed"})
    adapter._rag.lightrag.aquery_llm = AsyncMock(
        return_value={
            "llm_response": {"content": "answer"},
            "data": {"references": [{"file_path": "source"}]},
        }
    )
    adapter._rag.finalize_storages = AsyncMock()
    return adapter


async def test_public_content_api_and_stable_document_id(rag, tmp_path):
    await rag.ingest_text("stable", "thesis", "source")
    call = rag._rag.insert_content_list.call_args.kwargs
    assert call["doc_id"] == "stable"
    assert call["content_list"][0]["text"] == "thesis"
    chart = tmp_path / "chart.png"
    await rag.ingest_image("chart", chart, "breakout", "source")
    assert rag._rag.insert_content_list.call_args.kwargs["content_list"][1]["img_path"] == str(
        chart
    )


async def test_rag_failure_isolated(rag):
    rag._rag.insert_content_list.side_effect = RuntimeError("secret")
    with pytest.raises(UpstreamError, match="Knowledge ingestion"):
        await rag.ingest_text("id", "text", "src")
    assert await rag.health_check()


async def test_query_sources_and_close(rag):
    answer = await RagService(rag).retrieve_financial_knowledge("RSI?")
    assert answer == RagAnswer("answer", [{"file_path": "source"}])
    lib = rag._rag
    await rag.close()
    await rag.close()
    lib.finalize_storages.assert_awaited_once()


async def test_empty_retrieval_answers_without_upstream_error(rag):
    """A greeting matching no knowledge must not surface as a 502 to the chat UI."""
    rag._rag.lightrag.aquery_llm.return_value = {
        "status": "failure",
        "message": "Query returned empty dataset.",
        "metadata": {"failure_reason": "no_results", "mode": "hybrid"},
        "data": {"entities": [], "relationships": [], "chunks": [], "references": []},
        "llm_response": {"content": "Sorry, I'm not able to provide an answer."},
    }
    rag._rag.lightrag.aquery_data = AsyncMock(
        return_value=rag._rag.lightrag.aquery_llm.return_value
    )
    assert await RagService(rag).retrieve_financial_knowledge("xin chào") == RagAnswer(
        NO_CONTEXT_ANSWER
    )
    assert await rag.retrieve("xin chào") == {
        "entities": [],
        "relationships": [],
        "chunks": [],
        "_rerank_metadata": [],
    }


async def test_genuine_query_failure_still_raises(rag):
    """Provider/configuration failures keep failing closed instead of looking empty."""
    rag._rag.lightrag.aquery_llm.return_value = {
        "status": "failure",
        "message": "Query failed: provider unavailable",
        "metadata": {},
        "data": {},
        "llm_response": {"content": None},
    }
    rag._rag.lightrag.aquery_data = AsyncMock(
        return_value=rag._rag.lightrag.aquery_llm.return_value
    )
    with pytest.raises(UpstreamError, match="Knowledge retrieval failed"):
        await rag.query("RSI?")
    with pytest.raises(UpstreamError, match="Knowledge retrieval failed"):
        await rag.retrieve("RSI?")


def test_empty_result_classification():
    assert is_empty_result({"status": "success", "data": {}}) is False
    assert is_empty_result({"status": "failure", "metadata": {"failure_reason": "no_results"}})
    assert is_empty_result({"status": "failure", "message": "Query returned empty dataset."})
    assert not is_empty_result({"status": "failure", "message": "Query failed: boom"})
    assert not is_empty_result(
        {"status": "failure", "message": "Query returned empty dataset.", "data": {"chunks": [{}]}}
    )


async def test_no_local_pdf_fallback(rag, tmp_path):
    with pytest.raises(ConfigurationError, match="local inference is disabled"):
        await rag.ingest_document("id", tmp_path / "doc.pdf", "source")


async def test_pinned_library_public_signatures():
    """Check real installed library signatures instead of testing only mocks."""
    import inspect
    from importlib.metadata import version

    from lightrag import LightRAG
    from lightrag.kg import STORAGE_IMPLEMENTATIONS
    from raganything import RAGAnything

    assert version("raganything") == "1.4.4"
    assert version("lightrag-hku") == "1.4.16"
    assert "doc_id" in inspect.signature(RAGAnything.insert_content_list).parameters
    assert "doc_id" in inspect.signature(RAGAnything.process_document_complete).parameters
    assert "PGTableGraphStorage" not in STORAGE_IMPLEMENTATIONS["GRAPH_STORAGE"]["implementations"]
    assert inspect.iscoroutinefunction(LightRAG.aquery_llm)


async def test_library_callbacks_do_not_deepcopy_http_client(provider, monkeypatch, tmp_path):
    """Real SDK clients contain locks; LightRAG asdict must only copy plain closures."""
    import copy

    import lightrag
    import lightrag.kg.shared_storage
    import lightrag.utils
    import raganything

    from app.adapters import rag_bootstrap

    captured = {}
    light = MagicMock()
    light.initialize_storages = AsyncMock()
    light.finalize_storages = AsyncMock()

    def make_light(**kwargs):
        copy.deepcopy(kwargs)
        captured.update(kwargs)
        return light

    monkeypatch.setattr(lightrag, "LightRAG", make_light)
    monkeypatch.setattr(lightrag.utils, "TiktokenTokenizer", lambda: None)
    monkeypatch.setattr(lightrag.kg.shared_storage, "initialize_pipeline_status", AsyncMock())
    monkeypatch.setattr(rag_bootstrap, "prepare_tokenizer", AsyncMock())
    library = MagicMock()
    library.finalize_storages = AsyncMock()
    monkeypatch.setattr(raganything, "RAGAnything", MagicMock(return_value=library))
    embedding = OpenRouterEmbeddingAdapter(
        provider, EmbeddingSpace("openai/text-embedding-3-small", 1536, "knowledge-v1")
    )
    adapter = RagAnythingAdapter(
        Settings(_env_file=None, rag_storage_dir=str(tmp_path)), provider, embedding
    )
    await adapter.initialize()
    alias = captured["embedding_func"].model_name
    assert len("LIGHTRAG_VDB_RELATION_" + alias + "_1536d") <= 63
    assert alias.startswith("emb_")
    await adapter.close()
    await provider.close()
