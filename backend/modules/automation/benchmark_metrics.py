"""Explicit denominators; absent measurements stay absent."""

import math
from collections import defaultdict


def summarize(outcomes):
    tasks = defaultdict(list)
    for item in outcomes:
        tasks[item["task"]].append(item)
    result = {}
    positive = {"same", "eligible", "stock", "ingredient"}
    for task, rows in tasks.items():
        n = len(rows)
        decisions = [row for row in rows if row["status"] == "ok"]
        times = sorted(row["latency_ms"] for row in rows if row.get("latency_ms") is not None)
        costs = [row.get("usage", {}).get("cost_usd") for row in rows]

        def percentile(p):
            return times[max(0, math.ceil(len(times) * p) - 1)] if times else None

        confusion = defaultdict(int)
        for row in rows:
            confusion[f"{row['expected']} -> {row.get('prediction') or row['status']}"] += 1
        correct = sum(row.get("prediction") == row["expected"] for row in decisions)
        result[task] = {
            "support": n,
            "groups": len({row["group"] for row in rows}),
            "correct": correct,
            "valid_responses": sum(row.get("valid_response", False) for row in rows),
            "valid_groups": len({row["group"] for row in rows if row.get("valid_response", False)}),
            "false_positives": sum(
                row.get("prediction") in positive and row["expected"] not in positive for row in decisions
            ),
            "critical_false_positives": sum(
                row["critical_negative"] and row["expected"] not in positive and row.get("prediction") in positive
                for row in decisions
            ),
            "misses": sum(row["expected"] in positive and row.get("prediction") != row["expected"] for row in rows),
            "abstained": sum(row["status"] == "abstain" and row.get("reason") != "unsupported_task" for row in rows),
            "unsupported": sum(row.get("reason") == "unsupported_task" for row in rows),
            "unmeasured": sum(
                row["status"] == "unavailable" and not row.get("network_attempted", True) for row in rows
            ),
            "request_attempts": sum(row.get("network_attempted", False) for row in rows),
            "failures": sum(
                row["status"] in {"invalid", "unavailable", "cancelled"} and row.get("network_attempted", True)
                for row in rows
            ),
            "coverage": len(decisions) / n,
            "accuracy": correct / n,
            "insufficient_answers": sum(row.get("prediction") == "insufficient" for row in decisions),
            "non_insufficient_coverage": sum(row.get("prediction") != "insufficient" for row in decisions) / n,
            "p50_ms": percentile(0.5),
            "p95_ms": percentile(0.95),
            "known_cost_usd": _finite_sum([cost for cost in costs if cost is not None]),
            "total_cost_usd": _finite_sum(costs) if all(cost is not None for cost in costs) else None,
            "unknown_cost_count": sum(cost is None for cost in costs),
            "input_tokens": _tokens(rows, "input_tokens"),
            "output_tokens": _tokens(rows, "output_tokens"),
            "candidate_retrieval_recall": None,
            "review_corrections_saved": None,
            "candidate_scope": "Supplied synthetic candidates only; retrieval not exercised",
            "confusion": dict(confusion),
            "reliability": [
                {
                    "lower": i / 5,
                    "upper": (i + 1) / 5,
                    "count": len(bucket),
                    "accuracy": sum(row.get("prediction") == row["expected"] for row in bucket) / len(bucket)
                    if bucket
                    else None,
                }
                for i in range(5)
                if (
                    bucket := [
                        row
                        for row in decisions
                        if row.get("probability") is not None and min(int(row["probability"] * 5), 4) == i
                    ]
                )
            ],
        }
    return result


def _tokens(rows, field):
    values = [row.get("usage", {}).get(field) for row in rows]
    total = sum(values) if all(value is not None for value in values) else None
    return total if total is not None and total <= 9007199254740991 else None


def _finite_sum(values):
    total = sum(values)
    return total if math.isfinite(total) else None
