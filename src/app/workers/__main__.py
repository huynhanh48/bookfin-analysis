"""Cho phép chạy `python -m app.workers ...`."""

from app.workers.cli import main

raise SystemExit(main())
