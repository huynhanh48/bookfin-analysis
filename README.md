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
| Stock Research Agent | LangGraph 1.2.24, langchain-openrouter 0.3.0 |
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


## Deploy production bằng Docker

`docker-compose.prod.yml` đóng gói toàn bộ stack:

| Service | Vai trò | Port |
|---|---|---|
| `frontend` | Nginx serve SPA + proxy `/api/`, WebSocket | `${WEB_PORT:-8080}` |
| `api` | FastAPI, RAG consumer, chat, agent | `127.0.0.1:${API_PORT:-8000}` |
| `postgres` | PostgreSQL 17 + pgvector | `127.0.0.1:${POSTGRES_PORT:-5432}` |
| `redis` | cache, mirror, worker lock | `127.0.0.1:${REDIS_PORT:-6379}` |
| `migrate` | chạy Alembic một lần trước API | — |
| `scheduler` | job định kỳ | — |
| `realtime` | cache nến đang chạy + trigger sau đóng phiên | — |
| `worker` | chạy job thủ công (`--profile worker`) | — |

### 1. Chuẩn bị VPS

- Docker Engine + Docker Compose v2.
- Khuyến nghị tối thiểu **4 GB RAM**, **20 GB dung lượng trống** (backend image có MinerU/torch nên khá lớn).
- Mở port `${WEB_PORT:-8080}` (hoặc đặt sau Caddy/Nginx/Cloudflare).
- FCData, OpenRouter, Tavily/Firecrawl phải truy cập được từ VPS.

### 2. Clone branch deploy

```bash
git clone https://github.com/huynhanh48/bookfin-analysis.git
cd bookfin-analysis
git checkout v1.1.0
```

### 3. Tạo `.env` và điền cấu hình

```bash
cp .env.example .env
chmod 600 .env
```

**Không commit `.env`.** Các biến bắt buộc:

| Biến | Bắt buộc | Ghi chú |
|---|---|---|
| `POSTGRES_PASSWORD` | Có | mật khẩu PostgreSQL; đổi khỏi giá trị mẫu |
| `REDIS_PASSWORD` | Có | mật khẩu Redis |
| `ADMIN_API_KEY` | Có | `APP_ENV=production` bắt buộc; frontend sẽ hỏi key lần đầu |
| `OPENROUTER_API_KEY` | Có nếu dùng RAG/agent | LLM, vision, embedding, rerank |
| `AGENT_ENABLED` | Nên đặt `true` | bật mode Phân tích cổ phiếu |
| `AGENT_NEWS_ENABLED` | Nên đặt `true` | bật tin tức trong research report |
| `RAG_ENABLED` | Nên đặt `true` | tắt thì các endpoint RAG trả 503 |
| `RAG_NEWS_ENABLED` | Nên đặt `true` | cần `TAVILY_API_KEY`; Firecrawl là fallback |
| `TAVILY_API_KEY` | Nếu bật news | search chính |
| `FIRECRAWL_API_KEY` | Khuyến nghị | fallback extraction/search |
| `RAG_NEWS_RSS_FEEDS` | Tuỳ chọn | JSON array một dòng, ví dụ `["https://cafef.vn/...rss"]` |
| `RAG_REMOTE_PARSER_URL` | Nếu ingest PDF | PDF bị chặn nếu không có parser remote |
| `WEB_PORT` | Tuỳ chọn | mặc định `8080` |
| `API_PORT` | Tuỳ chọn | mặc định `8000`, chỉ bind localhost |
| `IMAGE_TAG` | Tuỳ chọn | mặc định `v1.1.0` |

Các biến **không nên sửa trong `.env` production**:

- `DATABASE_URL`, `REDIS_URL`: compose tự trỏ tới host `postgres`/`redis`.
- `APP_ENV`: compose force `production`.
- `RAG_STORAGE_DIR`, `RAG_UPLOAD_DIR`: compose trỏ vào `/app/data`.

Lưu ý cấu hình RAG:

