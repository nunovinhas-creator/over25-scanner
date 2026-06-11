"""
tests/pipeline/test_devig.py
-----------------------------
Unit tests for models.math.devig (margin removal) and
pipeline.transform.compute_final_probability (market blend).

Run with:
    pytest tests/pipeline/test_devig.py -v --tb=short
"""

from __future__ import annotations

import math

import pytest

from models.math.devig import devig, metodo_multiplicativo, metodo_shin
from pipeline.transform import compute_final_probability


# ---------------------------------------------------------------------------
# metodo_multiplicativo
# ---------------------------------------------------------------------------


class TestMultiplicativo:
    def test_symmetric_odds_gives_50_50(self) -> None:
        """Symmetric odds 1.90/1.90 must give exactly p=0.50."""
        p_over, p_under = metodo_multiplicativo(1.90, 1.90)
        assert abs(p_over - 0.50) < 1e-10
        assert abs(p_under - 0.50) < 1e-10

    def test_probabilities_sum_to_one(self) -> None:
        """De-vigged probabilities must always sum to 1."""
        for o, u in [(1.90, 1.90), (1.95, 1.85), (1.70, 2.20), (1.50, 2.60)]:
            p_o, p_u = metodo_multiplicativo(o, u)
            assert abs(p_o + p_u - 1.0) < 1e-12, f"Sum ≠ 1 for odds {o}/{u}"

    def test_asymmetric_odds_1_95_1_85(self) -> None:
        """odds 1.95/1.85 → p_over ≈ 0.4868 (task acceptance criterion)."""
        p_over, _ = metodo_multiplicativo(1.95, 1.85)
        assert abs(p_over - 0.4868) < 5e-4

    def test_overround_never_negative(self) -> None:
        """Implied overround (sum of raw probs) must be >= 1.0 for valid market odds."""
        for o, u in [(1.70, 2.20), (2.00, 2.00), (1.40, 3.00), (1.90, 1.90)]:
            q_o = 1.0 / o
            q_u = 1.0 / u
            margem = q_o + q_u
            assert margem >= 1.0, f"Margem < 1 for {o}/{u}: got {margem}"

    def test_favourite_has_higher_probability(self) -> None:
        """Lower odds → higher implied probability."""
        p_over, p_under = metodo_multiplicativo(1.70, 2.20)
        assert p_over > p_under

    def test_invalid_odds_raises(self) -> None:
        """Odds <= 1.0 must raise ValueError."""
        with pytest.raises(ValueError):
            metodo_multiplicativo(1.0, 1.90)
        with pytest.raises(ValueError):
            metodo_multiplicativo(1.90, 0.95)


# ---------------------------------------------------------------------------
# metodo_shin
# ---------------------------------------------------------------------------


class TestShin:
    def test_symmetric_odds_gives_50_50(self) -> None:
        """Symmetric odds 1.90/1.90 must give p=0.50 by symmetry."""
        p_over, p_under = metodo_shin(1.90, 1.90)
        assert abs(p_over - 0.50) < 1e-8
        assert abs(p_under - 0.50) < 1e-8

    def test_probabilities_sum_to_one(self) -> None:
        """Shin probabilities must sum to 1 for any valid input."""
        for o, u in [(1.90, 1.90), (1.95, 1.85), (1.70, 2.20), (2.10, 1.75)]:
            p_o, p_u = metodo_shin(o, u)
            assert abs(p_o + p_u - 1.0) < 1e-8, f"Shin sum ≠ 1 for {o}/{u}"

    def test_shin_probabilities_in_unit_interval(self) -> None:
        """Shin output must be in (0, 1)."""
        for o, u in [(1.90, 1.90), (1.60, 2.50), (1.30, 3.60)]:
            p_o, p_u = metodo_shin(o, u)
            assert 0.0 < p_o < 1.0
            assert 0.0 < p_u < 1.0

    def test_shin_close_to_multiplicative_for_small_margin(self) -> None:
        """For small margins (≤5%), Shin and multiplicative should be within 1pp."""
        o, u = 1.95, 2.00  # ~3% margin
        p_mult, _ = metodo_multiplicativo(o, u)
        p_shin, _ = metodo_shin(o, u)
        assert abs(p_mult - p_shin) < 0.015

    def test_invalid_odds_raises(self) -> None:
        with pytest.raises(ValueError):
            metodo_shin(0.90, 1.90)


