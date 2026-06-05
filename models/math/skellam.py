"""
Skellam distribution utilities for soccer goal prediction.

The Skellam distribution models the difference X = Y1 - Y2 where Y1 ~ Poisson(mu1)
and Y2 ~ Poisson(mu2).  In soccer: X = goals_home - goals_away.

Reference: Skellam, J.G. (1946). "The frequency distribution of the difference
between two Poisson variates belonging to different populations."
J. Royal Statistical Society, 109(3), 296.
"""

from __future__ import annotations

import math
import warnings
from typing import Tuple

import numpy as np
from scipy.special import ive  # exponentially scaled modified Bessel function
from scipy.optimize import brentq


# ---------------------------------------------------------------------------
# Skellam PMF
# ---------------------------------------------------------------------------

def skellam_pmf(k: int, mu1: float, mu2: float) -> float:
    """
    Probability mass function of the Skellam distribution.

    P(X = k) where X = Poisson(mu1) - Poisson(mu2).

    Uses the exponentially-scaled modified Bessel function of the first kind
    (scipy.special.ive) to avoid numerical overflow for large mu1, mu2.

    P(X=k) = exp(-(mu1+mu2)) * (mu1/mu2)^(k/2) * I_|k|(2*sqrt(mu1*mu2))

    Equivalently (numerically stable form):
        P(X=k) = exp(-(mu1+mu2) + 2*sqrt(mu1*mu2)) * (mu1/mu2)^(k/2)
                 * ive(|k|, 2*sqrt(mu1*mu2))

    Parameters
    ----------
    k : int
        Observed difference (can be any integer, positive or negative).
    mu1 : float
        Rate parameter for the first Poisson (e.g. home goals, mu >= 0).
    mu2 : float
        Rate parameter for the second Poisson (e.g. away goals, mu >= 0).

    Returns
    -------
    float in [0, 1]
    """
    mu1 = max(float(mu1), 1e-10)
    mu2 = max(float(mu2), 1e-10)
    k = int(k)
    abs_k = abs(k)

    two_sqrt = 2.0 * math.sqrt(mu1 * mu2)

    # log P(X=k) = -(mu1+mu2) + 0.5*k*log(mu1/mu2) + log(I_|k|(2*sqrt(mu1*mu2)))
    # Using ive: I_k(x) = ive(k,x) * exp(x)
    # → log I_k(x) = log(ive(k,x)) + x
    bessel_val = ive(abs_k, two_sqrt)  # ive is numerically stable
    if bessel_val <= 0:
        return 0.0

    log_p = (
        -(mu1 + mu2)
        + 0.5 * k * math.log(mu1 / mu2)
        + math.log(bessel_val)
        + two_sqrt
    )
    return float(np.clip(math.exp(log_p), 0.0, 1.0))


