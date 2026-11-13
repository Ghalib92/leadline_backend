"""Metrics for evals. Pure functions over (expected, got) pairs."""

from datetime import date
from decimal import Decimal, InvalidOperation

from apps.opportunities.dedupe import normalize_text


def judge_metrics(cases: list[tuple[bool, int]], threshold: int) -> dict:
    """`cases`: (a person approved it, the judge's score). The judge "approves" when the
    score reaches the feed threshold, i.e. when the opportunity would be shown.

    - agreement: share of cases where the judge and the person agree
    - precision: of what the judge would show, how much people approved
    - recall: of what people approved, how much the judge would show
    - false_positive_rate: of what people rejected, how much the judge would still show
    """
    tp = sum(1 for approved, score in cases if approved and score >= threshold)
    fp = sum(1 for approved, score in cases if not approved and score >= threshold)
    fn = sum(1 for approved, score in cases if approved and score < threshold)
    tn = sum(1 for approved, score in cases if not approved and score < threshold)
    approved = [score for ok, score in cases if ok]
    rejected = [score for ok, score in cases if not ok]
    return {
        "cases": len(cases),
        "threshold": threshold,
        "agreement": _ratio(tp + tn, len(cases)),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "false_positive_rate": _ratio(fp, fp + tn),
        "mean_score_approved": _mean(approved),
        "mean_score_rejected": _mean(rejected),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
    }


def compare_field(field: str, expected, got) -> bool:
    """Whether an extracted field matches the hand label, field by field."""
    if expected in (None, ""):
        return got in (None, "")
    if field in ("amount_min", "amount_max"):
        try:
            want, have = Decimal(str(expected)), Decimal(str(got))
        except (InvalidOperation, TypeError):
            return False
        return abs(want - have) <= max(Decimal(1), want * Decimal("0.01"))
    if field == "deadline":
        try:
            return date.fromisoformat(str(got)[:10]) == date.fromisoformat(str(expected)[:10])
        except ValueError:
            return False
    if field in ("funder", "location", "title"):
        want, have = normalize_text(str(expected)), normalize_text(str(got or ""))
        return bool(have) and (want in have or have in want)
    if isinstance(expected, str):
        return str(got).strip().lower() == expected.strip().lower()
    return got == expected


def extraction_metrics(results: list[dict[str, bool]]) -> dict:
    """`results`: per case, {field: matched}. Accuracy per field and of whole cases."""
    fields = sorted({field for result in results for field in result})
    return {
        "cases": len(results),
        "exact_cases": _ratio(sum(1 for r in results if r and all(r.values())), len(results)),
        "fields": {
            field: _ratio(
                sum(1 for r in results if r.get(field)), sum(1 for r in results if field in r)
            )
            for field in fields
        },
    }


def mean_metrics(scores: list[float], name: str = "score") -> dict:
    return {
        "cases": len(scores),
        f"mean_{name}": _mean(scores),
        f"min_{name}": min(scores) if scores else None,
    }


def _ratio(part, whole):
    return round(part / whole, 3) if whole else None


def _mean(values):
    return round(sum(values) / len(values), 1) if values else None
