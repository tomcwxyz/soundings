"""Validate and summarise the Phase 6.5 question set."""

from collections import Counter
from pathlib import Path

import yaml

from soundings.ask.dispatcher import TERMINAL_TOOL, ToolDispatcher
from soundings.evaluation.question_set import load_question_set, validate_question_set

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE_PATH = ROOT / "catalogue" / "indicators.yaml"


def catalogue_indicator_keys() -> set[str]:
    payload = yaml.safe_load(CATALOGUE_PATH.read_text())
    return {item["key"] for item in payload["indicators"]}


def ask_tool_names() -> set[str]:
    specs = ToolDispatcher(state=None).tool_specs()
    return {str(spec["name"]) for spec in specs if spec.get("name") != TERMINAL_TOOL}


def main() -> None:
    question_set = load_question_set()
    indicators = catalogue_indicator_keys()
    tools = ask_tool_names()
    errors = validate_question_set(
        question_set,
        indicator_keys=indicators,
        tool_names=tools,
    )

    if errors:
        print("Question-set validation failed:")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)

    statuses = Counter(case.expected for case in question_set.questions)
    used_tools = {tool for case in question_set.questions for tool in case.required_tools}
    print(f"Questions: {len(question_set.questions)}")
    print(
        "Expected: "
        + ", ".join(f"{name}={statuses[name]}" for name in ("supported", "partial", "gap"))
    )
    print(f"Ask tools covered: {len(used_tools)}/{len(tools)}")
    print("Known gaps:")
    for case in question_set.questions:
        if case.expected == "gap":
            print(f"- {case.id}: {case.question}")


if __name__ == "__main__":
    main()
