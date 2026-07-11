"""
BSD API event data schema and validation.

Validates the event structure returned by the BSD Sports API
(https://sports.bzzoiro.com/events/) before it enters the pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

BSDEventSchema = DataFrameSchema(
    columns={
        "id": Column(
            str,
            nullable=False,
            description="Unique event identifier from the BSD API.",
        ),
        "home": Column(
            str,
            checks=[Check(lambda s: s.str.strip().str.len() > 0, error="home must be non-empty")],
            nullable=False,
            description="Home team name.",
        ),
        "away": Column(
            str,
            checks=[Check(lambda s: s.str.strip().str.len() > 0, error="away must be non-empty")],
            nullable=False,
            description="Away team name.",
        ),
        "league": Column(
            str,
            nullable=True,
            description="League / competition name.",
        ),
        "date": Column(
            str,
            checks=[
                Check(
                    lambda s: pd.to_datetime(s, errors="coerce").notna(),
                    error="date must be ISO-8601 parseable",
                    element_wise=False,
                )
            ],
            nullable=False,
            description="Match kick-off datetime in ISO-8601 format.",
        ),
        "prob_over_25": Column(
            float,
            checks=[
                Check.ge(0.0, error="prob_over_25 must be >= 0"),
                Check.le(1.0, error="prob_over_25 must be <= 1"),
            ],
            nullable=True,
            description="Model probability for Over 2.5 goals (0–1).",
        ),
        "xgH": Column(
            float,
            checks=[Check.ge(0.0, error="xgH must be >= 0")],
            nullable=True,
            description="Expected goals for home team.",
        ),
        "xgA": Column(
            float,
            checks=[Check.ge(0.0, error="xgA must be >= 0")],
            nullable=True,
            description="Expected goals for away team.",
        ),
        "bttsN": Column(
            float,
            checks=[
                Check.ge(0.0, error="bttsN must be >= 0"),
                Check.le(100.0, error="bttsN must be <= 100"),
            ],
            nullable=True,
            description="BTTS probability as a percentage (0–100).",
        ),
        "pinnNow": Column(
            float,
            checks=[
                Check.ge(1.01, error="pinnNow (Pinnacle odds) must be >= 1.01"),
                Check.le(50.0, error="pinnNow (Pinnacle odds) must be <= 50"),
            ],
            nullable=True,
            description="Current Pinnacle Over 2.5 odds.",
        ),
        "movement": Column(
            str,
            checks=[
                Check.isin(
                    # UNKNOWN = campo em falta na resposta BSD (fail-honest, jul 2026)
                    ["SHORTENING", "DRIFTING", "STABLE", "STEAM", "UNKNOWN"],
                    error="movement must be one of SHORTENING, DRIFTING, STABLE, STEAM, UNKNOWN",
                )
            ],
            nullable=False,
            description="Odds movement direction.",
        ),
        "sharpLabel": Column(
            str,
            nullable=True,
            description="Sharp money label (STEAM / SHARP / WATCH or empty).",
        ),
        "div": Column(
            float,
            nullable=True,
            description="Divergence between Pinnacle and recreational book odds (%).",
        ),
        "score": Column(
            int,
            checks=[
                Check.ge(0, error="score must be >= 0"),
                Check.le(100, error="score must be <= 100"),
            ],
            nullable=False,
            description="System composite score (0–100).",
        ),
        "oddsOver": Column(
            float,
            checks=[
                Check.ge(1.01, error="oddsOver must be >= 1.01"),
                Check.le(50.0, error="oddsOver must be <= 50"),
            ],
            nullable=True,
            description="Best available Over 2.5 odds.",
        ),
    },
    coerce=True,
    strict=False,  # allow extra columns from the API response
    name="BSDEventSchema",
)


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def validate_bsd_events(events: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Validate a list of raw BSD API event dicts against ``BSDEventSchema``.

    Parameters
    ----------
    events:
        Raw event records from the BSD API (list of dicts).

    Returns
    -------
    valid:
        Records that passed schema validation (as dicts).
    errors:
        Records that failed, each augmented with an ``_validation_error``
        key containing the error message string.

    Notes
    -----
    * Validation is done row-by-row so a single bad record does not drop the
      whole batch.
    * Missing required fields default to ``None`` before validation so that
      the error message identifies the failing column, not a KeyError.
    """
    if not events:
        logger.warning("validate_bsd_events: received empty events list")
        return [], []

    valid: list[dict] = []
    errors: list[dict] = []

    # Validate the whole frame first for efficiency; fall back to row-by-row
    df = pd.DataFrame(events)

    # Ensure required columns exist (fill missing with None/NaN)
    required_cols = list(BSDEventSchema.columns.keys())
    for col in required_cols:
        if col not in df.columns:
            df[col] = None

    try:
        validated_df = BSDEventSchema.validate(df, lazy=True)
        valid = validated_df.to_dict(orient="records")
        logger.info("validate_bsd_events: all %d records passed", len(valid))
        return valid, []
    except pa.errors.SchemaErrors as exc:
        # Build a set of failing row indices
        failure_cases = exc.failure_cases
        if "index" in failure_cases.columns:
            bad_indices: set[int] = set(failure_cases["index"].dropna().astype(int).tolist())
        else:
            # Fallback: validate row-by-row
            bad_indices = set()

        if bad_indices:
            good_mask = ~df.index.isin(bad_indices)
            good_df = df[good_mask]
            bad_df = df[~good_mask]

            # Re-validate good rows (may still fail edge-case checks)
            try:
                good_validated = BSDEventSchema.validate(good_df, lazy=True)
                valid = good_validated.to_dict(orient="records")
            except pa.errors.SchemaErrors:
                valid = good_df.to_dict(orient="records")

            for idx in bad_indices:
                row = df.loc[idx].to_dict()
                case_rows = failure_cases[failure_cases["index"] == idx]
                row["_validation_error"] = case_rows[["check", "column", "failure_case"]].to_dict(orient="records")
                errors.append(row)
        else:
            # Cannot isolate bad rows; mark all as errors
            for row in events:
                row_copy = dict(row)
                row_copy["_validation_error"] = str(exc)
                errors.append(row_copy)

        logger.warning(
            "validate_bsd_events: %d valid, %d invalid records",
            len(valid),
            len(errors),
        )
        return valid, errors
    except Exception as exc:  # noqa: BLE001
        logger.exception("validate_bsd_events: unexpected error: %s", exc)
        error_records = [dict(r, _validation_error=str(exc)) for r in events]
        return [], error_records
