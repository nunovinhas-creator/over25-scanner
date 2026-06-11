"""
De-vig (margin removal) for Over/Under 2.5 odds pairs.

Two methods are provided:
  - metodo_multiplicativo : fast closed-form proportional removal
  - metodo_shin           : Shin (1992) insider-model; more accurate when the
                            market has informed money on one side

References:
  Shin, H. (1992). Prices of State Contingent Claims with Insider Traders,
  and the Favourite-Longshot Bias. Economic Journal, 102(411), 426-435.

  Cheung, K. et al. (2011). A comparison of methods for estimating
  probabilities in betting markets. Journal of the Royal Statistical
  Society Series A, 174(3), 805-825.
"""

from __future__ import annotations

import math
from typing import Tuple

from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_odds(odds_over: float, odds_under: float) -> None:
    if odds_over <= 1.0:
        raise ValueError(f"odds_over must be > 1.0, got {odds_over}")
    if odds_under <= 1.0:
        raise ValueError(f"odds_under must be > 1.0, got {odds_under}")


# ---------------------------------------------------------------------------
# Multiplicative method
# ---------------------------------------------------------------------------


def metodo_multiplicativo(
    odds_over: float,
    odds_under: float,
) -> Tuple[float, float]:
    """
    Remove bookmaker margin proportionally (multiplicative method).

    Each implied probability is divided by the total overround so that
    the de-vigged probabilities sum to exactly 1.

    Parameters
    ----------
    odds_over : float  — decimal odds for Over 2.5 (> 1.0)
    odds_under : float — decimal odds for Under 2.5 (> 1.0)

    Returns
    -------
    (p_over, p_under) : de-vigged probabilities summing to 1.0

    Examples
    --------
    >>> metodo_multiplicativo(1.90, 1.90)
    (0.5, 0.5)
    >>> p_over, _ = metodo_multiplicativo(1.95, 1.85)
    >>> round(p_over, 4)
    0.4868
    """
    _validate_odds(odds_over, odds_under)
    p_over_raw = 1.0 / odds_over
    p_under_raw = 1.0 / odds_under
    margem = p_over_raw + p_under_raw
    p_over = p_over_raw / margem
    p_under = p_under_raw / margem
    return p_over, p_under


# ---------------------------------------------------------------------------
# Shin (1992) method
# ---------------------------------------------------------------------------


def _shin_prob(q: float, z: float, M: float) -> float:
    """Single-outcome Shin probability given insider fraction z and overround M."""
    return (math.sqrt(z * z + 4.0 * (1.0 - z) * q * q / M) - z) / (2.0 * (1.0 - z))


def metodo_shin(
    odds_over: float,
    odds_under: float,
    tol: float = 1e-10,
) -> Tuple[float, float]:
    """
    Remove bookmaker margin using Shin (1992) insider model.

    Models the overround as arising from the presence of informed traders
    (fraction z) rather than pure proportional markup.  This corrects the
    favourite-longshot bias more accurately than the multiplicative method
    for skewed markets.

    The insider fraction z is solved iteratively (brentq) such that
    p_over + p_under = 1.

    Parameters
    ----------
    odds_over : float  — decimal odds for Over 2.5 (> 1.0)
    odds_under : float — decimal odds for Under 2.5 (> 1.0)
    tol : float        — solver tolerance (default 1e-10)

    Returns
    -------
    (p_over, p_under) : de-vigged probabilities summing to 1.0

    Examples
    --------
    >>> p_over, p_under = metodo_shin(1.90, 1.90)
    >>> round(p_over, 6)
    0.5
    """
    _validate_odds(odds_over, odds_under)
    q_o = 1.0 / odds_over
    q_u = 1.0 / odds_under
    M = q_o + q_u

    def _residual(z: float) -> float:
        if z >= 1.0:
            return -1.0
        return _shin_prob(q_o, z, M) + _shin_prob(q_u, z, M) - 1.0

    # At z=0: sum = sqrt(M) > 1  →  residual > 0
    # At z→1: sum → 0            →  residual < 0
    z_star = brentq(_residual, 0.0, 1.0 - 1e-9, xtol=tol, maxiter=200)
    p_over = _shin_prob(q_o, z_star, M)
    # Force exact complement for numerical stability
    p_under = 1.0 - p_over
    return p_over, p_under


# ---------------------------------------------------------------------------
# Convenience: de-vig with method selection
# ---------------------------------------------------------------------------


def devig(
    odds_over: float,
    odds_under: float,
    method: str = "multiplicative",
) -> Tuple[float, float]:
    """
    Remove bookmaker margin from an over/under odds pair.

    Parameters
    ----------
    odds_over : float
    odds_under : float
    method : str
        'multiplicative' (default) or 'shin'

    Returns
    -------
    (p_over, p_under)
    """
    if method == "shin":
        return metodo_shin(odds_over, odds_under)
    return metodo_multiplicativo(odds_over, odds_under)
