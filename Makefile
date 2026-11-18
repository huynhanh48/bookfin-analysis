.PHONY: install run lint format format-check test migrate revision docker-up docker-up-cache docker-down docker-ps docker-pgvector docker-redis

install:
	uv sync

run:
	uv run uvicorn app.main:app --reload

lint:
	uv run ruff check .

format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

test:
	uv run pytest -q

migrate:
	uv run alembic upgrade head

revision:
	uv run alembic revision --autogenerate -m "$(m)"

docker-up:
	docker compose up -d postgres

docker-up-cache:
	docker compose --profile cache up -d

docker-down:
	docker compose --profile cache down

docker-ps:
	docker compose ps

docker-pgvector:
	docker compose exec postgres psql -U $${POSTGRES_USER:-stock} -d $${POSTGRES_DB:-stock_research} -c "SELECT extversion FROM pg_extension WHERE extname = 'vector';"

docker-redis:
	docker compose exec redis redis-cli ping
