"""
tests/data_quality/test_picks_quality.py
-----------------------------------------
Data quality tests for data/picks.json.

Run with:
    pytest tests/data_quality/test_picks_quality.py -v --tb=short
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest
from pandera.pandas import Column, DataFrameSchema, Check

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

PICKS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "picks.json"


def _load_raw() -> List[Dict[str, Any]]:
    """Load raw picks list from JSON."""
    with open(PICKS_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _load_df() -> pd.DataFrame:
    """Load picks as a typed DataFrame (same logic as generate_dashboard.load_picks_df)."""
    raw = _load_raw()
    df = pd.DataFrame(raw)
    df["data"]           = pd.to_datetime(df["data"], utc=True, errors="coerce")
    df["score_sistema"]  = pd.to_numeric(df["score_sistema"],  errors="coerce")
    df["prob_over25"]    = pd.to_numeric(df["prob_over25"],    errors="coerce")
    df["odds_over"]      = pd.to_numeric(df["odds_over"],      errors="coerce")
    df["odds_over_close"]= pd.to_numeric(df["odds_over_close"], errors="coerce")
    df["clv"]            = pd.to_numeric(df["clv"],            errors="coerce")
    df["xg_total"]       = pd.to_numeric(df["xg_total"],       errors="coerce")
    df["btts_prob"]      = pd.to_numeric(df["btts_prob"],      errors="coerce")
    df["result_over25"]  = df["result_over25"].str.strip().str.upper()
    df["movimento"]      = df["movimento"].str.strip().str.upper()
    df["sharp_label"]    = df["sharp_label"].str.strip().str.upper()
    df["win"]            = (df["result_over25"] == "WIN").astype(float)
    df["ev"]             = (df["prob_over25"] / 100.0) * df["odds_over"] - 1.0
    return df


@pytest.fixture(scope="module")
def all_df() -> pd.DataFrame:
    """All picks (including unresolved)."""
    return _load_df()


@pytest.fixture(scope="module")
def resolved_df(all_df: pd.DataFrame) -> pd.DataFrame:
    """Picks with a definitive result."""
    return all_df[all_df["result_over25"].isin(["WIN", "LOSS"])].copy()


# ---------------------------------------------------------------------------
# Pandera schema
# ---------------------------------------------------------------------------

# Pandas 2.x uses us (microsecond) precision; 1.x uses ns (nanosecond).
_DT_UTC_DTYPE = pd.to_datetime(["2023-01-01"], utc=True).dtype

PicksSchema = DataFrameSchema(
    columns={
        "id":            Column(str,   nullable=False),
        "casa":          Column(str,   nullable=False),
        "fora":          Column(str,   nullable=False),
        "data":          Column(_DT_UTC_DTYPE, nullable=False),
        "score_sistema": Column(float, Check.in_range(0, 100), nullable=False),
        "prob_over25":   Column(float, Check.in_range(0, 100), nullable=False),
        "movimento":     Column(str,   Check.isin(["SHORTENING", "DRIFTING", "STABLE", "STEAM", "UNKNOWN", ""]), nullable=False),
        "sharp_label":   Column(str,   Check.isin(["STEAM", "SHARP", "WATCH", ""]), nullable=False),
    },
    coerce=True,   # coerce int→float, ns→us, etc.
    strict=False,  # allow extra columns not listed here
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPicksSchema:
    def test_picks_schema_valid(self, all_df: pd.DataFrame) -> None:
        """All picks should pass the Pandera schema."""
        try:
            PicksSchema.validate(all_df, lazy=True)
        except pa.errors.SchemaErrors as exc:
            failures = exc.failure_cases
            pytest.fail(
                f"Schema validation failed for {len(failures)} cases:\n{failures.to_string()}"
            )


class TestTemporalConsistency:
    def test_no_future_results(self, all_df: pd.DataFrame) -> None:
        """
        Picks without a result (result_over25 is empty/NaN) should have a
        match date more than 3 hours in the future relative to today.
        Picks in the past that still lack results are flagged as a warning.
        """
        now = datetime.now(tz=timezone.utc)
        unresolved = all_df[~all_df["result_over25"].isin(["WIN", "LOSS"])].copy()

        if unresolved.empty:
            pytest.skip("No unresolved picks found — skipping future-date check.")

        past_unresolved = unresolved[
            unresolved["data"] < (now - timedelta(hours=3))
        ]

        if not past_unresolved.empty:
            import warnings
            games = past_unresolved[["casa", "fora", "data", "result_over25"]].to_string()
            warnings.warn(
                f"{len(past_unresolved)} picks are past the 3h window but still lack a result:\n{games}",
                UserWarning,
                stacklevel=2,
            )


class TestOddsSanity:
    def test_odds_sanity(self, resolved_df: pd.DataFrame) -> None:
        """odds_over for resolved picks must be in [1.1, 20] with no nulls."""
        assert resolved_df["odds_over"].isna().sum() == 0, (
            "Some resolved picks have null odds_over"
        )
        bad = resolved_df[
            (resolved_df["odds_over"] < 1.1) | (resolved_df["odds_over"] > 20.0)
        ]
        assert bad.empty, (
            f"{len(bad)} picks have odds outside [1.1, 20]:\n"
            f"{bad[['casa', 'fora', 'odds_over']].to_string()}"
        )


class TestProbRange:
    def test_prob_range(self, all_df: pd.DataFrame) -> None:
        """prob_over25 must be in [0, 100] for all picks."""
        assert all_df["prob_over25"].isna().sum() == 0, "Null prob_over25 values found"
        bad = all_df[(all_df["prob_over25"] < 0) | (all_df["prob_over25"] > 100)]
        assert bad.empty, (
            f"{len(bad)} picks have prob_over25 outside [0, 100]:\n"
            f"{bad[['casa', 'fora', 'prob_over25']].to_string()}"
        )


class TestCLVDistribution:
    def test_clv_distribution(self, resolved_df: pd.DataFrame) -> None:
        """
        CLV should not be uniformly positive — that would indicate data fabrication
        or systematic bias.  At least 20% of picks should have CLV <= 0.
        """
        valid_clv = resolved_df["clv"].dropna()
        assert len(valid_clv) >= 5, "Too few CLV values to run distribution check"

        pct_negative = (valid_clv <= 0).mean()
        assert pct_negative >= 0.20, (
            f"Only {pct_negative:.1%} of CLV values are <= 0.  "
            "Suspected data error: CLV should not be almost always positive."
        )


class TestCleanlabLabelQuality:
    def test_cleanlab_label_quality(self, resolved_df: pd.DataFrame) -> None:
        """
        Use cleanlab to identify potentially mislabelled picks.
        Skipped automatically if cleanlab is not installed.
        At most 20% of picks may be flagged as label issues.
        """
        cleanlab = pytest.importorskip(
            "cleanlab",
            reason="cleanlab not installed — skipping label quality test",
        )
        from cleanlab.filter import find_label_issues
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import cross_val_predict

        df = resolved_df.dropna(
            subset=["prob_over25", "xg_total", "btts_prob", "score_sistema"]
        ).copy()

        assert len(df) >= 20, "Not enough resolved picks for cleanlab check (need >= 20)"

        X = df[["prob_over25", "xg_total", "btts_prob", "score_sistema"]].values
        X[:, 0] /= 100.0   # prob_over25 → [0,1]
        X[:, 1] /= 10.0    # xg_total normalised
        X[:, 2] /= 100.0   # btts_prob → [0,1]
        X[:, 3] /= 100.0   # score_sistema → [0,1]

        y_labels = (df["result_over25"] == "WIN").astype(int).values

        clf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1)
        pred_probs = cross_val_predict(
            clf, X, y_labels, cv=min(5, len(df) // 4), method="predict_proba"
        )

        issues = find_label_issues(
            labels=y_labels,
            pred_probs=pred_probs,
            return_indices_ranked_by="self_confidence",
        )

        issue_rate = len(issues) / len(df)
        assert issue_rate <= 0.20, (
            f"Cleanlab flagged {len(issues)}/{len(df)} picks ({issue_rate:.1%}) "
            f"as potential label issues — exceeds 20% threshold.  "
            f"Suspect pick indices: {issues[:10]}"
        )


class TestEVPositiveWinsMore:
    def test_ev_positive_wins_more(self, resolved_df: pd.DataFrame) -> None:
        """
        Picks with EV > 0 should win more often than EV <= 0 picks.
        Emits a pytest warning if this condition is violated (soft assertion).
        """
        ev_pos  = resolved_df[resolved_df["ev"] >  0]["win"]
        ev_neg  = resolved_df[resolved_df["ev"] <= 0]["win"]

        if ev_pos.empty or ev_neg.empty:
            pytest.skip("Not enough picks in both EV buckets to compare.")

        wr_pos = ev_pos.mean()
        wr_neg = ev_neg.mean()

        if wr_pos < wr_neg:
            pytest.warns(
                UserWarning,
                match="EV",
            )
            import warnings
            warnings.warn(
                f"EV+ picks win rate ({wr_pos:.1%}) < EV- picks win rate ({wr_neg:.1%}).  "
                "Model may have poor probability calibration.",
                UserWarning,
                stacklevel=1,
            )
        else:
            assert wr_pos >= wr_neg, (
                f"EV+ picks ({wr_pos:.1%}) should win >= EV- picks ({wr_neg:.1%})"
            )


class TestShorteningPremium:
    def test_shortening_premium(self, resolved_df: pd.DataFrame) -> None:
        """
        SHORTENING picks should have win rate >= DRIFTING picks.
        Emits a warning if violated — this is a soft/research assertion.
        """
        short  = resolved_df[resolved_df["movimento"] == "SHORTENING"]["win"]
        drift  = resolved_df[resolved_df["movimento"] == "DRIFTING"]["win"]

        if short.empty or drift.empty:
            pytest.skip("Not enough picks in SHORTENING/DRIFTING buckets.")

        wr_short = short.mean()
        wr_drift = drift.mean()

        if wr_short < wr_drift:
            import warnings
            warnings.warn(
                f"SHORTENING win rate ({wr_short:.1%}) < DRIFTING win rate ({wr_drift:.1%}).  "
                "Sharp money signal may not be adding value in this sample.",
                UserWarning,
                stacklevel=1,
            )
        # Not a hard failure — we warn but don't fail the CI
