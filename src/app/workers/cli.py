"""CLI thống nhất cho worker engine.

Cùng một Docker image chạy nhiều role bằng command khác nhau::

    python -m app.workers list
    python -m app.workers run ingest-close --param symbols=FPT,HPG
    python -m app.workers run recompute-indicators --params '{"symbols": ["FPT"]}'
    python -m app.workers scheduler --once
    python -m app.workers realtime --symbols FPT,HPG
    python -m app.workers health --role scheduler --max-age 120
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from typing import Any

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.workers.heartbeat import check_role_health
from app.workers.schedule import describe_schedule

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Tạo parser CLI.

    Returns:
        Parser với các subcommand `list`, `run`, `scheduler`, `realtime`, `health`.
    """
    parser = argparse.ArgumentParser(prog="python -m app.workers", description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    list_parser = subcommands.add_parser("list", help="Liệt kê job đã đăng ký.")
    list_parser.add_argument("--json", action="store_true", help="In JSON thay vì bảng.")

    run_parser = subcommands.add_parser("run", help="Chạy một job một lần (executor).")
    run_parser.add_argument("job", help="Tên job, ví dụ ingest-close.")
    run_parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Tham số job; value được parse JSON nếu hợp lệ. Có thể lặp lại.",
    )
    run_parser.add_argument(
        "--params",
        default="",
        help='JSON object đầy đủ, ví dụ \'{"symbols": ["FPT"], "days": 5}\'.',
    )
    run_parser.add_argument(
        "--trigger",
        choices=["manual", "scheduler", "realtime"],
        default="manual",
        help="Nguồn kích hoạt; mặc định manual (bỏ qua idempotency ngày).",
    )
    run_parser.add_argument("--force", action="store_true", help="Bỏ qua idempotency marker.")

    scheduler_parser = subcommands.add_parser("scheduler", help="Chạy role scheduler.")
    scheduler_parser.add_argument("--list", action="store_true", help="Liệt kê job có lịch.")
    scheduler_parser.add_argument("--run", default="", metavar="JOB", help="Chạy ngay một job.")
    scheduler_parser.add_argument(
        "--now", action="store_true", help="Tương thích cũ; job --run luôn chạy ngay."
    )
    scheduler_parser.add_argument(
        "--run-on-start", action="store_true", help="Chạy mọi job có lịch khi khởi động."
    )
    scheduler_parser.add_argument("--once", action="store_true", help="Chỉ kiểm tra một vòng.")

    realtime_parser = subcommands.add_parser("realtime", help="Chạy role realtime FCData.")
    realtime_parser.add_argument("--symbols", default="", help="Mã cổ phiếu, phân tách bằng phẩy.")
    realtime_parser.add_argument("--indices", default="", help="Mã chỉ số, phân tách bằng phẩy.")
    realtime_parser.add_argument(
        "--timeframe-minutes",
        type=int,
        default=1,
        help="Khung gom tick cho snapshot provisional.",
    )
    realtime_parser.add_argument(
        "--recompute-on-close",
        action="store_true",
        help="Tương thích cũ; trigger sau giờ đóng mặc định đã bật.",
    )
    realtime_parser.add_argument(
        "--no-session-trigger",
        action="store_true",
        help="Tắt ingest/recompute khi phiên đóng.",
    )

    health_parser = subcommands.add_parser("health", help="Kiểm tra heartbeat của role.")
    health_parser.add_argument("--role", required=True, help="Role cần kiểm tra.")
    health_parser.add_argument(
        "--max-age", type=float, default=120, help="Tuổi tối đa của heartbeat (giây)."
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entrypoint CLI.

    Args:
        argv: Tham số dòng lệnh; mặc định đọc từ `sys.argv`.

    Returns:
        Exit code process.
    """
    args = build_parser().parse_args(argv)
    if args.command == "health":
        return _health(args)
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        return 130


def _health(args: argparse.Namespace) -> int:
    """In trạng thái heartbeat và trả exit code 0/1."""
    settings = get_settings()
    ok, payload = check_role_health(settings, args.role, max_age_seconds=args.max_age)
    print(json.dumps({"ok": ok, "role": args.role, "heartbeat": payload}, default=str))
    return 0 if ok else 1


async def _run_async(args: argparse.Namespace) -> int:
    """Dispatch các subcommand async."""
    from app.workers.bootstrap import (
        close_worker_resources,
        execute_job,
        prepare,
    )

    settings = get_settings()
    prepare(settings)
    try:
        if args.command == "list":
            return _list_jobs(as_json=args.json)
        if args.command == "run":
            params = _parse_params(args.params, args.param)
            outcome = await execute_job(
                settings, args.job, params, trigger=args.trigger, force=args.force
            )
            print(json.dumps(outcome.as_dict(), ensure_ascii=False, default=str))
            return 0 if outcome.ok else 1
        if args.command == "scheduler":
            return await _scheduler(args, settings)
        if args.command == "realtime":
            return await _realtime(args, settings)
    finally:
        await close_worker_resources()
    return 2


def _list_jobs(*, as_json: bool = False) -> int:
    """In danh sách job từ registry."""
    from app.workers.bootstrap import build_registry

    registry = build_registry()
    specs = [*registry.scheduled(), *(spec for spec in registry.all() if spec.schedule is None)]
    if as_json:
        print(
            json.dumps(
                [
                    {
                        "name": spec.name,
                        "schedule": describe_schedule(spec.schedule),
                        "description": spec.description,
                        "timeout_seconds": spec.timeout_seconds,
                        "retry_attempts": spec.retry.attempts,
                        "idempotency": spec.idempotency,
                    }
                    for spec in specs
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"{'JOB':<24} {'SCHEDULE':<20} DESCRIPTION")
    for spec in specs:
        print(f"{spec.name:<24} {describe_schedule(spec.schedule):<20} {spec.description}")
    return 0


async def _scheduler(args: argparse.Namespace, settings: Settings) -> int:
    """Chạy role scheduler hoặc các thao tác tương thích `--list`/`--run`."""
    from app.workers.bootstrap import (
        build_engine,
        build_registry,
        execute_job,
        install_stop_handlers,
    )
    from app.workers.roles.scheduler import SchedulerRole

    registry = build_registry()
    if args.list:
        for spec in registry.scheduled():
            print(f"{spec.name:<24} {describe_schedule(spec.schedule):<20} {spec.description}")
        return 0
    if args.run:
        if args.run not in registry:
            print(f"Không có job {args.run!r}. Dùng `list` để xem.", file=sys.stderr)
            return 2
        outcome = await execute_job(settings, args.run, trigger="manual", force=True)
        print(json.dumps(outcome.as_dict(), ensure_ascii=False, default=str))
        return 0 if outcome.ok else 1
    if not settings.scheduler_enabled:
        logger.info("scheduler_disabled")
        return 0

    role = SchedulerRole(settings, build_engine(settings, registry=registry), registry)
    stop = asyncio.Event()
    install_stop_handlers(stop)
    await role.run(
        run_on_start=args.run_on_start or settings.scheduler_run_on_start,
        once=args.once,
        stop_event=stop,
    )
    return 0


async def _realtime(args: argparse.Namespace, settings: Settings) -> int:
    """Chạy role realtime."""
    from app.workers.bootstrap import build_engine, install_stop_handlers
    from app.workers.roles.realtime import RealtimeRole

    symbols = _symbols(args.symbols)
    indices = _symbols(args.indices)
    role = RealtimeRole(
        settings,
        build_engine(settings),
        symbols=symbols,
        indices=indices,
        timeframe_minutes=max(1, args.timeframe_minutes),
        trigger_on_session_close=not args.no_session_trigger,
    )
    stop = asyncio.Event()
    install_stop_handlers(stop)
    await role.run(stop)
    return 0


def _symbols(value: str) -> list[str]:
    """Tách chuỗi mã phân tách bằng dấu phẩy."""
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _parse_params(raw_json: str, raw_params: list[str]) -> dict[str, Any]:
    """Ghép `--params` JSON và các `--param key=value`.

    Args:
        raw_json: JSON object hoặc chuỗi rỗng.
        raw_params: Danh sách `key=value`.

    Returns:
        Dict params thô để Pydantic validate.

    Raises:
        SystemExit: Khi JSON sai hoặc thiếu dấu `=`.
    """
    params: dict[str, Any] = {}
    if raw_json.strip():
        try:
            decoded = json.loads(raw_json)
        except ValueError as exc:
            raise SystemExit(f"--params không phải JSON hợp lệ: {exc}") from exc
        if not isinstance(decoded, dict):
            raise SystemExit("--params phải là JSON object")
        params.update(decoded)
    for item in raw_params:
        if "=" not in item:
            raise SystemExit(f"--param phải có dạng key=value: {item!r}")
        key, raw = item.split("=", 1)
        try:
            value: Any = json.loads(raw)
        except ValueError:
            value = raw
        params[key.strip()] = value
    return params


if __name__ == "__main__":
    raise SystemExit(main())
