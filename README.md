# Stock Research API

Backend Python phục vụ hệ thống AI phân tích chứng khoán Việt Nam, xây dựng bằng FastAPI
theo Layer-Based Architecture (routers → services → repositories).

## Tech Stack

- Python 3.12
- FastAPI + Pydantic v2 + Pydantic Settings
- SQLAlchemy 2.x (async) + asyncpg + Alembic
- PostgreSQL 17 + pgvector
- Redis 8
- httpx
- uv (quản lý dependencies), Ruff (lint/format), Pytest + pytest-asyncio
- Docker Compose (PostgreSQL, Redis)

## Cấu trúc thư mục

| Thư mục | Mô tả |
|---------|-------|
| `src/app/routers` | Nhận request và trả response |
| `src/app/services` | Business logic |
| `src/app/repositories` | Truy vấn database |
| `src/app/models` | SQLAlchemy ORM models |
| `src/app/schemas` | Pydantic v2 schemas |
| `src/app/fcdata` | Tích hợp dữ liệu SSI FCData |
| `src/app/indicators` | Hàm tính toán chỉ báo kỹ thuật |
| `src/app/workers` | Tác vụ chạy nền |
| `src/app/middleware` | Middleware request/response |
| `src/app/common` | Tiện ích dùng chung |
| `alembic` | Database migrations |
| `tests` | Unit và integration tests |
| `docker` | Script khởi tạo cho container |

Luồng phụ thuộc: router → service → repository → model. Router không truy vấn database
trực tiếp, repository không chứa business logic.

## Cài đặt

Cài dependencies và tạo môi trường ảo:

    uv sync

Tạo file môi trường:

    cp .env.example .env

Đổi `POSTGRES_PASSWORD` trong `.env` rồi cập nhật `DATABASE_URL` cho khớp.

## Các lệnh thường dùng

Chạy development server:

    uv run uvicorn app.main:app --reload

API documentation: http://localhost:8000/docs

Kiểm tra lint:

    uv run ruff check .

Format code:

    uv run ruff format .

Kiểm tra format:

    uv run ruff format --check .

Chạy test:

    uv run pytest -q

Chạy migration:

    uv run alembic upgrade head

Hoặc dùng Makefile: `make install`, `make run`, `make lint`, `make format`, `make test`,
`make migrate`.

## Docker Infrastructure

Docker Compose cung cấp database và cache; backend chạy local bằng `uv`.

Khởi động PostgreSQL:

    docker compose up -d postgres

Khởi động PostgreSQL và Redis:

    docker compose --profile cache up -d

Kiểm tra trạng thái container:

    docker compose ps

Kiểm tra pgvector:

    docker compose exec postgres psql -U stock -d stock_research \
      -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"

Kiểm tra Redis:

    docker compose exec redis redis-cli ping

Dừng dịch vụ (giữ nguyên named volumes):

    docker compose --profile cache down

`docker/postgres/init.sql` chỉ chạy khi PostgreSQL khởi tạo data directory mới. Với
database đã tồn tại, bật extension bằng migration hoặc chạy SQL riêng. Mọi thay đổi
schema sau này phải đi qua Alembic.

## Trạng thái

Project đã khởi tạo: cấu trúc layer, cấu hình, Alembic, CI và Docker infrastructure.
Market Data Engine, indicators, RAG và AI Agent chưa được triển khai.
