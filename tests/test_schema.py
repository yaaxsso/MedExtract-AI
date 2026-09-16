"""
Tests for the Phase 4 validation/repair logic in src/api.py:
  - ExtractionSchema itself (required fields, literal constraints, defaults)
  - validate_and_repair's retry-then-give-up behavior

No Gemini calls happen here — validate_and_repair's `generate_fn` is a plain
stub, and the schema tests just construct ExtractionSchema directly.
"""
import json

import pytest
from pydantic import ValidationError

from src.api import ExtractionSchema, validate_and_repair


# ---------------------------------------------------------------------------
# ExtractionSchema
# ---------------------------------------------------------------------------
def test_schema_accepts_minimal_valid_payload():
    obj = ExtractionSchema(summary="Patient reports mild headache.", urgency="low")
    assert obj.chief_complaint is None
    assert obj.symptoms is None
    assert obj.summary == "Patient reports mild headache."
    assert obj.urgency == "low"


def test_schema_accepts_fully_populated_payload():
    obj = ExtractionSchema(
        chief_complaint="Headache",
        symptoms=["headache", "nausea"],
        diagnosis=None,
        medical_history="Migraine history",
        medications=["ibuprofen"],
        procedures=None,
        follow_up="Follow up in 1 week",
        summary="Patient with headache and nausea, possible migraine.",
        risk_indicators=None,
        urgency="moderate",
    )
    assert obj.symptoms == ["headache", "nausea"]
    assert obj.urgency == "moderate"


def test_schema_rejects_missing_summary():
    with pytest.raises(ValidationError):
        ExtractionSchema(urgency="low")


def test_schema_rejects_invalid_urgency_value():
    with pytest.raises(ValidationError):
        ExtractionSchema(summary="x", urgency="critical")  # not one of low/moderate/high


def test_schema_rejects_missing_urgency():
    with pytest.raises(ValidationError):
        ExtractionSchema(summary="x")


# ---------------------------------------------------------------------------
# validate_and_repair
# ---------------------------------------------------------------------------
def test_validate_and_repair_succeeds_on_first_try():
    raw = json.dumps({"summary": "All good.", "urgency": "low"})
    result, ok = validate_and_repair(raw, ExtractionSchema, generate_fn=lambda p: None, max_retries=2)
    assert ok is True
    assert result["summary"] == "All good."
    assert result["urgency"] == "low"


def test_validate_and_repair_repairs_after_one_bad_attempt():
    bad = json.dumps({"summary": "Oops missing urgency"})  # fails: urgency required
    good = json.dumps({"summary": "Fixed now.", "urgency": "high"})

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, text):
            self.text = text

    def fake_generate(prompt):
        calls["n"] += 1
        return FakeResponse(good)

    result, ok = validate_and_repair(bad, ExtractionSchema, generate_fn=fake_generate, max_retries=2)
    assert ok is True
    assert result["urgency"] == "high"
    assert calls["n"] == 1  # only needed one repair round


def test_validate_and_repair_gives_up_after_max_retries():
    always_bad = json.dumps({"summary": "still missing urgency"})

    class FakeResponse:
        def __init__(self, text):
            self.text = text

    def fake_generate(prompt):
        return FakeResponse(always_bad)

    result, ok = validate_and_repair(always_bad, ExtractionSchema, generate_fn=fake_generate, max_retries=2)
    assert ok is False
    assert "error" in result
    assert result["raw_output"] == always_bad


def test_validate_and_repair_handles_malformed_json():
    result, ok = validate_and_repair("not valid json at all {", ExtractionSchema, generate_fn=lambda p: type("R", (), {"text": "still not json"})(), max_retries=1)
    assert ok is False
    assert "error" in result
