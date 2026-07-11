"""
Picks data schema and validation for picks.json format.

Validates the persistent pick records that are loaded from the Google Apps
Script sheet (or local data/picks.json mirror) before they enter the pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import numpy as np
import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Column, Check, DataFrameSchema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PCT_RE = re.compile(r"^\d+(\.\d+)?%?$")


def _is_valid_odds_or_empty(s: pd.Series) -> pd.Series:
    """Accept blank/NaN or a numeric value that looks like decimal odds."""
    result = []
    for v in s:
        if v is None or (isinstance(v, float) and np.isnan(v)) or v == "":
            result.append(True)
        else:
            try:
                f = float(str(v).strip())
                result.append(1.0 <= f <= 50.0)
            except (ValueError, TypeError):
                result.append(False)
    return pd.Series(result, index=s.index)


def _is_valid_date_or_empty(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").notna() | s.isna() | (s == "")


# ---------------------------------------------------------------------------
# Schema definition
# ---------------------------------------------------------------------------

PicksSchema = DataFrameSchema(
    columns={
        "id": Column(
            str,
            nullable=False,
            description="Event identifier (may be composite: base_id[_suffix]).",
        ),
        "casa": Column(
            str,
            checks=[
                Check(
                    lambda s: s.str.strip().str.len() > 0,
                    error="casa (home team) must be non-empty",
                )
            ],
            nullable=False,
            description="Home team name (Portuguese field name).",
        ),
        "fora": Column(
            str,
            checks=[
                Check(
                    lambda s: s.str.strip().str.len() > 0,
                    error="fora (away team) must be non-empty",
                )
            ],
            nullable=False,
            description="Away team name (Portuguese field name).",
        ),
        "liga": Column(
            str,
            nullable=True,
            description="League / competition name.",
        ),
        "data": Column(
            str,
            checks=[
                Check(
                    _is_valid_date_or_empty,
                    error="data must be a valid ISO-8601 datetime string",
                    element_wise=False,
                )
            ],
            nullable=True,
            description="Match kick-off datetime in ISO-8601 format.",
        ),
        "score_sistema": Column(
            str,
            nullable=True,
            description="System composite score as string (0-100).",
        ),
        "prob_over25": Column(
            str,
            nullable=True,
            description="Model probability for Over 2.5 as string percent (0-100 or '0-100%').",
        ),
        "odds_over": Column(
            str,
            nullable=True,
            description="Best available Over 2.5 opening odds (string float).",
        ),
        "odds_under": Column(
            str,
            nullable=True,
            description="Best available Under 2.5 opening odds (string float, may be empty).",
        ),
        "movimento": Column(
            str,
            checks=[
                Check.isin(
                    # UNKNOWN = BSD não devolveu movement (fail-honest, jul 2026)
                    ["SHORTENING", "DRIFTING", "STABLE", "STEAM", "UNKNOWN", ""],
                    error="movimento must be one of SHORTENING, DRIFTING, STABLE, STEAM, UNKNOWN or empty",
                )
            ],
            nullable=True,
            description="Odds movement direction at pick time.",
        ),
        "score_previsto": Column(
            str,
            nullable=True,
            description="Model's most likely scoreline prediction, e.g. '1-2'.",
        ),
        "xg_total": Column(
            str,
            nullable=True,
            description="Sum of xG for home + away (string float).",
        ),
        "btts_prob": Column(
            str,
            nullable=True,
            description="Both Teams To Score probability as string integer percent.",
        ),
        "fonte": Column(
            str,
            nullable=True,
            description="Origin of the pick record (e.g. 'auto-log', 'manual').",
        ),
        "sharp_label": Column(
            str,
            checks=[
                Check.isin(
                    ["STEAM", "SHARP", "WATCH", ""],
                    error="sharp_label must be STEAM, SHARP, WATCH or empty",
                )
            ],
            nullable=True,
            description="Sharp money label from the scanner.",
        ),
        "div": Column(
            str,
            nullable=True,
            description="Pinnacle vs recreational divergence % (string float).",
        ),
        "phase": Column(
            str,
            nullable=True,
            description="Pipeline phase tag (e.g. 'dev', 'prod').",
        ),
        "result_over25": Column(
            str,
            checks=[
                Check.isin(
                    ["WIN", "LOSS", "PUSH", "VOID", ""],
                    error="result_over25 must be WIN, LOSS, PUSH, VOID or empty",
                )
            ],
            nullable=True,
            description="Settlement result of the Over 2.5 bet.",
        ),
        "golos_total": Column(
            str,
            nullable=True,
            description="Total goals scored in the match (string int, may be empty pre-match).",
        ),
        "has_sharp": Column(
            str,
            nullable=True,
            description="Flag '1' if any sharp co-signal was present, else '0' or empty.",
        ),
        "sharp_co_label": Column(
            str,
            nullable=True,
            description="Label of the co-occurring sharp 1X2 signal, if any.",
        ),
        "score_band": Column(
            str,
            nullable=True,
            description="Score bucket string, e.g. '55-65'.",
        ),
        "odds_band": Column(
            str,
            nullable=True,
            description="Odds bucket string, e.g. '1.80-2.10'.",
        ),
        "saved_at": Column(
            str,
            nullable=True,
            description="ISO-8601 timestamp when the pick was persisted to the sheet.",
        ),
        "odds_over_close": Column(
            str,
            nullable=True,
            description="Pinnacle closing Over 2.5 odds (string float).",
        ),
        "clv": Column(
            str,
            nullable=True,
            description="Closing Line Value % (string float, may be negative).",
        ),
        "resultado": Column(
            str,
            nullable=True,
            description="Final scoreline, e.g. '2-1' (populated post-match).",
        ),
    },
    coerce=True,
    strict=False,  # GAS may add extra columns; ignore them
    name="PicksSchema",
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean_picks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Fix common data quality issues in a raw picks DataFrame before validation.

    Operations performed:
    - Strip leading/trailing whitespace from all string columns.
    - Convert empty strings ('') to ``np.nan`` in every column so that
      nullable checks behave correctly.
    - Parse the ``data`` and ``saved_at`` columns to ``pd.Timestamp`` if
      they are valid ISO-8601 strings, leaving un-parseable values as NaT.

    Parameters
    ----------
    df:
        Raw DataFrame loaded directly from picks.json / GAS response.

    Returns
    -------
    pd.DataFrame with the same columns, cleaned in-place copy.
    """
    df = df.copy()

    # Strip whitespace on string-typed columns
    str_cols = df.select_dtypes(include="object").columns
    for col in str_cols:
        df[col] = df[col].str.strip()

    # '' → NaN
    df.replace("", np.nan, inplace=True)

    return df