- `RAG_TEXT_EMBEDDING_MODEL` / `RAG_TEXT_EMBEDDING_DIMENSION` và
  `RAG_MULTIMODAL_EMBEDDING_MODEL` / `RAG_MULTIMODAL_EMBEDDING_DIMENSION`
  phải khớp model thật. **Không đổi dimension sau khi đã có vector** nếu chưa reindex.
- `RAG_RERANK_ENABLED=true` dùng OpenRouter; `RAG_RERANK_FAIL_OPEN=true` để report
  vẫn chạy khi reranker lỗi.
- `AGENT_CHECKPOINT_ENABLED=true` cần migration checkpointer; `migrate` xử lý tự động.
- Chỉ chạy **một process RAG** trên một volume. Không chạy thêm `rag_worker` độc lập
  cùng API đang bật RAG.

Kiểm tra cấu hình trước khi build:

```bash
docker compose -f docker-compose.prod.yml config >/dev/null && echo OK
```

Nếu báo thiếu `ADMIN_API_KEY`/`POSTGRES_PASSWORD`/`REDIS_PASSWORD`, điền lại `.env`.

### 4. Build và chạy

```bash
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

Lần build đầu sẽ lâu vì cài Python dependency + frontend. `migrate` chạy
`alembic upgrade head` trước khi API/worker khởi động.

Kiểm tra:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f migrate
docker compose -f docker-compose.prod.yml logs -f api frontend
curl -s http://127.0.0.1:8000/health
curl -s http://127.0.0.1:8000/ready
```

Frontend:

```text
http://<VPS_IP>:8080
```

Lần đầu gọi API, nếu `ADMIN_API_KEY` đã đặt, trình duyệt sẽ hỏi key. Key được lưu
trong `localStorage["bookfin_admin_key"]`; khi đổi key phải xoá key cũ trong
DevTools → Application → Local Storage.

### 5. Reverse proxy + HTTPS (khuyến nghị)

Nginx/Caddy chỉ cần proxy tới `127.0.0.1:8080`; Nginx trong container đã proxy
`/api/` và WebSocket `/api/v1/ws` sang API.

Ví dụ Caddy:

```text
bookfin.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Không cần mở trực tiếp PostgreSQL/Redis/API ra Internet.

### 6. Vận hành

```bash
# log
docker compose -f docker-compose.prod.yml logs -f api scheduler realtime

# chạy migration thủ công khi cần
docker compose -f docker-compose.prod.yml run --rm migrate

# chạy job một lần
docker compose -f docker-compose.prod.yml --profile worker run --rm worker \
  python -m app.workers run divergence-scan --param limit=20
```

Backup tối thiểu:

- PostgreSQL: `pg_dump` database `stock_research`.
- Volume `rag_data`: chứa upload gốc, LightRAG NetworkX graph, parser artifacts.
- Volume `postgres_data`.
- Volume `redis_data` không phải source of truth; có thể mất nếu chấp nhận rebuild cache.

### 7. Update / rollback

```bash
git fetch origin
git checkout v1.1.0
git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
```

- Luôn backup PostgreSQL + `rag_data` trước khi update.
- Migration được chạy bởi service `migrate`.
- Rollback code không tự rollback schema; cần restore DB backup hoặc viết migration down
  có kiểm soát. Không chạy downgrade mù trên production.

### 8. Lỗi thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `401/403` khi chat | `ADMIN_API_KEY` trong `.env`; nhập lại key ở frontend |
| `502` từ frontend | API unhealthy, migration lỗi; xem `logs api migrate` |
| `compose config` báo thiếu biến | điền `ADMIN_API_KEY`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD` |
| RAG trả 503 | `RAG_ENABLED=true`; API là process duy nhất giữ volume |
| RAG lỗi lock | chỉ một API/RAG runtime trên volume; không chạy worker RAG song song |
| PDF không ingest | thiếu `RAG_REMOTE_PARSER_URL` |
| Không có nến mới | FCData reachable; kiểm tra log backfill/worker; freshness warning |
| Hết dung lượng Docker | prune image/cache; backend image có MinerU/torch dung lượng lớn |


