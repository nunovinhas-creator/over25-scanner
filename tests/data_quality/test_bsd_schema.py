"""
tests/data_quality/test_bsd_schema.py
---------------------------------------
Structural / schema tests for BSD Sports API event payloads.

These tests exercise the BSDEventSchema validator and the
validate_bsd_events() batch helper used by pipeline/etl.py.

Run with:
    pytest tests/data_quality/test_bsd_schema.py -v --tb=short
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

import pytest
import pandera as pa
from pandera import Column, DataFrameSchema, Check
import pandas as pd

# ---------------------------------------------------------------------------
# BSD Event schema definition
# ---------------------------------------------------------------------------
# Raw BSD API events use float probabilities in the range 0..1
# (e.g. prob_over_25=0.72, NOT 72).

BSDEventSchema = DataFrameSchema(
    columns={
        "event_id":     Column(str,   nullable=False),
        "home":         Column(str,   nullable=False),
        "away":         Column(str,   nullable=False),
        "date":         Column(str,   nullable=False),
        "league":       Column(str,   nullable=True),
        "prob_over_25": Column(float, Check.in_range(0.0, 1.0), nullable=True),
        "odds_over":    Column(float, Check.in_range(1.0, 50.0), nullable=True),
        "movement":     Column(
            str,
            Check.isin(["SHORTENING", "DRIFTING", "STABLE", ""]),
            nullable=True,
        ),
    },
    coerce=True,
    strict=False,  # allow additional columns from the real API
)

# ---------------------------------------------------------------------------
# Sample BSD event fixture
# ---------------------------------------------------------------------------

VALID_BSD_EVENT: Dict[str, Any] = {
    "event_id":     "207461",
    "home":         "SC Paderborn 07",
    "away":         "VfL Wolfsburg",
    "date":         "2026-05-25T18:30:00+00:00",
    "league":       "2. Bundesliga",
    "country":      "Germany",           # extra field – schema is non-strict
    "prob_over_25": 0.797,               # normalised to [0,1]
    "prob_under_25": 0.203,
    "odds_over":    1.952,
    "odds_under":   2.10,
    "movement":     "DRIFTING",
    "xg_home":      2.1,
    "xg_away":      2.09,
    "btts_prob":    0.77,
    "pinnacle_home": 2.45,
    "pinnacle_draw": 3.20,
    "pinnacle_away": 3.10,
}

# ---------------------------------------------------------------------------
# Batch validation helper (mirrors what pipeline/etl.py would call)
# ---------------------------------------------------------------------------

class SchemaError(Exception):
    """Raised when a BSD event fails schema validation."""


def validate_bsd_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate a single BSD event dict against BSDEventSchema.
    Returns the event unchanged if valid.
    Raises SchemaError with details on failure.
    """
    required = ["event_id", "home", "away", "date"]
    missing = [f for f in required if not event.get(f)]
    if missing:
        raise SchemaError(f"Missing required fields: {missing}")

    df = pd.DataFrame([event])
    try:
        BSDEventSchema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as exc:
        raise SchemaError(str(exc.failure_cases.to_dict())) from exc

    return event


