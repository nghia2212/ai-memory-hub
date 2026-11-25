"""Pre-execution deterministic complexity rubric (XS/S/M/L/XL)."""
from __future__ import annotations

from services.orchestrator.contracts import ComplexityTier, PreExecutionFeatures

_TASK_TYPE_BASE: dict[str, int] = {
    "routine": 0,
    "documentation": 1,
    "testing": 2,
    "review": 2,
    "ui": 3,
    "implementation": 3,
    "debugging": 4,
    "complex_reasoning": 5,
    "architecture": 6,
}

_UNCERTAINTY_POINTS: dict[str, int] = {
    "low": 0,
    "medium": 2,
    "high": 4,
}


def classify_complexity(features: PreExecutionFeatures) -> ComplexityTier:
    """
    Classify task complexity deterministically from pre-execution features only.
    Never uses post-execution outcomes or token counts alone.
    """
    score = _TASK_TYPE_BASE.get(features.task_type, 3)
    score += _UNCERTAINTY_POINTS.get(features.uncertainty_level, 2)

    if features.file_count >= 15:
        score += 4
    elif features.file_count >= 6:
        score += 2
    elif features.file_count >= 2:
        score += 1

    if features.expected_edits >= 8:
        score += 3
    elif features.expected_edits >= 3:
        score += 1

    if features.dependency_count >= 4:
        score += 2
    elif features.dependency_count >= 1:
        score += 1

    if features.expected_tests >= 5:
        score += 1

    if score <= 2:
        return "XS"
    if score <= 5:
        return "S"
    if score <= 8:
        return "M"
    if score <= 11:
        return "L"
    return "XL"
