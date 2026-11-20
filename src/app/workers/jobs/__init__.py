"""Nạp các job module để decorator `register_job` chạy đúng một lần.

Thứ tự import quyết định thứ tự job có lịch: ingestion trước indicators, nhờ đó
khi scheduler khởi động muộn sau giờ đóng cửa, `ingest-close` luôn chạy trước
`recompute-indicators`.
"""

from app.workers.jobs import indicators, ingestion, rag  # noqa: F401

__all__ = ["indicators", "ingestion", "rag"]