def validate_picks(picks: list[dict]) -> pd.DataFrame:
    """
    Load a list of raw pick dicts, validate against ``PicksSchema``, coerce
    types, and return a clean DataFrame with proper dtypes.

    Numeric coercions applied after validation:

    ===============  ============
    Column           Target dtype
    ===============  ============
    score_sistema    int (0-100)
    prob_over25      float (0-100 % → stored as float, NOT divided by 100)
    odds_over        float
    odds_under       float
    odds_over_close  float
    xg_total         float
    btts_prob        float
    clv              float
    golos_total      float (Int to allow NaN)
    has_sharp        Int8  (pandas nullable int; 0 or 1)
    ===============  ============

    Parameters
    ----------
    picks:
        List of raw pick dicts (e.g. from ``json.load``).

    Returns
    -------
    pd.DataFrame
        Validated, type-coerced DataFrame.  Rows that fail schema
        validation are dropped with a warning.
    """
    if not picks:
        logger.warning("validate_picks: received empty picks list")
        return pd.DataFrame()

    df = pd.DataFrame(picks)

    # Ensure all schema columns exist (fill missing with NaN)
    for col in PicksSchema.columns:
        if col not in df.columns:
            df[col] = np.nan

    # Clean before validation
    df = clean_picks(df)

    # ---- Schema validation (lazy mode → collect all errors) ----------------
    try:
        df = PicksSchema.validate(df, lazy=True)
        logger.info("validate_picks: all %d records passed schema validation", len(df))
    except pa.errors.SchemaErrors as exc:
        failure_cases = exc.failure_cases
        if "index" in failure_cases.columns:
            bad_idx = set(failure_cases["index"].dropna().astype(int).tolist())
        else:
            bad_idx = set()

        if bad_idx:
            n_bad = len(bad_idx)
            logger.warning(
                "validate_picks: dropping %d records that failed schema checks "
                "(indices: %s)",
                n_bad,
                sorted(bad_idx)[:20],
            )
            df = df[~df.index.isin(bad_idx)].copy()
        else:
            logger.warning(
                "validate_picks: schema errors detected but could not isolate "
                "bad rows — proceeding with full DataFrame. Error: %s",
                str(exc)[:500],
            )

    if df.empty:
        logger.error("validate_picks: no valid records remain after validation")
        return df

    # ---- Numeric coercions -------------------------------------------------
    def _to_float(series: pd.Series, strip_pct: bool = False) -> pd.Series:
        s = series.copy().astype(str)
        if strip_pct:
            s = s.str.rstrip("%")
        return pd.to_numeric(s, errors="coerce")

    def _to_int(series: pd.Series) -> pd.Series:
        return pd.to_numeric(series.astype(str), errors="coerce").round(0).astype("Int64")

    df["score_sistema"] = _to_int(df["score_sistema"])
    df["prob_over25"] = _to_float(df["prob_over25"], strip_pct=True)
    df["odds_over"] = _to_float(df["odds_over"])
    df["odds_under"] = _to_float(df["odds_under"])
    df["odds_over_close"] = _to_float(df["odds_over_close"])
    df["xg_total"] = _to_float(df["xg_total"])
    df["btts_prob"] = _to_float(df["btts_prob"], strip_pct=True)
    df["clv"] = _to_float(df["clv"])
    df["golos_total"] = _to_float(df["golos_total"])
    df["has_sharp"] = _to_int(df["has_sharp"]).astype("Int8")
    df["div"] = _to_float(df["div"])

    # Parse datetime columns
    df["data"] = pd.to_datetime(df["data"], errors="coerce", utc=True)
    df["saved_at"] = pd.to_datetime(df["saved_at"], errors="coerce", utc=True)

    logger.info(
        "validate_picks: returning %d records with coerced dtypes", len(df)
    )
    return df
