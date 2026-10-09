"""One acceptance contract shared by execution and outcome learning."""

from collections import Counter
from typing import Any


def model_identity(actual_model: object) -> str | None:
    """Conservative identity across provider/org prefixes; unknown is never proof."""
    if not isinstance(actual_model, str) or not actual_model.strip():
        return None
    return actual_model.strip().casefold().rsplit("/", 1)[-1].removesuffix(":free") or None


def review_reason(review: dict[str, Any], expected: list[str]) -> str | None:
    def normalize(text: str) -> str:
        return " ".join(text.split()).casefold()

    criteria = review.get("criteria")
    wanted = [normalize(c) for c in expected]
    if not wanted or not all(wanted) or len(set(wanted)) != len(wanted):
        return "Task acceptance criteria are missing or duplicated."
    if not isinstance(criteria, list) or not all(
        isinstance(c, dict) and isinstance(c.get("criterion"), str) for c in criteria
    ):
        return "Review criterion assessments are missing or invalid."
    actual = [normalize(c["criterion"]) for c in criteria]
    if Counter(actual) != Counter(wanted):
        return (
            "Review must assess every task criterion exactly once; missing or duplicate coverage."
        )
    return None


def review_passed(review: dict[str, Any], expected: list[str]) -> bool:
    findings = review.get("findings")
    return (
        review_reason(review, expected) is None
        and review.get("verdict") == "accept"
        and isinstance(findings, list)
        and all(
            isinstance(f, dict) and f.get("severity") not in ("blocker", "major") for f in findings
        )
        and all(c.get("met") is True for c in review["criteria"])
    )
