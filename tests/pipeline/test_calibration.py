"""
tests/pipeline/test_calibration.py
-----------------------------------
Unit tests for:
  - data/calibrator.json deserialization
  - pipeline.transform.compute_final_probability_dc
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from pipeline.transform import compute_final_probability_dc

# ---------------------------------------------------------------------------
# Helpers — reconstruct calibrator from JSON (same logic as run_calibration.py)
# ---------------------------------------------------------------------------

_CALIBRATOR_PATH = Path(__file__).resolve().parents[2] / "data" / "calibrator.json"


def _load_calibrator():
    if not _CALIBRATOR_PATH.exists():
        pytest.skip("data/calibrator.json not found (run backtesting.run_calibration first)")
    cal_data = json.loads(_CALIBRATOR_PATH.read_text())
    method = cal_data["method"]
    if method == "isotonic":
        x = np.array(cal_data["x_thresholds"], dtype=np.float64)
        y = np.array(cal_data["y_thresholds"], dtype=np.float64)

        def _predict(p_arr):
            return np.clip(np.interp(np.asarray(p_arr, dtype=np.float64), x, y), 1e-6, 1.0 - 1e-6)

        return _predict, cal_data
    elif method == "platt":
        from scipy.special import expit

        A, B = float(cal_data["A"]), float(cal_data["B"])

        def _predict(p_arr):
            p = np.clip(np.asarray(p_arr, dtype=np.float64), 1e-6, 1.0 - 1e-6)
            return np.clip(expit(A * np.log(p / (1.0 - p)) + B), 1e-6, 1.0 - 1e-6)

        return _predict, cal_data
    else:
        pytest.skip(f"Unknown calibrator method: {method}")


# ---------------------------------------------------------------------------
# Minimal dc_ratings fixture (synthetic — units only)
# ---------------------------------------------------------------------------

_MINIMAL_DC = {
    "Test League": {
        "teams": {
            "Home FC": {"attack": 0.10, "defence": -0.10},
            "Away FC": {"attack": -0.05, "defence": 0.05},
        },
        "home_adv": 0.25,
        "rho": -0.08,
        "n_games": 100,
        "converged": True,
        "fitted_at": "2026-01-01T00:00:00",
    }
}


# ---------------------------------------------------------------------------
# calibrator.json deserialization
# ---------------------------------------------------------------------------


class TestCalibratorJson:
    def test_calibrator_file_exists(self) -> None:
        assert _CALIBRATOR_PATH.exists(), "data/calibrator.json is missing"

    def test_calibrator_has_required_keys(self) -> None:
        if not _CALIBRATOR_PATH.exists():
            pytest.skip("data/calibrator.json not found")
        cal = json.loads(_CALIBRATOR_PATH.read_text())
        for key in ("method", "cv_brier_loeo", "n_train", "train_epochs"):
            assert key in cal, f"Missing key: {key}"

    def test_calibrator_output_in_unit_interval(self) -> None:
        calibrator_fn, _ = _load_calibrator()
        p_in = np.array([0.20, 0.35, 0.45, 0.55, 0.65, 0.80])
        p_out = calibrator_fn(p_in)
        assert p_out.shape == p_in.shape
        assert (p_out > 0.0).all()
        assert (p_out < 1.0).all()

    def test_calibrator_monotone(self) -> None:
        """Isotonic calibrator must be non-decreasing."""
        calibrator_fn, cal_data = _load_calibrator()
        if cal_data["method"] != "isotonic":
            pytest.skip("monotone test only applies to isotonic calibrator")
        p_in = np.linspace(0.01, 0.99, 50)
        p_out = calibrator_fn(p_in)
        diffs = np.diff(p_out)
        assert (diffs >= -1e-10).all(), "Isotonic calibrator is not monotone"


# ---------------------------------------------------------------------------
# compute_final_probability_dc
# ---------------------------------------------------------------------------


class TestComputeFinalProbabilityDC:
    def setup_method(self):
        self.calibrator_fn, _ = _load_calibrator()

    def test_output_keys_present(self) -> None:
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        for key in ("p_model_source", "p_dc_raw", "p_model", "p_market",
                    "p_market_source", "p_final", "ev_final", "odds_band"):
            assert key in result, f"Missing key: {key}"

    def test_known_teams_use_dc(self) -> None:
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        assert result["p_model_source"] == "dc"
        assert result["p_dc_raw"] is not None
        assert 0.0 < result["p_dc_raw"] < 1.0

    def test_unknown_team_falls_back_to_market(self) -> None:
        result = compute_final_probability_dc(
            home="Ghost FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        assert result["p_model_source"] == "market_only"
        assert abs(result["p_final"] - result["p_market"]) < 1e-6

    def test_unknown_league_falls_back_to_market(self) -> None:
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Unknown League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        assert result["p_model_source"] == "market_only"

    def test_p_final_between_p_model_and_p_market(self) -> None:
        """p_final must lie between p_model and p_market (blend invariant)."""
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        lo = min(result["p_model"], result["p_market"])
        hi = max(result["p_model"], result["p_market"])
        assert lo - 1e-9 <= result["p_final"] <= hi + 1e-9

    def test_ev_final_formula(self) -> None:
        """ev_final = p_final * odds_over - 1"""
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
        )
        expected = result["p_final"] * 1.90 - 1.0
        assert abs(result["ev_final"] - expected) < 1e-5

    def test_fallback_when_no_odds_under(self) -> None:
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=None,
        )
        assert result["p_market_source"] == "fallback"
        expected_market = (1.0 / 1.90) / 1.05
        assert abs(result["p_market"] - expected_market) < 1e-5

    @pytest.mark.parametrize("odds,expected_band", [
        (1.40, "<1.50"),
        (1.60, "1.50–1.70"),
        (1.85, "1.70–2.00"),
        (2.20, "2.00–2.50"),
        (2.60, ">2.50"),
    ])
    def test_odds_band_classification(self, odds: float, expected_band: str) -> None:
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=odds,
            odds_under=None,
        )
        assert result["odds_band"] == expected_band

    def test_all_market_weight_equals_market(self) -> None:
        """model_weight=0 → p_final == p_market."""
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
            model_weight=0.0,
        )
        assert abs(result["p_final"] - result["p_market"]) < 1e-6

    def test_all_model_weight_equals_model(self) -> None:
        """model_weight=1 → p_final == p_model."""
        result = compute_final_probability_dc(
            home="Home FC",
            away="Away FC",
            league="Test League",
            dc_ratings=_MINIMAL_DC,
            calibrator_fn=self.calibrator_fn,
            odds_over=1.90,
            odds_under=1.90,
            model_weight=1.0,
        )
        assert abs(result["p_final"] - result["p_model"]) < 1e-6