def validate_bsd_events(
    events: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Validate a list of BSD events.
    Returns (valid_events, invalid_events).
    """
    valid:   List[Dict[str, Any]] = []
    invalid: List[Dict[str, Any]] = []

    for event in events:
        try:
            validate_bsd_event(event)
            valid.append(event)
        except SchemaError:
            invalid.append(event)

    return valid, invalid


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestValidEvent:
    def test_valid_event_passes_schema(self) -> None:
        """A well-formed BSD event should pass schema validation without raising."""
        result = validate_bsd_event(VALID_BSD_EVENT)
        assert result["event_id"] == "207461"
        assert result["home"] == "SC Paderborn 07"


class TestRequiredFields:
    @pytest.mark.parametrize("missing_field", ["home", "away", "date"])
    def test_missing_required_fields_fails(self, missing_field: str) -> None:
        """Removing a required field (home / away / date) must raise SchemaError."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        del event[missing_field]
        with pytest.raises(SchemaError):
            validate_bsd_event(event)

    def test_missing_event_id_fails(self) -> None:
        """event_id is required and non-null."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        del event["event_id"]
        with pytest.raises(SchemaError):
            validate_bsd_event(event)


class TestProbRange:
    def test_prob_out_of_range_fails(self) -> None:
        """
        prob_over_25 > 1.0 fails schema.
        Raw percentage (e.g. 79.7) is NOT normalised and must not be accepted.
        """
        event = copy.deepcopy(VALID_BSD_EVENT)
        event["prob_over_25"] = 79.7  # wrong: raw percentage, not [0,1]
        with pytest.raises(SchemaError):
            validate_bsd_event(event)

    def test_prob_negative_fails(self) -> None:
        """prob_over_25 must not be negative."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        event["prob_over_25"] = -0.1
        with pytest.raises(SchemaError):
            validate_bsd_event(event)

    def test_prob_at_boundary_passes(self) -> None:
        """Edge values 0.0 and 1.0 should be accepted."""
        for boundary in [0.0, 1.0]:
            event = copy.deepcopy(VALID_BSD_EVENT)
            event["prob_over_25"] = boundary
            validate_bsd_event(event)  # must not raise


class TestBatchValidation:
    def test_validate_batch_counts(self) -> None:
        """
        validate_bsd_events() with a mix of valid and invalid events returns
        the correct count in each bucket.
        """
        invalid_missing_home = copy.deepcopy(VALID_BSD_EVENT)
        del invalid_missing_home["home"]

        invalid_bad_prob = copy.deepcopy(VALID_BSD_EVENT)
        invalid_bad_prob["event_id"] = "999999"
        invalid_bad_prob["prob_over_25"] = 120.0  # raw percentage – invalid

        valid_extra = copy.deepcopy(VALID_BSD_EVENT)
        valid_extra["event_id"] = "111111"  # different ID, still valid

        batch = [VALID_BSD_EVENT, invalid_missing_home, invalid_bad_prob, valid_extra]
        valid, invalid = validate_bsd_events(batch)

        assert len(valid)   == 2, f"Expected 2 valid, got {len(valid)}"
        assert len(invalid) == 2, f"Expected 2 invalid, got {len(invalid)}"

    def test_validate_batch_empty(self) -> None:
        """An empty batch returns two empty lists."""
        valid, invalid = validate_bsd_events([])
        assert valid   == []
        assert invalid == []

    def test_validate_batch_all_valid(self) -> None:
        """A batch of 3 identical valid events returns all in valid bucket."""
        batch = [copy.deepcopy(VALID_BSD_EVENT) for _ in range(3)]
        valid, invalid = validate_bsd_events(batch)
        assert len(valid)   == 3
        assert len(invalid) == 0


class TestMovementValues:
    @pytest.mark.parametrize("movement", ["SHORTENING", "DRIFTING", "STABLE", ""])
    def test_allowed_movement_values_pass(self, movement: str) -> None:
        """Only the four allowed movement values should pass schema validation."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        event["movement"] = movement
        validate_bsd_event(event)  # must not raise

    @pytest.mark.parametrize("movement", ["UP", "DOWN", "shortening", "steaming", "1"])
    def test_invalid_movement_values_fail(self, movement: str) -> None:
        """Invalid movement strings must be rejected by the schema."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        event["movement"] = movement
        with pytest.raises(SchemaError):
            validate_bsd_event(event)

    def test_null_movement_is_allowed(self) -> None:
        """movement=None (nullable) should pass schema validation."""
        event = copy.deepcopy(VALID_BSD_EVENT)
        event["movement"] = None
        validate_bsd_event(event)  # must not raise
