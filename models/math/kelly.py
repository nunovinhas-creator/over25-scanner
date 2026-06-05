"""
Kelly Criterion implementations for sports betting bankroll management.

The Kelly Criterion (Kelly, 1956) gives the fraction of bankroll to wager
that maximises the expected logarithm of wealth (i.e. long-run growth rate).

Includes:
  - Full Kelly
  - Fractional Kelly (safer, reduced variance)
  - Simultaneous Kelly for correlated multi-bet portfolios (via numerical optimisation)
  - Monte Carlo drawdown simulation
  - Risk-constrained optimal fraction finder

Reference:
  Kelly, J.L. (1956). "A New Interpretation of Information Rate."
  Bell System Technical Journal, 35(4), 917-926.
"""

from __future__ import annotations

import math
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize, brentq


# ---------------------------------------------------------------------------
# Basic Kelly fractions
# ---------------------------------------------------------------------------

def kelly_full(prob: float, decimal_odds: float) -> float:
    """
    Compute the full Kelly fraction.

    f* = (b * p - q) / b

    where:
      b = decimal_odds - 1   (net gain per unit staked)
      p = probability of winning
      q = 1 - p              (probability of losing)

    Parameters
    ----------
    prob : float
        Estimated probability of winning in (0, 1).
    decimal_odds : float
        Decimal (European) odds ≥ 1.01.

    Returns
    -------
    float
        Kelly fraction in [0, 1].  Returns 0.0 if the bet has no positive
        expected value (i.e. negative Kelly → do not bet).

    Examples
    --------
    >>> kelly_full(0.6, 2.0)
    0.2
    >>> kelly_full(0.4, 2.0)   # negative EV → no bet
    0.0
    """
    prob = float(prob)
    decimal_odds = float(decimal_odds)

    if not (0.0 < prob < 1.0):
        raise ValueError(f"prob must be in (0, 1), got {prob}")
    if decimal_odds < 1.01:
        raise ValueError(f"decimal_odds must be ≥ 1.01, got {decimal_odds}")

    b = decimal_odds - 1.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return float(max(f, 0.0))


