"""Worker precompute chỉ báo (tương thích ngược).

Entrypoint mới: `python -m app.workers run recompute-indicators ...`.

Chạy độc lập với API::

    uv run python -m app.workers.indicator_worker --symbols FPT,HPG
    uv run python -m app.workers.indicator_worker --all --limit 50
"""

import argparse
import asyncio
from datetime import datetime

from app.core.config import get_settings
from app.workers.bootstrap import close_worker_resources, execute_job, prepare


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh của worker."""
    parser = argparse.ArgumentParser(description="Precompute chỉ báo và cấu trúc thị trường.")
    parser.add_argument("--symbols", default="", help="Danh sách mã, phân tách bằng dấu phẩy.")
    parser.add_argument("--all", action="store_true", help="Chạy cho toàn bộ mã đang hoạt động.")
    parser.add_argument("--limit", type=int, default=200, help="Số mã tối đa khi dùng --all.")
    parser.add_argument("--timeframe", default="1D", choices=["1D", "1W"], help="Khung thời gian.")
    parser.add_argument(
        "--end",
        type=datetime.fromisoformat,
        default=None,
        help="Replay cutoff ISO8601 with timezone; excludes unclosed bars at that time.",
    )
    return parser.parse_args()


async def run(
    symbols: list[str], timeframe: str, run_all: bool, limit: int, end: datetime | None = None
) -> None:
    """Tính lại chỉ báo cho danh sách mã qua worker engine.

    Args:
        symbols: Danh sách mã chỉ định.
        timeframe: Khung thời gian.
        run_all: Chạy cho toàn bộ mã đang hoạt động.
        limit: Số mã tối đa khi chạy toàn bộ.
        end: Aware historical replay cutoff; omitted for latest closed candles.
    """
    settings = get_settings()
    prepare(settings)
    params: dict[str, object] = {
        "symbols": symbols,
        "include_all": run_all,
        "limit": limit,
        "timeframe": timeframe,
    }
    if end is not None:
        params["end"] = end
    outcome = await execute_job(settings, "recompute-indicators", params)
    if outcome.status in {"failed", "timed_out"}:
        raise SystemExit(1)


async def main() -> None:
    """Entrypoint của worker."""
    args = _parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    try:
        await run(symbols, args.timeframe, args.all, args.limit, args.end)
    finally:
        await close_worker_resources()


if __name__ == "__main__":
    asyncio.run(main())