# ---------------------------------------------------------------------------
# devig convenience wrapper
# ---------------------------------------------------------------------------


class TestDevig:
    def test_default_is_multiplicative(self) -> None:
        p1, _ = devig(1.90, 1.90)
        p2, _ = metodo_multiplicativo(1.90, 1.90)
        assert p1 == p2

    def test_shin_method(self) -> None:
        p1, _ = devig(1.90, 1.90, method="shin")
        p2, _ = metodo_shin(1.90, 1.90)
        assert abs(p1 - p2) < 1e-12


# ---------------------------------------------------------------------------
# compute_final_probability (blend model + market)
# ---------------------------------------------------------------------------


class TestComputeFinalProbability:
    def test_blend_reduces_overconfident_model(self) -> None:
        """
        Task acceptance criterion: prob_over25=80, odds 1.86/1.94
        must produce p_final ≈ 0.597 (not 0.80).

        p_market = 1/1.86 / (1/1.86 + 1/1.94) ≈ 0.5105
        p_final  = 0.30*0.80 + 0.70*0.5105     ≈ 0.597
        """
        result = compute_final_probability(
            prob_over25=80.0,
            odds_over=1.86,
            odds_under=1.94,
            model_weight=0.30,
        )
        assert result["p_final"] < 0.80, "p_final must be lower than raw model prob"
        assert abs(result["p_final"] - 0.597) < 0.015, (
            f"Expected p_final ≈ 0.597, got {result['p_final']}"
        )

    def test_p_model_field_correct(self) -> None:
        result = compute_final_probability(80.0, 1.86, 1.94)
        assert abs(result["p_model"] - 0.80) < 1e-6

    def test_p_market_source_devig_when_odds_under_present(self) -> None:
        result = compute_final_probability(65.0, 1.90, 1.90)
        assert result["p_market_source"] == "devig"
        assert abs(result["p_market"] - 0.50) < 1e-6

    def test_p_market_source_fallback_when_no_odds_under(self) -> None:
        result = compute_final_probability(65.0, 1.90, odds_under=None)
        assert result["p_market_source"] == "fallback"
        # fallback: (1/1.90) / 1.05
        expected = (1.0 / 1.90) / 1.05
        assert abs(result["p_market"] - expected) < 1e-5

    def test_ev_final_correct(self) -> None:
        """ev_final = p_final * odds_over - 1"""
        result = compute_final_probability(80.0, 1.86, 1.94, model_weight=0.30)
        expected_ev = result["p_final"] * 1.86 - 1.0
        assert abs(result["ev_final"] - expected_ev) < 1e-5

    def test_symmetric_market_50pct_low_model(self) -> None:
        """Model 50%, symmetric 1.90/1.90 market → p_final = 0.50 exactly."""
        result = compute_final_probability(50.0, 1.90, 1.90, model_weight=0.30)
        assert abs(result["p_final"] - 0.50) < 1e-6

    def test_blend_weight_honoured(self) -> None:
        """Custom model_weight is applied correctly."""
        # All-market (w=0): p_final == p_market
        r = compute_final_probability(80.0, 1.90, 1.90, model_weight=0.0)
        assert abs(r["p_final"] - r["p_market"]) < 1e-6

        # All-model (w=1): p_final == p_model
        r = compute_final_probability(80.0, 1.90, 1.90, model_weight=1.0)
        assert abs(r["p_final"] - r["p_model"]) < 1e-6

    def test_output_keys_present(self) -> None:
        result = compute_final_probability(70.0, 1.90, 1.90)
        for key in ("p_model", "p_market", "p_market_source", "p_final", "ev_final"):
            assert key in result, f"Missing key: {key}"
