"""Tests for temporal guidance appended to Ask system prompts."""

from soundings.ask.grants_guidance import GRANTS_GUIDANCE
from soundings.ask.temporal_guidance import TEMPORAL_GUIDANCE


def test_temporal_guidance_prefers_deterministic_change_tool() -> None:
    assert "get_change" in TEMPORAL_GUIDANCE
    assert "prefer get_change" in TEMPORAL_GUIDANCE.lower()
    assert "do not calculate change in prose" in TEMPORAL_GUIDANCE.lower()


def test_temporal_guidance_is_appended_by_existing_ask_suffix() -> None:
    assert TEMPORAL_GUIDANCE in GRANTS_GUIDANCE
