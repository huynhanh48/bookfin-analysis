# Stock Research API

Backend Python phân tích chứng khoán Việt Nam, dữ liệu lấy từ **BookFin FCData Gateway**
(`https://fcdata.bookfin.vn`). Dự án không gọi thẳng SSI và không sửa service cũ.

Luồng phụ thuộc: `router → service → repository → PostgreSQL`, trong đó service còn gọi
`fcdata/` (gateway) và `indicators/` (tính toán thuần).

## Tech stack

- Python 3.12, FastAPI, Uvicorn
- Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x async + asyncpg + Alembic
- PostgreSQL 17 + pgvector, Redis 8 (tuỳ chọn)
- httpx (REST), websockets (realtime), numpy, TA-Lib
- uv, Ruff, Pytest + pytest-asyncio + respx
- Docker Compose: PostgreSQL, Redis, API và worker

## Cấu trúc thư mục

| Thư mục | Mô tả |
|---------|-------|
| `src/app/routers` | Nhận request, trả response |
| `src/app/services` | Business logic |
| `src/app/repositories` | Truy vấn database |
| `src/app/models` | SQLAlchemy ORM |
| `src/app/schemas` | Pydantic v2 DTO |
| `src/app/fcdata` | Adapter REST + WebSocket tới gateway |
| `src/app/indicators` | Chỉ báo và cấu trúc thị trường (hàm thuần) |
| `src/app/workers` | Worker backfill, realtime, precompute |
| `src/app/core` | Config, database, redis, logging, exception, DI |
| `src/app/middleware` | Request id, access log |
| `alembic` | Migration |
| `tests` | Unit test và integration test |
| `references` | Contract gateway và script Pine tham chiếu |

## Cài đặt

```bash
uv sync
cp .env.example .env
```

Sửa `POSTGRES_PASSWORD` trong `.env` nếu cần. `DATABASE_URL` dùng `localhost` khi chạy
trên máy; khi chạy trong Docker, compose tự đổi host thành `postgres`.

## Docker

Docker Compose dựng PostgreSQL, Redis (tuỳ chọn), API và worker. API và worker **mount
`./src`**, nên sửa code là container cập nhật ngay: API chạy `uvicorn --reload`, worker
dùng đúng source đã mount.

```bash
docker compose up -d --build                 # postgres + api
docker compose --profile cache up -d         # thêm redis
docker compose ps
docker compose logs -f api
```

Apply migration trong container:

```bash
docker compose run --rm migrate              # alembic upgrade head
```

Chạy worker (một lần, dùng chung source đã mount):

```bash
docker compose run --rm worker python -m app.workers.backfill --sync-symbols --symbols FPT,HPG --days 400
docker compose run --rm worker python -m app.workers.indicator_worker --symbols FPT,HPG
docker compose run --rm worker python -m app.workers.realtime --symbols FPT --indices VNINDEX
```

Test và lint trong container:

```bash
docker compose exec api pytest -q -m "not integration"
docker compose exec api ruff check .
```

Kiểm tra hạ tầng:

```bash
docker compose exec postgres psql -U stock -d stock_research \
  -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"
docker compose exec redis redis-cli ping
docker compose --profile cache --profile worker --profile tools down   # giữ nguyên volumes
```

`docker/postgres/init.sql` chỉ chạy khi PostgreSQL tạo data directory mới; mọi thay đổi
schema sau đó phải đi qua Alembic.

## Chạy trực tiếp bằng uv

```bash
uv run alembic upgrade head
uv run uvicorn app.main:app --reload                  # http://localhost:8000/docs
uv run python -m app.workers.backfill --sync-symbols --symbols FPT --days 400
uv run python -m app.workers.indicator_worker --symbols FPT
uv run python -m app.workers.realtime --symbols FPT --indices VNINDEX
```

Các lệnh Makefile tương ứng: `make up`, `make docker-migrate`, `make worker-backfill`,
`make worker-indicators`, `make worker-realtime`, `make docker-test`, `make dev`.

## API chính

```bash
curl localhost:8000/health
curl localhost:8000/ready
curl "localhost:8000/api/v1/market/symbols?limit=5"
curl "localhost:8000/api/v1/market/candles/FPT?timeframe=1D&limit=10"
curl "localhost:8000/api/v1/market/quotes/FPT"
curl "localhost:8000/api/v1/indicators/FPT?timeframe=1D"
curl "localhost:8000/api/v1/indicators/FPT/structure?timeframe=1D"
curl -X POST localhost:8000/api/v1/market/backfill -H "Content-Type: application/json" \
  -H "X-Admin-Key: $ADMIN_API_KEY" -d '{"symbols":["FPT"],"timeframe":"1D"}'
```

`/health` không truy cập database hay gateway. `/ready` kiểm tra PostgreSQL, Redis khi
bật. Endpoint `backfill` yêu cầu `X-Admin-Key` khi `ADMIN_API_KEY` được đặt.

## Lint và test

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -m "not integration"
uv run pytest -q -m integration        # cần PostgreSQL đang chạy
```

## Lưu ý về dữ liệu

- Bảng `candles` **chỉ chứa nến đã đóng**. Nến đang hình thành được giữ ở Redis hoặc
  trong bộ nhớ với trạng thái `FORMING` và không bao giờ ghi đè bản ghi đã đóng.
- Mọi bản ghi nến đều có `price_basis` (`RAW`/`ADJUSTED`); không trộn hai loại trong
  cùng một chuỗi khi tính chỉ báo hoặc cấu trúc.
- Chỉ báo và cấu trúc thị trường được precompute theo nến và lưu kèm `engine_version`
  cùng hash tham số, nên khi logic đổi thì kết quả cũ vẫn đối chiếu được.
- `break_bias` là bias phá cấu trúc (giống biến `trend` trong Pine), **không** phải xác
  nhận đảo chiều. `confirmed_trend` mới là xu hướng đã xác nhận theo cấu trúc HH+HL/LH+LL.
- Khi gateway lỗi, API trả bản ghi cũ kèm `as_of`, `is_stale` và `note` thay vì trả rỗng
  giả thành công.

## Trạng thái

Phase 1 đã có: gateway REST + WebSocket, backfill và nến đã đóng trong PostgreSQL,
indicator engine (TA-Lib), market structure engine, worker, API và Docker.
RAG, LangGraph và AI Agent chưa triển khai.