def kelly_fractional(
    prob: float,
    decimal_odds: float,
    fraction: float = 0.5,
) -> float:
    """
    Compute a fractional Kelly stake.

    Fractional Kelly reduces variance at the cost of slightly lower expected
    growth rate.  Half Kelly (fraction=0.5) is the most common choice: it
    reduces variance by ~75% while retaining ~75% of the growth rate.

    Parameters
    ----------
    prob : float
        Estimated win probability in (0, 1).
    decimal_odds : float
        Decimal odds ≥ 1.01.
    fraction : float
        Kelly multiplier, typically in (0, 1].  0.5 = half Kelly.

    Returns
    -------
    float
        Fractional Kelly stake (bounded to [0, 1]).

    Examples
    --------
    >>> kelly_fractional(0.6, 2.0, fraction=0.5)
    0.1
    """
    if not (0.0 < fraction <= 2.0):
        raise ValueError(f"fraction must be in (0, 2], got {fraction}")
    full = kelly_full(prob, decimal_odds)
    return float(np.clip(fraction * full, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Simultaneous / portfolio Kelly
# ---------------------------------------------------------------------------

def kelly_simultaneous(bets: List[Dict[str, Any]]) -> List[float]:
    """
    Compute simultaneous Kelly fractions for a portfolio of bets.

    Uses numerical optimisation to maximise the expected log-growth of
    the bankroll when multiple bets are placed at the same time.  This is
    equivalent to the logarithmic utility maximisation:

        max E[log(1 + sum_i f_i * (b_i * X_i - (1-X_i)))]

    where X_i ∈ {0, 1} is the outcome of bet i and the expectation is taken
    over all 2^n outcome combinations (feasible for n ≤ ~20).

    For large n (> 20 bets), a first-order approximation is used instead.

    Parameters
    ----------
    bets : list of dict
        Each dict must have:
          - 'prob'         : float, win probability in (0,1)
          - 'decimal_odds' : float, decimal odds ≥ 1.01
        Optional:
          - 'correlation'  : not currently modelled (independence assumed)

    Returns
    -------
    fractions : list of float
        Optimal Kelly fraction for each bet, same order as input.
        Zero values indicate no-bet (negative EV or optimiser drove to 0).

    Notes
    -----
    For the independence case with n ≤ 20 bets, all 2^n outcome vectors are
    enumerated.  For n > 20 Monte Carlo sampling (10,000 draws) is used to
    estimate the expected log-wealth gradient.

    Examples
    --------
    >>> bets = [
    ...     {'prob': 0.60, 'decimal_odds': 2.0},
    ...     {'prob': 0.55, 'decimal_odds': 2.1},
    ... ]
    >>> kelly_simultaneous(bets)
    [0.19..., 0.14...]
    """
    if not bets:
        return []

    n = len(bets)
    probs = np.array([float(b["prob"]) for b in bets])
    odds = np.array([float(b["decimal_odds"]) for b in bets])
    nets = odds - 1.0  # net gain per unit

    for i, (p, o) in enumerate(zip(probs, odds)):
        if not (0.0 < p < 1.0):
            raise ValueError(f"bets[{i}]['prob']={p} must be in (0,1)")
        if o < 1.01:
            raise ValueError(f"bets[{i}]['decimal_odds']={o} must be ≥ 1.01")

    MAX_ENUM = 20

    def _expected_log_growth(fracs: np.ndarray) -> float:
        """Negative expected log growth (to be minimised)."""
        if n <= MAX_ENUM:
            # Enumerate all 2^n outcomes
            total = 0.0
            for mask in range(1 << n):
                p_scenario = 1.0
                wealth_factor = 1.0
                for i in range(n):
                    win = bool(mask & (1 << i))
                    p_scenario *= probs[i] if win else (1.0 - probs[i])
                    if win:
                        wealth_factor += fracs[i] * nets[i]
                    else:
                        wealth_factor -= fracs[i]
                if wealth_factor <= 0:
                    return 1e9  # ruin
                total += p_scenario * math.log(wealth_factor)
            return -total
        else:
            # Monte Carlo approximation
            rng = np.random.default_rng(42)
            n_sim = 10_000
            outcomes = rng.random((n_sim, n)) < probs  # (n_sim, n) bool
            # wealth factor per simulation
            gain = outcomes * (fracs * nets) + (~outcomes) * (-fracs)
            wealth = 1.0 + gain.sum(axis=1)
            if np.any(wealth <= 0):
                return 1e9
            return -float(np.mean(np.log(wealth)))

    # Initial guess: independent full-Kelly per bet, scaled down
    f0 = np.array([kelly_full(p, o) for p, o in zip(probs, odds)])
    f0 = f0 / max(1.0, f0.sum())  # ensure total ≤ 1
    f0 = np.clip(f0, 0.0, 0.3)

    bounds = [(0.0, 0.5)] * n  # cap individual fractions at 50%

    result = minimize(
        _expected_log_growth,
        f0,
        method="SLSQP",
        bounds=bounds,
        constraints=[{"type": "ineq", "fun": lambda f: 1.0 - f.sum()}],
        options={"maxiter": 500, "ftol": 1e-10},
    )

    fracs = np.clip(result.x, 0.0, 1.0)
    # Zero out bets that had negative independent Kelly (negative EV)
    for i in range(n):
        if kelly_full(probs[i], odds[i]) == 0.0:
            fracs[i] = 0.0

    return [float(f) for f in fracs]


# ---------------------------------------------------------------------------
# Monte Carlo drawdown simulation
# ---------------------------------------------------------------------------

def kelly_drawdown_risk(
    prob: float,
    decimal_odds: float,
    fraction: float,
    n_bets: int = 100,
    n_sim: int = 10_000,
    drawdown_threshold: float = 0.20,
    seed: Optional[int] = 42,
) -> float:
    """
    Estimate P(max drawdown > threshold) over `n_bets` consecutive bets using
    Monte Carlo simulation.

    A "drawdown" is defined as the percentage decline from the highest
    bankroll value seen so far to the current value:

        drawdown_t = (peak_t - bankroll_t) / peak_t

    Parameters
    ----------
    prob : float
        Win probability per bet.
    decimal_odds : float
        Decimal odds.
    fraction : float
        Fraction of current bankroll wagered per bet (e.g. 0.05 = 5%).
    n_bets : int
        Number of bets in each simulation path.
    n_sim : int
        Number of Monte Carlo simulation paths.
    drawdown_threshold : float
        Maximum acceptable drawdown (default 0.20 = 20%).
    seed : int or None
        Random seed for reproducibility.

    Returns
    -------
    float
        Probability that the maximum drawdown exceeds `drawdown_threshold`
        at any point during the `n_bets` sequence.

    Examples
    --------
    >>> kelly_drawdown_risk(0.55, 2.0, fraction=0.10, n_bets=100, n_sim=5000)
    0.12...   # ~12% chance of 20%+ drawdown
    """
    prob = float(prob)
    decimal_odds = float(decimal_odds)
    fraction = float(fraction)
    drawdown_threshold = float(drawdown_threshold)

    if not (0.0 < prob < 1.0):
        raise ValueError(f"prob must be in (0,1), got {prob}")
    if decimal_odds < 1.01:
        raise ValueError(f"decimal_odds must be ≥ 1.01, got {decimal_odds}")
    if not (0.0 < fraction <= 1.0):
        raise ValueError(f"fraction must be in (0,1], got {fraction}")
    if drawdown_threshold <= 0.0 or drawdown_threshold >= 1.0:
        raise ValueError(f"drawdown_threshold must be in (0,1), got {drawdown_threshold}")

    rng = np.random.default_rng(seed)
    b = decimal_odds - 1.0

    # Simulate outcomes: True = win
    outcomes = rng.random((n_sim, n_bets)) < prob  # (n_sim, n_bets)

    # Compute per-bet multipliers: win → 1 + f*b, loss → 1 - f
    multipliers = np.where(outcomes, 1.0 + fraction * b, 1.0 - fraction)

    # Bankroll path starting from 1.0
    # bankroll[:, t] = product of multipliers[0..t]
    bankroll = np.cumprod(multipliers, axis=1)
    # Prepend the starting value (1.0) for peak tracking
    bankroll = np.hstack([np.ones((n_sim, 1)), bankroll])

    # Running peak
    peak = np.maximum.accumulate(bankroll, axis=1)

    # Drawdown at each step
    drawdown = (peak - bankroll) / peak

    # Maximum drawdown per simulation
    max_dd = drawdown.max(axis=1)

    prob_exceeds = float(np.mean(max_dd > drawdown_threshold))
    return prob_exceeds


# ---------------------------------------------------------------------------
# Risk-constrained optimal fraction
# ---------------------------------------------------------------------------

def optimal_fraction_by_risk(
    prob: float,
    decimal_odds: float,
    max_drawdown_prob: float = 0.05,
    n_bets: int = 100,
    n_sim: int = 5_000,
    drawdown_threshold: float = 0.20,
    tol: float = 1e-3,
    seed: Optional[int] = 42,
) -> float:
    """
    Find the maximum Kelly fraction such that P(drawdown > threshold) ≤ max_drawdown_prob.

    Uses binary search (Brent's method) to find the largest fraction `f` for
    which the simulated drawdown risk stays within the risk budget.

    Parameters
    ----------
    prob : float
        Win probability.
    decimal_odds : float
        Decimal odds.
    max_drawdown_prob : float
        Maximum acceptable probability of hitting the drawdown threshold
        (default 0.05 = 5%).
    n_bets : int
        Bet sequence length per simulation.
    n_sim : int
        Monte Carlo samples (reduced for speed; increase for precision).
    drawdown_threshold : float
        Drawdown level that defines "ruin" (default 0.20 = 20%).
    tol : float
        Brent's method tolerance on fraction.
    seed : int or None
        Random seed.

    Returns
    -------
    float
        Largest fraction in [0, 1] satisfying the drawdown risk constraint.
        Returns 0.0 if even the minimum fraction (1%) violates the constraint.

    Examples
    --------
    >>> optimal_fraction_by_risk(0.55, 2.0, max_drawdown_prob=0.05)
    0.04...  # ≈4% of bankroll per bet
    """
    prob = float(prob)
    decimal_odds = float(decimal_odds)

    if kelly_full(prob, decimal_odds) == 0.0:
        return 0.0  # negative EV → no bet at all

    # Check if even a tiny fraction violates the constraint
    min_fraction = 0.001
    risk_min = kelly_drawdown_risk(
        prob, decimal_odds, min_fraction,
        n_bets=n_bets, n_sim=n_sim,
        drawdown_threshold=drawdown_threshold, seed=seed,
    )
    if risk_min > max_drawdown_prob:
        warnings.warn(
            f"Even fraction={min_fraction} gives drawdown risk {risk_min:.3f} > "
            f"{max_drawdown_prob:.3f}. Returning 0.0.",
            UserWarning, stacklevel=2,
        )
        return 0.0

    # Check if full Kelly is already within budget
    full_k = kelly_full(prob, decimal_odds)
    risk_full = kelly_drawdown_risk(
        prob, decimal_odds, full_k,
        n_bets=n_bets, n_sim=n_sim,
        drawdown_threshold=drawdown_threshold, seed=seed,
    )
    if risk_full <= max_drawdown_prob:
        return full_k  # Full Kelly is safe

    # Binary search using brentq
    def _risk_minus_budget(f: float) -> float:
        r = kelly_drawdown_risk(
            prob, decimal_odds, f,
            n_bets=n_bets, n_sim=n_sim,
            drawdown_threshold=drawdown_threshold, seed=seed,
        )
        return r - max_drawdown_prob

    try:
        opt_fraction = brentq(
            _risk_minus_budget,
            a=min_fraction,
            b=full_k,
            xtol=tol,
            maxiter=30,
        )
    except Exception as e:
        warnings.warn(f"Brent's method failed: {e}. Returning half-Kelly.", UserWarning, stacklevel=2)
        opt_fraction = full_k * 0.5

    return float(np.clip(opt_fraction, 0.0, 1.0))


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

def format_stake(fraction: float, bankroll: float) -> str:
    """
    Format a Kelly fraction and bankroll into a human-readable stake string.

    Parameters
    ----------
    fraction : float
        Kelly fraction in [0, 1].
    bankroll : float
        Current bankroll in currency units.

    Returns
    -------
    str
        Formatted string, e.g.: "Stake: €45.00  (4.50% of €1,000.00 bankroll)"

    Examples
    --------
    >>> format_stake(0.045, 1000.0)
    'Stake: €45.00  (4.50% of €1,000.00 bankroll)'
    """
    fraction = float(fraction)
    bankroll = float(bankroll)

    if bankroll <= 0:
        raise ValueError(f"bankroll must be positive, got {bankroll}")
    if fraction < 0 or fraction > 1:
        raise ValueError(f"fraction must be in [0,1], got {fraction}")

    stake = fraction * bankroll
    pct = fraction * 100.0
    return (
        f"Stake: €{stake:,.2f}  ({pct:.2f}% of €{bankroll:,.2f} bankroll)"
    )


# ---------------------------------------------------------------------------
# Quick EV summary
# ---------------------------------------------------------------------------

def bet_summary(prob: float, decimal_odds: float, bankroll: float = 1000.0) -> dict:
    """
    Summarise a bet's Kelly metrics in a single dict.

    Parameters
    ----------
    prob : float
        Win probability.
    decimal_odds : float
        Decimal odds.
    bankroll : float
        Current bankroll (default 1000).

    Returns
    -------
    dict with keys:
        prob, decimal_odds, edge, ev_per_unit,
        kelly_full, kelly_half, kelly_quarter,
        stake_full, stake_half, stake_quarter
    """
    b = decimal_odds - 1.0
    edge = prob * decimal_odds - 1.0           # EV per unit staked
    ev_per_unit = edge

    full = kelly_full(prob, decimal_odds)
    half = kelly_fractional(prob, decimal_odds, fraction=0.5)
    quarter = kelly_fractional(prob, decimal_odds, fraction=0.25)

    return {
        "prob": round(prob, 4),
        "decimal_odds": round(decimal_odds, 3),
        "edge": round(edge, 4),
        "ev_per_unit": round(ev_per_unit, 4),
        "kelly_full": round(full, 4),
        "kelly_half": round(half, 4),
        "kelly_quarter": round(quarter, 4),
        "stake_full": round(full * bankroll, 2),
        "stake_half": round(half * bankroll, 2),
        "stake_quarter": round(quarter * bankroll, 2),
    }
