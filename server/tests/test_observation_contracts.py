"""Tests for observation Pydantic contracts (TDD)."""

from datetime import date

import pytest
from pydantic import ValidationError

from soundings.contracts.observation import ObservationSubmit


def _base_payload(**overrides):
    """Return a minimally-valid ObservationSubmit payload."""
    payload = {
        "organisation_id": "CC:123456",
        "place_id": "E07000123",
        "period_start": date(2026, 1, 1),
        "theme": "housing",
        "statement": "Affordability pressures have increased markedly in the last year.",
        "evidence_type": "quantitative",
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


def test_quantitative_observation_valid():
    obs = ObservationSubmit(**_base_payload(value=47, unit="percent"))
    assert obs.value == 47
    assert obs.unit == "percent"
    assert obs.evidence_type == "quantitative"
    assert obs.confidence == "high"


def test_qualitative_observation_valid():
    obs = ObservationSubmit(
        **_base_payload(
            value=None,
            evidence_type="qualitative",
            methodology_note="Semi-structured interviews with 12 residents.",
        )
    )
    assert obs.value is None
    assert obs.methodology_note is not None
    assert obs.evidence_type == "qualitative"


def test_statement_too_short_rejected():
    with pytest.raises(ValidationError):
        ObservationSubmit(**_base_payload(statement="Too short"))


def test_invalid_evidence_type_rejected():
    with pytest.raises(ValidationError):
        ObservationSubmit(**_base_payload(evidence_type="mixed"))


def test_invalid_confidence_rejected():
    with pytest.raises(ValidationError):
        ObservationSubmit(**_base_payload(confidence="extreme"))
