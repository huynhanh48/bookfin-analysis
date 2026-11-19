# BookFin Analysis — V1 RAG

Backend phân tích chứng khoán Việt Nam: market data, technical evidence, multimodal RAG và Analysis Case Memory.

## Công nghệ

| Thành phần | Công nghệ |
|---|---|
| Runtime | Python 3.12+ |
| API | FastAPI, Pydantic v2 |
| Database | PostgreSQL 17 + pgvector |
| ORM / migration | SQLAlchemy 2.x async, Alembic |
| Cache / mirror | Redis (tùy chọn) |
| Chỉ báo | TA-Lib 0.8.3 + custom market structure/divergence |
| RAG | RAG-Anything 1.4.4, LightRAG 1.4.16 |
| LLM / Vision / Embedding | OpenRouter |
| News | Tavily, Firecrawl |
| Đóng gói | Docker Compose, uv |

## Chạy bằng Docker

```bash
cp .env.example .env
# Điền OPENROUTER_API_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD

docker compose up -d postgres
docker compose --profile tools run --rm migrate
docker compose --profile cache up -d redis   # nếu dùng Redis
docker compose up -d --build api

curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
```

Chạy local không Docker:

```bash
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Chỉ chạy **một process RAG** trên một volume/working directory.

## Env chính

| Biến | Ý nghĩa |
|---|---|
| `OPENROUTER_API_KEY` | Bắt buộc cho RAG |
| `RAG_ENABLED=true` | Bật RAG runtime |
| `RAG_ENRICHMENT_ENABLED=true` | Bật Vision/claim/technical/news cho Analysis Case |
| `RAG_VISION_MODEL` | Model đọc ảnh, ví dụ `google/gemini-3.1-flash-lite` |
| `RAG_LLM_MODEL` | Model text/graph/answer |
| `RAG_CASE_GRAPH_ENABLED` | Bật/tắt case knowledge graph |
| `RAG_CASE_INDEX_IMAGE` | Chỉ có tác dụng khi graph bật; `false` = chỉ index text |
| `RAG_NEWS_ENABLED` | Bật news enrichment |
| `TAVILY_API_KEY`, `FIRECRAWL_API_KEY` | Search/extract tin tức |
| `ADMIN_API_KEY` | Bắt buộc ngoài development |

## API V1

| Method | Endpoint | Dùng để |
|---|---|---|
| `POST` | `/api/v1/rag/documents` | Nạp text/ảnh/PDF vào Knowledge RAG |
| `GET` | `/api/v1/rag/documents/{id}` | Xem trạng thái document |
| `POST` | `/api/v1/rag/documents/{id}/retry` | Retry document lỗi |
| `POST` | `/api/v1/rag/query` | Hỏi trên Knowledge RAG |
| `POST` | `/api/v1/analysis/cases` | Tạo Analysis Case từ chart + ghi chú |
| `GET` | `/api/v1/analysis/cases/{id}` | Xem case, claims, evidence, embeddings, outcome |
| `GET` | `/api/v1/analysis/jobs/{job_id}` | Poll stage/progress/lỗi |
| `POST` | `/api/v1/analysis/cases/{id}/retry` | Retry case `PARTIAL/FAILED` |
| `PATCH` | `/api/v1/analysis/cases/{id}/outcome` | Ghi kết quả sau phân tích |
| `POST` | `/api/v1/analysis/search` | Tìm case bằng text/ảnh + filter |
| `POST` | `/api/v1/rag/query/multimodal` | Hỏi đáp có citation, kèm ảnh tùy chọn |

### Ví dụ

Nạp Knowledge RAG:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/rag/documents \
  -F 'metadata={"kind":"text","source":"note-01","text":"Breakout cần xác nhận bằng volume và nến đóng."}'
```

Hỏi Knowledge RAG:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/rag/query \
  -H 'Content-Type: application/json' \
  -d '{"question":"Breakout cần xác nhận gì?","mode":"hybrid"}'
```

Tạo Analysis Case:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analysis/cases \
  -F 'metadata={"symbol":"SSI","timeframe":"1D","analyzed_at":"2025-10-20T15:30:00+07:00","thesis":"SSI có RSI bearish divergence trong giai đoạn 8-9/2025.","source":"TradingView"}' \
  -F 'chart=@chart.png'
```

Poll job:

```bash
curl -s http://127.0.0.1:8000/api/v1/analysis/jobs/<job_id>
```

Tìm case tương tự:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/analysis/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"RSI bearish divergence","symbol":"SSI","top_k":5}'
```

Hỏi multimodal có citation:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/rag/query/multimodal \
  -F 'metadata={"query":"Case này có bằng chứng gì ủng hộ phân kỳ âm?"}' \
  -F 'image=@chart.png'
```

Ghi outcome:

```bash
curl -X PATCH http://127.0.0.1:8000/api/v1/analysis/cases/<case_id>/outcome \
  -H 'Content-Type: application/json' \
  -d '{"outcome":{"5_sessions":{"return_pct":2.1,"max_drawdown_pct":-1.4}}}'
```

Nếu backend yêu cầu admin key, thêm header:

```text
X-Admin-Key: <ADMIN_API_KEY>
```

## Trạng thái Analysis Case

```text
PENDING → EXTRACTING → ENRICHING → VERIFYING → NORMALIZING → INDEXING
        → COMPLETED | PARTIAL | NEEDS_REVIEW | FAILED
```

- `COMPLETED`: pipeline cốt lõi xong; có thể có news warning.
- `PARTIAL`: thiếu technical/embedding/graph nhưng đã lưu subset.
- `NEEDS_REVIEW`: symbol/timeframe/ngày/ảnh chưa đủ rõ.
- `FAILED`: lỗi blocking; retry được.
- `outcome` là kết quả sau này, không dùng trong embedding/search.
