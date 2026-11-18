# syntax=docker/dockerfile:1
# Image cho API và worker. Database và cache do docker-compose cung cấp.
#
# Hai target:
#   - `runtime` (mặc định): bản production, không cài dev dependency.
#   - `dev`: cài đủ dev dependency (pytest, ruff, respx) và chạy uvicorn --reload;
#     docker-compose mount ./src vào container nên sửa code là chạy lại ngay.
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY --from=uv /uv /uvx /bin/

# Cài dependency trước để tận dụng cache layer khi source thay đổi.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

# Chạy bằng user không phải root.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


FROM base AS dev

# Dev cần pytest/ruff/respx để chạy test và lint ngay trong container.
USER root
RUN uv sync --frozen \
    && chown -R appuser:appuser /app
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