def skellam_cdf(k_max: int, mu1: float, mu2: float) -> float:
    """
    Cumulative P(X <= k_max) for the Skellam distribution.

    Parameters
    ----------
    k_max : int
        Upper bound (inclusive).
    mu1, mu2 : float
        Poisson rate parameters.

    Returns
    -------
    float in [0, 1]
    """
    mu1 = max(float(mu1), 1e-10)
    mu2 = max(float(mu2), 1e-10)
    # Compute over a range wide enough to capture the mass
    k_lo = int(math.floor(-4 * math.sqrt(mu1 + mu2) - 1))
    total = sum(skellam_pmf(k, mu1, mu2) for k in range(k_lo, k_max + 1))
    return float(np.clip(total, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Over 2.5 from Skellam + marginals
# ---------------------------------------------------------------------------

def prob_over25_skellam(mu_h: float, mu_a: float, max_goals: int = 12) -> float:
    """
    Compute P(goals_home + goals_away > 2.5) using the Skellam distribution
    combined with Poisson marginals.

    Strategy: sum P(gh + ga > 2) over all (gh, ga) pairs where gh ~ Poisson(mu_h),
    ga ~ Poisson(mu_a), and the joint probability is approximated from the
    marginals.  Under the assumption of independence (standard for Poisson
    goal models) the joint P(gh, ga) = P_Poisson(gh; mu_h) * P_Poisson(ga; mu_a).

    The Skellam distribution is used separately to derive P(home win / draw / away
    win), which is exposed as a secondary output.

    Parameters
    ----------
    mu_h : float
        Expected goals for home team.
    mu_a : float
        Expected goals for away team.
    max_goals : int
        Truncation level (per team).

    Returns
    -------
    float in [0, 1] — P(total goals > 2.5)
    """
    from scipy.stats import poisson

    mu_h = max(float(mu_h), 1e-6)
    mu_a = max(float(mu_a), 1e-6)

    g = np.arange(0, max_goals + 1)
    pmf_h = poisson.pmf(g, mu_h)
    pmf_a = poisson.pmf(g, mu_a)

    p_under = 0.0
    for gh in range(max_goals + 1):
        for ga in range(max_goals + 1):
            if gh + ga <= 2:
                p_under += pmf_h[gh] * pmf_a[ga]

    return float(np.clip(1.0 - p_under, 0.0, 1.0))


def match_outcome_probs_skellam(mu_h: float, mu_a: float) -> Tuple[float, float, float]:
    """
    Compute 1X2 probabilities using the Skellam distribution.

    P(home win)  = P(X > 0)
    P(draw)      = P(X = 0) = skellam_pmf(0, mu_h, mu_a)
    P(away win)  = P(X < 0)

    Parameters
    ----------
    mu_h, mu_a : float
        Expected goals.

    Returns
    -------
    (p_home, p_draw, p_away) each in [0, 1], summing to ≈ 1.
    """
    mu_h = max(float(mu_h), 1e-10)
    mu_a = max(float(mu_a), 1e-10)

    p_draw = skellam_pmf(0, mu_h, mu_a)

    # P(X >= 1) = 1 - CDF(0)
    p_home = 1.0 - skellam_cdf(0, mu_h, mu_a)

    p_away = max(0.0, 1.0 - p_home - p_draw)
    return float(p_home), float(p_draw), float(p_away)


# ---------------------------------------------------------------------------
# Elo → expected goals conversion
# ---------------------------------------------------------------------------

def skellam_from_elo(
    elo_h: float,
    elo_a: float,
    avg_goals: float = 2.7,
    home_advantage_elo: float = 100.0,
) -> Tuple[float, float]:
    """
    Convert an Elo difference to expected goals for each team.

    Logic:
    1. Adjust Elo for home advantage.
    2. Use the standard Elo expected score formula to get P(home points).
    3. Map P(home win) to a ratio of expected goals, keeping total = avg_goals.

    Parameters
    ----------
    elo_h : float
        Elo rating of the home team (raw, before home advantage).
    elo_a : float
        Elo rating of the away team.
    avg_goals : float
        Assumed average total goals in the league (default 2.7 for European football).
    home_advantage_elo : float
        Elo points added to home team to reflect home advantage.

    Returns
    -------
    (mu_h, mu_a) : Tuple[float, float]
        Expected goals for home and away team.
    """
    elo_h_adj = float(elo_h) + home_advantage_elo
    elo_a_adj = float(elo_a)

    # Standard Elo: E(home) = 1 / (1 + 10^((elo_a - elo_h)/400))
    diff = elo_a_adj - elo_h_adj
    e_home = 1.0 / (1.0 + 10.0 ** (diff / 400.0))
    e_away = 1.0 - e_home

    # Distribute total expected goals proportionally.
    # Weaker team scores fewer goals; stronger team scores more.
    # Rescale so that mu_h + mu_a = avg_goals.
    # Use a smooth allocation: mu_h = avg_goals * e_home / (e_home + e_away)
    # which simplifies to avg_goals * e_home (since they sum to 1).
    # But that makes mu_h >> mu_a for strong home teams, which is correct.
    mu_h = avg_goals * e_home
    mu_a = avg_goals * e_away

    # Clip to reasonable range
    mu_h = float(np.clip(mu_h, 0.2, 5.0))
    mu_a = float(np.clip(mu_a, 0.2, 5.0))
    return mu_h, mu_a


# ---------------------------------------------------------------------------
# Reverse-engineer xG from 1X2 market odds
# ---------------------------------------------------------------------------

def expected_goals_from_1x2(
    p_home: float,
    p_draw: float,
    p_away: float,
    avg_goals: float = 2.7,
    tol: float = 1e-6,
    max_iter: int = 200,
) -> Tuple[float, float]:
    """
    Reverse-engineer expected goals (mu_h, mu_a) from true 1X2 probabilities.

    The function finds (mu_h, mu_a) such that the Skellam-derived 1X2 probs
    match the supplied market probabilities, subject to:
        mu_h + mu_a ≈ avg_goals   (can be relaxed via avg_goals=None)

    Algorithm:
    - Fix total goals = avg_goals as a soft constraint.
    - Use a 1-D root finding on the home-win probability to find
      the mu_h / mu_a split.
    - If the supplied probs do not sum to 1, they are renormalised first.

    Parameters
    ----------
    p_home, p_draw, p_away : float
        True (overround-removed) probabilities.  Need not sum to exactly 1.
    avg_goals : float
        Total expected goals to preserve.
    tol : float
        Root-finding tolerance.
    max_iter : int
        Maximum Brent iterations.

    Returns
    -------
    (mu_h, mu_a) : Tuple[float, float]
        Estimated expected goals.
    """
    p_home = float(p_home)
    p_draw = float(p_draw)
    p_away = float(p_away)

    total = p_home + p_draw + p_away
    if abs(total - 1.0) > 0.05:
        # Renormalise
        p_home /= total
        p_draw /= total
        p_away /= total

    # Validate
    for p, name in [(p_home, "p_home"), (p_draw, "p_draw"), (p_away, "p_away")]:
        if not (0.0 < p < 1.0):
            raise ValueError(f"{name}={p} must be in (0, 1)")

    # Target home-win prob
    target_pw = p_home

    # Under the constraint mu_h + mu_a = avg_goals, define mu_h = t, mu_a = avg_goals - t
    def residual(t: float) -> float:
        mu_h_try = t
        mu_a_try = avg_goals - t
        if mu_a_try <= 0 or mu_h_try <= 0:
            return target_pw - 0.5
        ph, _, _ = match_outcome_probs_skellam(mu_h_try, mu_a_try)
        return ph - target_pw

    lo = 1e-3
    hi = avg_goals - 1e-3

    # Check bracket
    try:
        f_lo = residual(lo)
        f_hi = residual(hi)
        if f_lo * f_hi > 0:
            # No bracket — fall back to simple proportional split
            mu_h = avg_goals * p_home / (p_home + p_away)
            mu_a = avg_goals - mu_h
            return float(np.clip(mu_h, 0.2, 5.0)), float(np.clip(mu_a, 0.2, 5.0))

        mu_h = brentq(residual, lo, hi, xtol=tol, maxiter=max_iter)
        mu_a = avg_goals - mu_h
    except Exception:
        # Fallback
        mu_h = avg_goals * p_home / (p_home + p_away)
        mu_a = avg_goals - mu_h

    return float(np.clip(mu_h, 0.2, 5.0)), float(np.clip(mu_a, 0.2, 5.0))
