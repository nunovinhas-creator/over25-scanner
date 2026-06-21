"""
tests/test_btts_over25.py
--------------------------
Testes unitários para build_dc_grid e extract_btts_over25_prob.
"""
from __future__ import annotations
import numpy as np
import pytest
from models.math.poisson import build_dc_grid, extract_btts_over25_prob


class TestBuildDcGrid:
    def test_shape(self):
        grid = build_dc_grid(1.5, 1.0, max_goals=10)
        assert grid.shape == (11, 11)

    def test_sums_to_one(self):
        grid = build_dc_grid(1.5, 1.2, rho=-0.1, max_goals=10)
        assert abs(grid.sum() - 1.0) < 0.01  # truncation ≈1

    def test_all_non_negative(self):
        grid = build_dc_grid(2.0, 1.5, rho=-0.05)
        assert (grid >= 0).all()

    def test_zero_zero_cell(self):
        """grid[0, 0] = P(0-0) should be non-trivial for small lambdas."""
        grid = build_dc_grid(0.5, 0.5, rho=0.0)
        assert grid[0, 0] > 0.1

    def test_rho_zero_equals_outer_product(self):
        """Without DC correction, grid = outer product of marginal PMFs."""
        from scipy.stats import poisson
        lh, la = 1.5, 1.2
        grid = build_dc_grid(lh, la, rho=0.0, max_goals=6)
        g = np.arange(7)
        expected = np.outer(poisson.pmf(g, lh), poisson.pmf(g, la))
        np.testing.assert_allclose(grid, expected, rtol=1e-10)


class TestExtractBttsOver25Prob:
    def _make_degenerate_grid(self, x: int, y: int, max_goals: int = 10) -> np.ndarray:
        """Grid with all mass at scoreline (x, y)."""
        g = np.zeros((max_goals + 1, max_goals + 1))
        g[x, y] = 1.0
        return g

    def test_zero_zero_does_not_contribute(self):
        """0-0: x=0, does not meet x>=1 — should return 0."""
        grid = self._make_degenerate_grid(0, 0)
        assert extract_btts_over25_prob(grid) == pytest.approx(0.0)

    def test_one_zero_does_not_contribute(self):
        """1-0: y=0, does not meet y>=1 — should return 0."""
        grid = self._make_degenerate_grid(1, 0)
        assert extract_btts_over25_prob(grid) == pytest.approx(0.0)

    def test_zero_one_does_not_contribute(self):
        """0-1: x=0, does not meet x>=1 — should return 0."""
        grid = self._make_degenerate_grid(0, 1)
        assert extract_btts_over25_prob(grid) == pytest.approx(0.0)

    def test_one_one_does_not_contribute(self):
        """1-1: BTTS but total=2 (not Over 2.5) — should return 0."""
        grid = self._make_degenerate_grid(1, 1)
        assert extract_btts_over25_prob(grid) == pytest.approx(0.0)

    def test_one_two_contributes(self):
        """1-2: x>=1, y>=1, total=3 >= 3 — BTTS AND Over 2.5."""
        grid = self._make_degenerate_grid(1, 2)
        assert extract_btts_over25_prob(grid) == pytest.approx(1.0)

    def test_two_one_contributes(self):
        """2-1: symmetric to 1-2."""
        grid = self._make_degenerate_grid(2, 1)
        assert extract_btts_over25_prob(grid) == pytest.approx(1.0)

    def test_two_two_contributes(self):
        """2-2: x>=1, y>=1, total=4 >= 3."""
        grid = self._make_degenerate_grid(2, 2)
        assert extract_btts_over25_prob(grid) == pytest.approx(1.0)

    def test_three_nil_does_not_contribute(self):
        """3-0: total=3 but y=0, not BTTS."""
        grid = self._make_degenerate_grid(3, 0)
        assert extract_btts_over25_prob(grid) == pytest.approx(0.0)

    def test_output_between_zero_and_one(self):
        """p_dc_conjunta must be in [0, 1] for any realistic lambda."""
        grid = build_dc_grid(2.0, 1.5, rho=-0.08)
        p = extract_btts_over25_prob(grid)
        assert 0.0 <= p <= 1.0

    def test_higher_lambdas_higher_probability(self):
        """Higher expected goals → higher P(BTTS AND Over 2.5)."""
        g_low  = build_dc_grid(0.8, 0.6)
        g_high = build_dc_grid(2.0, 1.8)
        assert extract_btts_over25_prob(g_high) > extract_btts_over25_prob(g_low)

    def test_overlay_positive(self):
        """P(BTTS AND O2.5) > P(BTTS) × P(O2.5) due to positive correlation."""
        from models.math.poisson import prob_over25_poisson
        lh, la = 1.5, 1.2
        grid = build_dc_grid(lh, la, rho=0.0)
        p_conjunta = extract_btts_over25_prob(grid)
        p_btts     = float(grid[1:, 1:].sum())
        p_o25      = prob_over25_poisson(lh, la, rho=0.0)
        p_naive    = p_btts * p_o25
        assert p_conjunta > p_naive, (
            f"Expected p_conjunta ({p_conjunta:.4f}) > p_naive ({p_naive:.4f})"
        )
