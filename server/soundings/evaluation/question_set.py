"""Question-set schema and static validation for Phase 6.5."""

from collections import Counter
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

EVALUATION_DIR = Path(__file__).resolve().parents[3] / "evaluation"
DEFAULT_QUESTION_SET_PATH = EVALUATION_DIR / "questions.yaml"
DEFAULT_GRANTS_QUESTION_SET_PATH = EVALUATION_DIR / "grants_questions.yaml"
DEFAULT_TEMPORAL_QUESTION_SET_PATH = EVALUATION_DIR / "temporal_questions.yaml"
DEFAULT_EXTRA_QUESTION_SET_PATHS = (
    DEFAULT_GRANTS_QUESTION_SET_PATH,
    DEFAULT_TEMPORAL_QUESTION_SET_PATH,
)

ANSWER_BLOCK_TYPES = frozenset(
    {
        "text",
        "indicator-card",
        "trend-chart",
        "compare-chart",
        "organisations",
        "insight-callout",
        "map",
        "distribution-chart",
        "composition-chart",
        "bar-chart",
        "scatter-plot",
        "sub-area-table",
    }
)


class QuestionCase(BaseModel):
    id: str = Field(pattern=r"^Q\d{2}$")
    question: str = Field(min_length=8)
    places: list[str] = Field(default_factory=list)
    intent: str
    expected: Literal["supported", "partial", "gap"]
    tags: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    required_indicators: list[str] = Field(default_factory=list)
    candidate_indicator_keys: list[str] = Field(default_factory=list)
    expected_blocks: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(min_length=1)
    known_gap: str | None = None


class QuestionSet(BaseModel):
    version: int = 1
    questions: list[QuestionCase] = Field(min_length=1)


def load_question_set(path: Path | None = None) -> QuestionSet:
    target = path or DEFAULT_QUESTION_SET_PATH
    payload = yaml.safe_load(target.read_text())

    if path is None:
        # Capability-specific evaluation slices stay separate so they can move
        # with their boundary if Soundings is later decomposed. The default
        # evaluation composes every slice into one versioned contract.
        for extra_path in DEFAULT_EXTRA_QUESTION_SET_PATHS:
            extra_payload = yaml.safe_load(extra_path.read_text())
            if extra_payload.get("version") != payload.get("version"):
                raise ValueError(f"{extra_path.name} version does not match baseline")
            payload["questions"] = [
                *payload.get("questions", []),
                *extra_payload.get("questions", []),
            ]

    return QuestionSet.model_validate(payload)


def validate_question_set(
    question_set: QuestionSet,
    *,
    indicator_keys: set[str],
    tool_names: set[str],
) -> list[str]:
    """Return human-readable static consistency errors."""

    errors: list[str] = []
    ids = [case.id for case in question_set.questions]
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    if duplicates:
        errors.append(f"Duplicate question IDs: {', '.join(sorted(duplicates))}")

    for case in question_set.questions:
        unknown_tools = sorted(set(case.required_tools) - tool_names)
        if unknown_tools:
            errors.append(f"{case.id}: unknown tools: {', '.join(unknown_tools)}")

        unknown_blocks = sorted(set(case.expected_blocks) - ANSWER_BLOCK_TYPES)
        if unknown_blocks:
            errors.append(f"{case.id}: unknown answer blocks: " + ", ".join(unknown_blocks))

        unknown_indicators = sorted(set(case.required_indicators) - indicator_keys)
        if unknown_indicators:
            errors.append(
                f"{case.id}: required indicators not active: " + ", ".join(unknown_indicators)
            )

        active_candidates = sorted(set(case.candidate_indicator_keys) & indicator_keys)
        if active_candidates:
            errors.append(
                f"{case.id}: candidate indicators are now active; update the case: "
                + ", ".join(active_candidates)
            )

        if case.expected in {"partial", "gap"} and not case.known_gap:
            errors.append(f"{case.id}: {case.expected} case must explain its known_gap")

    return errors
