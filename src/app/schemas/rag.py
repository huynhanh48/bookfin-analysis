"""Knowledge ingestion and query response contracts."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentCreate(BaseModel):
    """Metadata supplied as JSON form field beside the optional file."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "image", "document"]
    text: str = Field(default="", max_length=100_000)
    source: str = Field(min_length=1, max_length=1000)


class DocumentView(BaseModel):
    """Durable job status; provider internals and file bytes are never returned."""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: str
    source: str
    status: str
    error_code: str | None


class DocumentResponse(BaseModel):
    """Consistent knowledge ingestion envelope."""

    data: DocumentView


class RagQuery(BaseModel):
    """Supported public LightRAG retrieval modes."""

    question: str = Field(min_length=1, max_length=10_000)
    mode: Literal["hybrid", "local", "global", "naive", "mix"] = "hybrid"


class AnswerView(BaseModel):
    """Answer plus citations actually returned by the backend."""

    answer: str
    sources: list[dict] = Field(default_factory=list)
    rerank_metadata: dict = Field(default_factory=dict)


class AnswerResponse(BaseModel):
    """Consistent query envelope."""

    data: AnswerView


class EvidenceAnswerView(BaseModel):
    """Answer and source diagnostics after SQL-filtered evidence retrieval."""

    answer: str
    case_ids: list[UUID] = Field(default_factory=list)
    sources: list[dict] = Field(default_factory=list)
    verification_summary: dict = Field(default_factory=dict)
    retrieval_metadata: dict = Field(default_factory=dict)
    rerank_metadata: dict = Field(default_factory=dict)


class EvidenceAnswerResponse(BaseModel):
    """Consistent success envelope for multimodal evidence answers."""

    data: EvidenceAnswerView
