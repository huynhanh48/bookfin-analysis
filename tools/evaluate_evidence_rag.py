"""Evaluate deterministic claim fixtures and optional captured retrieval/answer runs.

No network requests. Missing live predictions/usage remain null, never fabricated zero.
Predictions JSONL: query_id, ranked_ids, citation_ids, asserted_claims,
unsupported_claims, latency_seconds, cost_usd. Human labeling is required for
asserted/unsupported claim counts; citation-ID membership alone is not entailment.
"""

import argparse
import json
import math
from pathlib import Path
from statistics import mean

from app.schemas.evidence import TechnicalClaim
from app.services.technical_verification_service import TechnicalVerificationService


def ranking_metrics(ranked: list[str], relevance: dict[str, int], k: int) -> dict[str, float]:
    """Compute Recall@K, reciprocal rank and graded nDCG; deduplicate IDs first."""
    ranked = list(dict.fromkeys(ranked))[:k]
    relevant = {key for key, value in relevance.items() if value > 0}
    recall = len(set(ranked) & relevant) / len(relevant) if relevant else 0.0
    reciprocal = next((1 / rank for rank, key in enumerate(ranked, 1) if key in relevant), 0.0)
    gain = sum(
        (2 ** relevance.get(key, 0) - 1) / math.log2(rank + 1) for rank, key in enumerate(ranked, 1)
    )
    ideal = sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(sorted(relevance.values(), reverse=True)[:k], 1)
    )
    return {
        "recall_at_k": recall,
        "reciprocal_rank": reciprocal,
        "ndcg_at_k": gain / ideal if ideal else 0.0,
    }


def evaluate(dataset: dict, predictions: list[dict], k: int = 5) -> dict:
    """Report measured fixture checks separately from unmeasured provider quality/cost."""
    correct = total = 0
    verifier = TechnicalVerificationService()
    for case in dataset["cases"]:
        for row in case["claims"]:
            claim = TechnicalClaim.model_validate(
                {key: value for key, value in row.items() if key != "expected"}
            )
            actual = verifier.verify([claim], case["snapshot"], f"technical:{case['id']}")[0]
            total += 1
            correct += actual.verification_status == row["expected"]
    known = {query["id"]: query for query in dataset["queries"]}
    retrieval = []
    citation_correct = citation_total = 0
    unsupported = asserted = 0
    latencies = []
    costs = []
    for row in predictions:
        reference = known[row["query_id"]]
        retrieval.append(ranking_metrics(row["ranked_ids"], reference["relevance"], k))
        for identifier in row.get("citation_ids", []):
            citation_total += 1
            citation_correct += identifier in reference["allowed_citations"]
        unsupported += row.get("unsupported_claims", 0)
        asserted += row.get("asserted_claims", 0)
        if row.get("latency_seconds") is not None:
            latencies.append(row["latency_seconds"])
        if row.get("cost_usd") is not None:
            costs.append(row["cost_usd"])
    return {
        "dataset": dataset["version"],
        "scope": "synthetic fixtures; live providers not exercised",
        "deterministic_claim_accuracy": correct / total if total else None,
        "claim_count": total,
        "retrieval_queries_measured": len(retrieval),
        "recall_at_k": mean(r["recall_at_k"] for r in retrieval) if retrieval else None,
        "mrr": mean(r["reciprocal_rank"] for r in retrieval) if retrieval else None,
        "ndcg_at_k": mean(r["ndcg_at_k"] for r in retrieval) if retrieval else None,
        "citation_id_correctness": citation_correct / citation_total if citation_total else None,
        "citation_entailment": "requires human review",
        "unsupported_claim_rate": unsupported / asserted if asserted else None,
        "mean_end_to_end_latency_seconds": mean(latencies) if latencies else None,
        "measured_api_cost_usd": sum(costs) if costs else None,
        "k": k,
    }


def main() -> None:
    """Read a fixed benchmark and optional recorded predictions; write reproducible JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset", type=Path, default=Path("tests/fixtures/evidence_rag/financial_eval.json")
    )
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path, default=Path("docs/evidence-rag/evaluation.json"))
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()
    predictions = (
        [json.loads(line) for line in args.predictions.read_text().splitlines() if line.strip()]
        if args.predictions
        else []
    )
    result = evaluate(json.loads(args.dataset.read_text()), predictions, args.k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
