"""Tests for the Phase 6.5 question-led evaluation set."""

from pathlib import Path

import yaml

from soundings.ask.dispatcher import TERMINAL_TOOL, ToolDispatcher
from soundings.evaluation.question_set import load_question_set, validate_question_set

ROOT = Path(__file__).resolve().parents[2]


def _indicator_keys() -> set[str]:
    payload = yaml.safe_load((ROOT / "catalogue" / "indicators.yaml").read_text())
    return {item["key"] for item in payload["indicators"]}


def _tool_names() -> set[str]:
    return {
        str(spec["name"])
        for spec in ToolDispatcher(state=None).tool_specs()
        if spec.get("name") != TERMINAL_TOOL
    }


def test_question_set_has_curated_baseline() -> None:
    question_set = load_question_set()
    assert len(question_set.questions) == 31
    assert {case.expected for case in question_set.questions} == {
        "supported",
        "partial",
        "gap",
    }


def test_question_set_references_active_tools_and_indicators() -> None:
    question_set = load_question_set()
    errors = validate_question_set(
        question_set,
        indicator_keys=_indicator_keys(),
        tool_names=_tool_names(),
    )
    assert errors == []


def test_question_set_exercises_every_non_terminal_ask_tool() -> None:
    question_set = load_question_set()
    used_tools = {tool for case in question_set.questions for tool in case.required_tools}
    assert _tool_names() <= used_tools


def test_known_gap_candidate_indicators_are_not_advertised() -> None:
    question_set = load_question_set()
    active = _indicator_keys()
    gap_candidates = {
        key
        for case in question_set.questions
        if case.expected == "gap"
        for key in case.candidate_indicator_keys
    }
    assert gap_candidates
    assert gap_candidates.isdisjoint(active)
