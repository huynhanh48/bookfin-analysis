"""Reproducible HTTP benchmark for analysis-case ingestion; no provider code imports."""

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "NEEDS_REVIEW"}


def percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolated percentile for small benchmark samples."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


async def wait_for_job(
    client: httpx.AsyncClient, base_url: str, job_id: str, headers: dict, timeout: float
) -> dict:
    """Poll the durable job endpoint without sleeping on the server."""
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        response = await client.get(f"{base_url}/analysis/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        job = response.json()["data"]
        if job["status"] in TERMINAL:
            return job
        await asyncio.sleep(2)
    raise TimeoutError(f"job {job_id} did not reach terminal state within {timeout}s")


async def run(args) -> dict:
    base_url = args.base_url.rstrip("/")
    headers = {"X-Admin-Key": args.admin_key} if args.admin_key else {}
    chart_bytes = Path(args.chart).read_bytes() if args.chart else None
    metadata = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "analyzed_at": args.analyzed_at,
        "thesis": args.thesis,
        "source": args.source,
        "price_basis": "RAW",
    }
    runs = []
    async with httpx.AsyncClient(timeout=30) as client:
        for _index in range(args.runs):
            request_started = time.perf_counter()
            files = {"chart": ("chart.png", chart_bytes, "image/png")} if chart_bytes else None
            response = await client.post(
                f"{base_url}/analysis/cases",
                headers=headers,
                data={"metadata": json.dumps(metadata)},
                files=files,
            )
            response.raise_for_status()
            created = response.json()
            case_id = created["data"]["id"]
            job_id = created.get("job_id")
            job = (
                await wait_for_job(client, base_url, job_id, headers, args.timeout)
                if job_id
                else {"status": created["data"]["ingest_status"]}
            )
            detail = await client.get(f"{base_url}/analysis/cases/{case_id}", headers=headers)
            detail.raise_for_status()
            case = detail.json()["data"]
            runs.append(
                {
                    "case_id": case_id,
                    "job_id": job_id,
                    "status": job.get("status"),
                    "request_wall_seconds": time.perf_counter() - request_started,
                    "job_timings": job.get("diagnostics", {}).get("timings", {}),
                    "provider_usage": job.get("diagnostics", {}).get("provider_usage", {}),
                    "case_timings": case.get("enrichment", {})
                    .get("diagnostics", {})
                    .get("timings", {}),
                    "claims": len(case.get("enrichment", {}).get("claims", [])),
                    "embeddings": case.get("enrichment", {}).get("embeddings", []),
                    "normalized_chars": len(case.get("enrichment", {}).get("normalized_text", "")),
                    "warnings": job.get("diagnostics", {}).get("warnings", []),
                    "news_warnings": job.get("diagnostics", {}).get("news_warnings", []),
                }
            )
    totals = [
        float(row["job_timings"].get("total_wall_clock_seconds") or 0)
        for row in runs
        if row["job_timings"]
    ]
    index_times = [
        float(row["job_timings"].get("case_graph_index_seconds") or 0)
        for row in runs
        if row["job_timings"]
    ]
    return {
        "label": args.label,
        "generated_at": datetime.now(UTC).isoformat(),
        "input": {
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "analyzed_at": args.analyzed_at,
            "source": args.source,
            "chart": args.chart,
            "runs": args.runs,
        },
        "runs": runs,
        "summary": {
            "count": len(runs),
            "total_p50_seconds": percentile(totals, 0.5),
            "total_p95_seconds": percentile(totals, 0.95),
            "case_graph_index_p50_seconds": percentile(index_times, 0.5),
            "case_graph_index_p95_seconds": percentile(index_times, 0.95),
            "mean_total_seconds": statistics.mean(totals) if totals else None,
            "provider_requests": sum(int(row["provider_usage"].get("requests", 0)) for row in runs),
            "estimated_cost": sum(
                float(row["provider_usage"].get("estimated_cost", 0)) for row in runs
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--admin-key", default="")
    parser.add_argument("--label", required=True)
    parser.add_argument("--symbol", default="SSI")
    parser.add_argument("--timeframe", default="1D")
    parser.add_argument("--analyzed-at", default="2025-10-20T15:30:00+07:00")
    parser.add_argument(
        "--thesis",
        default="SSI có RSI bearish divergence và volume tăng; cần kiểm tra cấu trúc.",
    )
    parser.add_argument("--source", required=True)
    parser.add_argument("--chart", default="")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    result = asyncio.run(run(args))
    output = Path(args.output) if args.output else Path(f"docs/performance/{args.label}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"written: {output}")


if __name__ == "__main__":
    main()