Chạy local không Docker:

```bash
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Chỉ chạy **một process RAG** trên một volume/working directory.

## Stock Research Agent

Agent đọc nến đã đóng và indicator hiện có, sau đó tổng hợp báo cáo bằng OpenRouter.
Hướng dẫn triển khai và các giới hạn: [docs/agent-workflow.md](docs/agent-workflow.md).

### Worker engine (Docker)

Một Docker image chạy nhiều role qua command; mọi role dùng chung Job Registry và
Worker Engine (không cần Celery).

```bash
docker compose --profile workers up -d scheduler
docker compose --profile realtime up -d realtime
docker compose run --rm worker python -m app.workers list
docker compose run --rm worker python -m app.workers run divergence-scan --param limit=20
docker compose logs -f scheduler
```

| Job | Lịch (T2–T6) | Việc |
|---|---|---|
| `sync-symbols` | 08:00 | đồng bộ danh mục từ FCData |
| `ingest-close` | 15:05 | nạp nến đóng của 5 ngày gần nhất |
| `recompute-indicators` | 15:20 | tính lại chỉ báo confirmed |
| `divergence-scan` | 15:30 | quét phân kỳ phiên gần nhất |
| `screener-scan` | 15:35 | quét AFL trên nến đã đóng |
| `rag-ingestion` | thủ công | drain hàng đợi RAG khi API không giữ runtime |

- **Registry**: thêm job mới bằng module trong `src/app/workers/jobs/` + decorator
  `register_job`; scheduler/CLI tự nhận, không thêm service/container.
- **Executor**: `python -m app.workers run <job>` chạy một lần, trả exit code 1 khi
  job `failed`/`partial`/`timed_out`; params validate bằng Pydantic
  (`--param key=value` hoặc `--params '{"...": ...}'`).
- **Lock**: Redis `SET NX PX` + token riêng; release/extend bằng Lua
  compare-and-delete/pexpire nên instance hết TTL không xoá khoá của instance khác.
- **Retry/timeout/idempotency**: retry exponential backoff, timeout từng lần thử;
  marker `worker:done:*` theo ngày cho scheduler/realtime tránh chạy trùng sau restart.
- **Không recompute trùng**: `IndicatorService.recompute` tái sử dụng snapshot theo
  `input_hash`; `ingest-close` chỉ upsert nến thiếu/đã đóng.
- **Realtime**: role mới chỉ cache nến đang hình thành; sau giờ đóng phiên kích hoạt
  `ingest-close` → `recompute-indicators` cho watchlist. Scheduler vẫn là lưới an toàn.
- **Healthcheck**: mỗi role ghi heartbeat JSON; Docker probe bằng
  `python -m app.workers health --role scheduler|realtime --max-age 120`.
- **Graceful shutdown**: SIGTERM dừng nhận job mới, chờ job đang chạy tối đa
  `WORKER_SHUTDOWN_GRACE_SECONDS` rồi cancel và nhả lock.
- Các module cũ (`app.workers.scheduler`, `indicator_worker`, `divergence_scan`,
  `realtime`, `backfill`, `rag_worker`) vẫn chạy được để tương thích ngược.
- Hai service worker dùng lại image `bookfin-analysis-api:latest` (cùng Dockerfile
  `target: dev`) và mount `./src`, nên sửa code là áp dụng ngay.

## Stock Screener & Watchlist

Chiến lược lọc dòng tiền AFL (`docs/loc_dong_tien.afl`) được chuyển sang Python:

```text
DK1 volume > 100.000
DK2 close > 10.000 VND
DK3 volume > 0.8 * SMA(volume 10 phiên trước)
DK4 close > 1.02 * close phiên trước
DK6 close > 1.02 * SMA(close, 5)
```

- `POST /api/v1/screener/scan` — historical hoặc realtime, filter tùy chỉnh.
- Filter `divergence` riêng: preset "Divergence", chọn số phiên gần nhất
  (1 = phiên gần nhất, tránh cuối tuần/ngày lễ) hoặc khoảng ngày; chọn mốc
  pivot (ngày trên chart) hoặc nến xác nhận; hỗ trợ bullish/bearish và
  regular/hidden.
- `GET /api/v1/screener/filters/default` — cấu hình mặc định.
- `GET /api/v1/screener/results?profile_id=...` — snapshot realtime.
- `/api/v1/watchlists` — CRUD watchlist; tự tạo 10 list mặc định.
- Trang `/screener` trong `bookfin-analysis-web`.
- Realtime dùng worker hiện tại + Redis pub/sub, không thêm container.
- Chi tiết: `docs/stock-screener.md`.

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
| `GET` | `/api/v1/rag/documents` | Liệt kê nội dung Knowledge RAG theo cursor |
| `GET` | `/api/v1/rag/documents/{id}` | Xem trạng thái document |
| `GET` | `/api/v1/rag/documents/{id}/content` | Xem nội dung text gốc và tiến độ |
| `GET` | `/api/v1/rag/documents/{id}/file` | Tải file gốc qua API quản trị |
| `POST` | `/api/v1/rag/documents/{id}/retry` | Retry document lỗi |
| `DELETE` | `/api/v1/rag/documents/{id}` | Gỡ document khỏi chỉ mục, giữ bản ghi/file gốc để kiểm toán |
| `POST` | `/api/v1/rag/query` | Hỏi trên Knowledge RAG |
| `POST` | `/api/v1/analysis/cases` | Tạo Analysis Case từ chart + ghi chú |
| `GET` | `/api/v1/analysis/cases/{id}` | Xem case, claims, evidence, embeddings, outcome |
| `GET` | `/api/v1/analysis/jobs/{job_id}` | Poll stage/progress/lỗi |
| `POST` | `/api/v1/analysis/cases/{id}/retry` | Retry case `PARTIAL/FAILED` |
| `PATCH` | `/api/v1/analysis/cases/{id}/outcome` | Ghi kết quả sau phân tích |
| `POST` | `/api/v1/analysis/search` | Tìm case bằng text/ảnh + filter |
| `POST` | `/api/v1/rag/query/multimodal` | Hỏi đáp có citation, kèm ảnh tùy chọn |

Frontend có trang `/chat` để hỏi trên Knowledge RAG và quản lý nội dung đã nạp.
Trang dùng các API trên, không tạo một RAG pipeline khác. Tài liệu chỉ xuất hiện
trong câu trả lời sau khi trạng thái là `COMPLETED`. Nếu API yêu cầu
`X-Admin-Key`, nhập key trong ô trên trang; key chỉ nằm trong bộ nhớ tab và không
được lưu vào localStorage. Mỗi câu hỏi hiện là một lượt truy vấn độc lập. Sau khi
cập nhật backend, restart API để các route quản lý mới được đăng ký.

### Dữ liệu nến & chỉ báo

FCData chỉ trả nến ngày. Khung `1W` được dựng từ nến 1D đã đóng khi đọc (tuần chỉ đóng sau thứ Sáu 15:00 giờ VN), không lưu bảng riêng.

| Method | Endpoint | Dùng để |
|---|---|---|
| `GET` | `/api/v1/market/symbols?q=VIC` | Tìm mã theo ticker hoặc tên công ty |
| `GET` | `/api/v1/market/candles/{symbol}?timeframe=1D\|1W` | Nến đã đóng |
| `GET` | `/api/v1/indicators/{symbol}/series?timeframe=1W&indicator=ichimoku&indicator=divergence` | Nến + chuỗi chỉ báo cho chart trong một request |
| `GET` | `/api/v1/indicators/{symbol}/series?...&end=2023-06-01T00:00:00Z` | Nạp lùi lịch sử theo mốc thời gian |
| `GET` | `/api/v1/indicators/divergences/scan?days=1` | Quét mã có phân kỳ đã xác nhận trong N phiên |

Response của endpoint chart có thêm `local_divergences` và `structural_divergences`
(`events` vẫn giữ nguyên cho client cũ). Mỗi structural có `member_ids` để client
ẩn đúng các local đã được gộp.
| `POST` | `/api/v1/market/backfill` | Nạp lịch sử, chỉ nhận `1D` |

Backfill sâu để Ichimoku tuần đủ mây (cần khoảng 3 năm nến ngày):

```bash
uv run python -m app.workers.backfill --symbols SSI,FPT --days 1200
```

### Realtime

- WS: `/api/v1/ws`, channel `market:{SYMBOL}:1D|1W`; BE gộp tick FCData và publish `candle` state `PROVISIONAL`.
- Nhịp: **1s** khi đang xem 1 mã (`CHART_REALTIME_SINGLE_SYMBOL_SECONDS`), 2s khi nhiều mã.
- Chỉ báo nến đang chạy do BE tính bằng **TA-Lib** (ichimoku, macd, rsi, adx) và gửi kèm `indicators` — không persist, không dùng cho claim verification.
- Khi FCData `ssiReady=false`, chart vẫn cập nhật bằng snapshot `DailyOhlc` mỗi 5s.

### Chart (FE)

Mọi chỉ báo đều nằm trong modal **Indicator** của chart, giá trị lấy từ BE:

| Chỉ báo | Hiển thị |
|---|---|
| Ichimoku | Tenkan, Kijun, Senkou A/B (dời +26 nến), Chikou (-26), mây Kumo; icon bánh răng mở modal cài đặt; logo giữa chart đã tắt |
| MACD | DIF/DEA/histogram + tam giác mua/bán tại điểm DIF cắt DEA |
| RSI(14) | Một đường (style mặc định); mốc 70 và 30 cùng màu xám nét đứt, mốc 50 màu xanh dương; tô vùng khi vượt 70 hoặc dưới 30 |
| DMI | +DI, -DI, ADX; tô vùng trên 50 và dưới 10 |
| Divergence local | Regular nét liền 1.5px, hidden nét đứt 1.5px (MACD/RSI); khung 1W dùng pivot 3 nến |
| Divergence cấu trúc | Gộp 2+ local liên tiếp cùng chiều/loại/chỉ báo trong một cấu trúc giá: nét chấm 2.5px + nhãn `STR`; các local thành phần được ẩn; bánh răng mở cài đặt |

**Công cụ vẽ (thêm vào thanh vẽ của Pro)**
- Sóng Elliott (3/5/8/tuỳ ý), ABCD, XABCD.
- **Thước đo A→B**: hiện % thay đổi, chênh lệch giá và số ngày (`day`).
- **Fibonacci retracement**: bật/tắt từng mức (0, 23.6, 38.2, 50, 61.8, 78.6, 100%), lưu vào localStorage.
- `Segment` = A→B không kéo dài; `Ray`/`Trend Line` mới kéo dài.

**Lưu trên trình duyệt (localStorage)**
- `/series` mặc định `include_provisional=true` (1D): trả thêm nến đang chạy `source=fcdata:provisional` + chỉ báo tạm tính, kèm `provisional`, `last_confirmed_at`.
- Quét phân kỳ: `uv run python -m app.workers run divergence-scan --param days=1` (chỉ nến đã đóng).
- Worker realtime (tuỳ chọn): `uv run python -m app.workers realtime --symbols FPT,SSI` (role mới, không ghi nến minute vào DB).
- `bookfin:chart-settings:v1`: đường Ichimoku, phân kỳ ẩn, danh sách chỉ báo đang bật, override từng chỉ báo và các mức Fibonacci — reload không phải cài lại.
- `bookfin:overlays:v1:{symbol}:{timeframe}`: nét vẽ overlay (sóng Elliott, ABCD, XABCD…) theo từng mã/khung; nút **Xoá nét vẽ** xoá cả nét vẽ và bản lưu của mã/khung hiện tại.
- Cần chạy `npm install` (postinstall patch `scripts/patch-klinecharts-pro.mjs` bổ sung `getChart()` cho Pro để đọc/khôi phục overlay; nếu thiếu patch app vẫn chạy, chỉ không lưu nét vẽ).

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
