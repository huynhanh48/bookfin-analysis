.PHONY: install dev lint format format-check test test-unit test-integration \
	db-up cache-up up up-cache build down ps logs shell docker-migrate \
	docker-test docker-lint worker run-job worker-backfill worker-indicators \
	worker-realtime worker-job migrate revision backfill indicators realtime pgvector redis-ping

# ── Chạy trực tiếp trên máy bằng uv ─────────────────────────────────────────

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

test:
	uv run pytest -q

test-unit:
	uv run pytest -q -m "not integration"

test-integration:
	uv run pytest -q -m integration

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

backfill:
	uv run python -m app.workers.backfill $(args)

indicators:
	uv run python -m app.workers.indicator_worker $(args)

realtime:
	uv run python -m app.workers realtime $(args)

worker:
	uv run python -m app.workers $(args)

run-job:
	uv run python -m app.workers run $(args)

jobs:
	uv run python -m app.workers list

# ── Docker Compose ──────────────────────────────────────────────────────────
# api và worker mount ./src nên sửa code là container cập nhật ngay.

build:
	docker compose build api

up:
	docker compose up -d --build

up-cache:
	docker compose --profile cache up -d --build

down:
	docker compose --profile cache --profile worker --profile tools down

ps:
	docker compose --profile cache ps

logs:
	docker compose logs -f api

shell:
	docker compose exec api bash

docker-migrate:
	docker compose run --rm migrate

docker-test:
	docker compose exec api pytest -q -m "not integration"

docker-lint:
	docker compose exec api ruff check .

worker-backfill:
	docker compose run --rm worker python -m app.workers.backfill $(args)

worker-indicators:
	docker compose run --rm worker python -m app.workers.indicator_worker $(args)

worker-realtime:
	docker compose run --rm worker python -m app.workers realtime $(args)

worker-job:
	docker compose run --rm worker python -m app.workers run $(args)

db-up:
	docker compose up -d postgres

cache-up:
	docker compose --profile cache up -d

pgvector:
	docker compose exec postgres psql -U $${POSTGRES_USER:-stock} -d $${POSTGRES_DB:-stock_research} -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"

redis-ping:
	docker compose exec redis redis-cli ping
