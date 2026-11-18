-- Runs once, when PostgreSQL initializes an empty data directory.
-- pgvector backs RAG embeddings; business and RAG tables are managed by Alembic.
CREATE EXTENSION IF NOT EXISTS vector;
