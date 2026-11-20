"""Quét phân kỳ đã xác nhận (tương thích ngược).

Entrypoint mới: `python -m app.workers run divergence-scan --param days=1`.

Dùng engine divergence hiện có (RSI/MACD + swing giá), chỉ tính trên nến đã đóng
nên không tạo tín hiệu từ nến đang chạy.
"""

import argparse
import asyncio
from datetime import datetime

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.indicator_service import DivergenceScanRow
from app.workers.bootstrap import close_worker_resources, prepare
from app.workers.jobs.indicators import DivergenceScanParams, scan_confirmed_divergences

logger = get_logger(__name__)


def _parse_args() -> argparse.Namespace:
    """Đọc tham số dòng lệnh."""
    parser = argparse.ArgumentParser(description="Quét mã có phân kỳ đã xác nhận.")
    parser.add_argument("--symbols", default="", help="Danh sách mã, phân tách bằng dấu phẩy.")
    parser.add_argument("--days", type=int, default=1, help="Số phiên gần nhất cần quét.")
    parser.add_argument("--limit", type=int, default=600, help="Số mã tối đa khi quét toàn bộ.")
    parser.add_argument("--timeframe", default="1D", choices=["1D", "1W"], help="Khung thời gian.")
    return parser.parse_args()


async def run(symbols: list[str], days: int, limit: int, timeframe: str) -> list[DivergenceScanRow]:
    """Quét phân kỳ và trả danh sách kết quả.

    Args:
        symbols: Danh sách mã chỉ định; rỗng nghĩa là toàn bộ mã đang hoạt động.
        days: Số phiên gần nhất.
        limit: Số mã tối đa.
        timeframe: Khung thời gian.

    Returns:
        Danh sách `DivergenceScanRow` đã sắp xếp theo thời điểm xác nhận.
    """
    settings = get_settings()
    prepare(settings)
    params = DivergenceScanParams(
        symbols=symbols or None, days=days, limit=limit, timeframe=timeframe
    )
    return await scan_confirmed_divergences(settings, params)


def _print(rows: list[DivergenceScanRow]) -> None:
    """In bảng kết quả ra stdout."""
    if not rows:
        print("Không có phân kỳ nào trong khoảng thời gian đã chọn.")
        return
    header = f"{'MÃ':<6} {'CHIỀU':<8} {'LOẠI':<8} {'CHỈ BÁO':<12} {'TỪ':<11} {'ĐẾN':<11} {'GIÁ':<20} {'XÁC NHẬN':<11}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row.symbol:<6} {row.direction:<8} {row.kind:<8} {row.indicator:<12} "
            f"{row.start_time:%Y-%m-%d} {row.end_time:%Y-%m-%d} "
            f"{row.start_price:>8.2f} → {row.end_price:>8.2f} "
            f"{row.confirmed_at:%Y-%m-%d}"
        )
    print(f"\nTổng: {len(rows)} tín hiệu / {len({row.symbol for row in rows})} mã")


async def main() -> None:
    """Entrypoint của worker."""
    args = _parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    started = datetime.now()
    try:
        rows = await run(symbols, args.days, args.limit, args.timeframe)
        _print(rows)
        logger.info(
            "divergence_scan_done",
            extra={
                "symbols": len(rows),
                "elapsed_seconds": round((datetime.now() - started).total_seconds(), 2),
            },
        )
    finally:
        await close_worker_resources()


if __name__ == "__main__":
    asyncio.run(main())
