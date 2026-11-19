"""Evidence enrichment sidecars, dual-vector lineage and durable job checkpoints.

Revision: d3e4f5a6b7c8; preserves all market and legacy RAG records.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "d3e4f5a6b7c8"
down_revision = "c2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable inferred context, evidence storage and isolated default-model indexes."""
    op.alter_column("analysis_cases", "symbol", nullable=True)
    op.alter_column("analysis_cases", "timeframe", nullable=True)
    op.alter_column("analysis_cases", "analyzed_at", nullable=True)
    op.add_column(
        "rag_documents", sa.Column("stage", sa.String(20), server_default="PENDING", nullable=False)
    )
    op.add_column(
        "rag_documents", sa.Column("progress", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "rag_documents",
        sa.Column("diagnostics", JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )
    op.add_column(
        "rag_documents", sa.Column("request_id", sa.String(100), server_default=None, nullable=True)
    )
    op.add_column(
        "analysis_case_embeddings",
        sa.Column("representation_kind", sa.String(20), server_default="legacy", nullable=False),
    )
    op.add_column(
        "analysis_case_embeddings",
        sa.Column("content_hash", sa.String(64), server_default=None, nullable=True),
    )
    op.add_column(
        "analysis_case_embeddings",
        sa.Column("image_hash", sa.String(64), server_default=None, nullable=True),
    )
    op.execute(
        "UPDATE rag_documents SET stage = status, progress = CASE WHEN status = 'COMPLETED' THEN 100 ELSE 0 END"
    )
    # Retain vectors while replacing dimension-wide indexes that mixed model spaces.
    op.drop_index("ix_case_embedding_cosine_1024", table_name="analysis_case_embeddings")
    op.drop_index("ix_case_embedding_cosine_1536", table_name="analysis_case_embeddings")
    op.execute(
        "CREATE INDEX ix_case_legacy_openai_1536 ON analysis_case_embeddings USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE space = 'openai/text-embedding-3-small:1536:case-v1' AND dimension = 1536 AND representation_kind = 'legacy'"
    )
    op.execute(
        "CREATE INDEX ix_case_legacy_voyage_1024 ON analysis_case_embeddings USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE space = 'mongodb/voyage-multimodal-3.5:1024:case-v1' AND dimension = 1024 AND representation_kind = 'legacy'"
    )
    op.execute(
        """CREATE TABLE analysis_enrichments (
	case_id UUID NOT NULL,
	original_input JSONB NOT NULL,
	observations JSONB NOT NULL,
	resolved_context JSONB NOT NULL,
	technical_snapshot JSONB NOT NULL,
	normalized_text TEXT NOT NULL,
	verification_summary JSONB NOT NULL,
	diagnostics JSONB NOT NULL,
	version VARCHAR(40) NOT NULL,
	CONSTRAINT pk_analysis_enrichments PRIMARY KEY (case_id),
	CONSTRAINT fk_analysis_enrichments_case_id_analysis_cases FOREIGN KEY(case_id) REFERENCES analysis_cases (id) ON DELETE CASCADE
)
"""
    )
    op.execute(
        """CREATE TABLE analysis_claims (
	id UUID NOT NULL,
	case_id UUID NOT NULL,
	claim_type VARCHAR(50) NOT NULL,
	source_type VARCHAR(20) NOT NULL,
	verification_status VARCHAR(40) NOT NULL,
	payload JSONB NOT NULL,
	CONSTRAINT pk_analysis_claims PRIMARY KEY (id),
	CONSTRAINT fk_analysis_claims_case_id_analysis_cases FOREIGN KEY(case_id) REFERENCES analysis_cases (id) ON DELETE CASCADE
)
"""
    )
    op.execute(
        "CREATE INDEX ix_analysis_claims_verification_status ON analysis_claims (verification_status)"
    )
    op.execute("CREATE INDEX ix_analysis_claims_case_id ON analysis_claims (case_id)")
    op.execute(
        """CREATE TABLE news_documents (
	id UUID NOT NULL,
	source_url TEXT NOT NULL,
	canonical_url TEXT NOT NULL,
	title TEXT NOT NULL,
	publisher TEXT NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE,
	retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
	content TEXT NOT NULL,
	content_hash VARCHAR(64) NOT NULL,
	relevance DOUBLE PRECISION NOT NULL,
	associated_symbol VARCHAR(20) NOT NULL,
	associated_sector VARCHAR(200),
	source_type VARCHAR(30) NOT NULL,
	CONSTRAINT pk_news_documents PRIMARY KEY (id)
)
"""
    )
    op.execute("CREATE INDEX ix_news_documents_source_type ON news_documents (source_type)")
    op.execute("CREATE INDEX ix_news_documents_published_at ON news_documents (published_at)")
    op.execute(
        "CREATE INDEX ix_news_documents_associated_symbol ON news_documents (associated_symbol)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_news_url_hash ON news_documents (canonical_url, content_hash)"
    )
    op.execute(
        """CREATE TABLE analysis_evidence (
	id UUID NOT NULL,
	case_id UUID NOT NULL,
	news_id UUID,
	source_type VARCHAR(30) NOT NULL,
	payload JSONB NOT NULL,
	CONSTRAINT pk_analysis_evidence PRIMARY KEY (id),
	CONSTRAINT fk_analysis_evidence_case_id_analysis_cases FOREIGN KEY(case_id) REFERENCES analysis_cases (id) ON DELETE CASCADE,
	CONSTRAINT fk_analysis_evidence_news_id_news_documents FOREIGN KEY(news_id) REFERENCES news_documents (id)
)
"""
    )
    op.execute("CREATE INDEX ix_analysis_evidence_case_id ON analysis_evidence (case_id)")
    op.execute("CREATE INDEX ix_analysis_evidence_source_type ON analysis_evidence (source_type)")
    op.execute(
        """CREATE TABLE evidence_provider_cache (
	key VARCHAR(64) NOT NULL,
	payload JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	CONSTRAINT pk_evidence_provider_cache PRIMARY KEY (key)
)
"""
    )
    op.execute(
        "CREATE INDEX ix_evidence_provider_cache_expires_at ON evidence_provider_cache (expires_at)"
    )
    op.execute(
        "CREATE INDEX ix_case_text_v2_openai_1536 ON analysis_case_embeddings USING hnsw (CAST(embedding AS VECTOR(1536)) vector_cosine_ops) WHERE space = 'openai/text-embedding-3-small:1536:case-text-v2' AND dimension = 1536 AND representation_kind = 'text'"
    )
    op.execute(
        "CREATE INDEX ix_case_image_v2_voyage_1024 ON analysis_case_embeddings USING hnsw (CAST(embedding AS VECTOR(1024)) vector_cosine_ops) WHERE space = 'mongodb/voyage-multimodal-3.5:1024:case-image-v2' AND dimension = 1024 AND representation_kind = 'multimodal'"
    )
    op.execute(
        "CREATE INDEX ix_enrichment_lexical ON analysis_enrichments USING gin (to_tsvector('simple', normalized_text))"
    )


def downgrade() -> None:
    """Refuse lossy downgrade until unresolved source contexts have been reviewed."""
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM analysis_cases WHERE symbol IS NULL OR timeframe IS NULL OR analyzed_at IS NULL) THEN RAISE EXCEPTION 'Resolve missing analysis context before downgrade'; END IF; END $$"
    )
    op.drop_index("ix_case_text_v2_openai_1536", table_name="analysis_case_embeddings")
    op.drop_index("ix_case_image_v2_voyage_1024", table_name="analysis_case_embeddings")
    op.drop_index("ix_case_legacy_openai_1536", table_name="analysis_case_embeddings")
    op.drop_index("ix_case_legacy_voyage_1024", table_name="analysis_case_embeddings")
    op.execute(
        "CREATE INDEX ix_case_embedding_cosine_1024 ON analysis_case_embeddings USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE dimension = 1024"
    )
    op.execute(
        "CREATE INDEX ix_case_embedding_cosine_1536 ON analysis_case_embeddings USING hnsw ((embedding::vector(1536)) vector_cosine_ops) WHERE dimension = 1536"
    )
    op.drop_table("evidence_provider_cache")
    op.drop_table("analysis_evidence")
    op.drop_table("news_documents")
    op.drop_table("analysis_claims")
    op.drop_table("analysis_enrichments")
    op.drop_column("analysis_case_embeddings", "image_hash")
    op.drop_column("analysis_case_embeddings", "content_hash")
    op.drop_column("analysis_case_embeddings", "representation_kind")
    op.drop_column("rag_documents", "request_id")
    op.drop_column("rag_documents", "diagnostics")
    op.drop_column("rag_documents", "progress")
    op.drop_column("rag_documents", "stage")
    op.alter_column("analysis_cases", "symbol", nullable=False)
    op.alter_column("analysis_cases", "timeframe", nullable=False)
    op.alter_column("analysis_cases", "analyzed_at", nullable=False)
