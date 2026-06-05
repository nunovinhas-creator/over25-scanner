"""
Brier score and reliability metrics for probability forecasts.

Implements the full Brier decomposition (Murphy 1973) plus helpers for
reliability diagrams and forecast sharpness / resolution.

References
----------
- Brier, G.W. (1950) "Verification of Forecasts Expressed in Terms of
  Probability". Monthly Weather Review, 78(1), 1-3.
- Murphy, A.H. (1973) "A New Vector Partition of the Probability Score".
  Journal of Applied Meteorology, 12(4), 595-600.
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_inputs(
    probs: np.ndarray, outcomes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and coerce inputs; drop NaN pairs."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)

    if probs.shape != outcomes.shape:
        raise ValueError(
            f"probs and outcomes must have the same shape; "
            f"got {probs.shape} vs {outcomes.shape}"
        )

    mask = ~(np.isnan(probs) | np.isnan(outcomes))
    probs, outcomes = probs[mask], outcomes[mask]

    if len(probs) == 0:
        raise ValueError("No valid (non-NaN) probability/outcome pairs found.")

    if np.any((probs < 0) | (probs > 1)):
        raise ValueError("probs must be in [0, 1].")

    if not np.all(np.isin(outcomes, [0, 1])):
        raise ValueError("outcomes must be binary (0 or 1).")

    return probs, outcomes


# ---------------------------------------------------------------------------
# Core metrics
# ---------------------------------------------------------------------------


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """
    Compute the Mean Brier Score (lower is better).

    BS = (1/N) * sum( (p_i - o_i)^2 )

    Parameters
    ----------
    probs:
        Predicted probabilities in [0, 1], shape (N,).
    outcomes:
        Binary outcomes (0 or 1), shape (N,).

    Returns
    -------
    float
        Mean Brier Score in [0, 1]; perfect forecast → 0.0.
    """
    probs, outcomes = _validate_inputs(probs, outcomes)
    return float(np.mean((probs - outcomes) ** 2))


def brier_skill_score(
    probs: np.ndarray,
    outcomes: np.ndarray,
    baseline_prob: Optional[float] = None,
) -> float:
    """
    Brier Skill Score (BSS) relative to a climatological baseline.

    BSS = 1 - BS / BS_ref

    A BSS of 1.0 is perfect; 0.0 means the model matches the climatological
    baseline; negative values indicate the model is worse than climatology.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.
    baseline_prob:
        Climatological base rate.  If ``None`` (default), the observed
        frequency in ``outcomes`` is used as the baseline.

    Returns
    -------
    float
        Brier Skill Score (may be negative).
    """
    probs, outcomes = _validate_inputs(probs, outcomes)
    bs = brier_score(probs, outcomes)

    if baseline_prob is None:
        baseline_prob = float(np.mean(outcomes))

    bs_ref = brier_score(
        np.full_like(probs, baseline_prob), outcomes
    )

    if bs_ref == 0.0:
        logger.warning(
            "brier_skill_score: baseline BS is 0 (all outcomes identical). "
            "Returning 0.0."
        )
        return 0.0

    return float(1.0 - bs / bs_ref)


# ---------------------------------------------------------------------------
# Brier decomposition (Murphy 1973)
# ---------------------------------------------------------------------------


def uncertainty(outcomes: np.ndarray) -> float:
    """
    Brier decomposition: Uncertainty component.

    UNC = o_bar * (1 - o_bar)

    This is the Brier score of a perfectly calibrated climatological forecast.
    It is a property of the outcome distribution only — independent of the
    forecast.

    Parameters
    ----------
    outcomes:
        Binary outcome array.

    Returns
    -------
    float
    """
    outcomes = np.asarray(outcomes, dtype=float)
    outcomes = outcomes[~np.isnan(outcomes)]
    o_bar = float(np.mean(outcomes))
    return float(o_bar * (1.0 - o_bar))


def sharpness(probs: np.ndarray) -> float:
    """
    Sharpness: variance of the probability forecasts.

    Higher sharpness means the model issues more decisive (extreme)
    probabilities. Sharpness is a necessary but not sufficient condition
    for skill — a sharp but poorly calibrated model can be worse than
    climatology.

    Parameters
    ----------
    probs:
        Predicted probabilities.

    Returns
    -------
    float
        Sample variance of probs.
    """
    probs = np.asarray(probs, dtype=float)
    probs = probs[~np.isnan(probs)]
    if len(probs) == 0:
        return 0.0
    return float(np.var(probs, ddof=0))


def resolution(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """
    Brier decomposition: Resolution component.

    RES = (1/N) * sum_k( n_k * (o_bar_k - o_bar)^2 )

    where o_bar_k is the mean outcome in probability bin k, and o_bar is
    the overall base rate.  Higher resolution is better — it reflects how
    much the forecast can distinguish outcome subgroups.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.

    Returns
    -------
    float
    """
    probs, outcomes = _validate_inputs(probs, outcomes)
    o_bar = float(np.mean(outcomes))
    n = len(outcomes)

    bins = np.linspace(0.0, 1.0 + 1e-9, 11)  # 10 equal-width bins
    res = 0.0
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (probs >= lo) & (probs < hi)
        n_k = mask.sum()
        if n_k == 0:
            continue
        o_bar_k = float(np.mean(outcomes[mask]))
        res += n_k * (o_bar_k - o_bar) ** 2

    return float(res / n)


def reliability_diagram_data(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> dict:
    """
    Compute binned data for a reliability (calibration) diagram.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.
    n_bins:
        Number of equal-width bins in [0, 1].

    Returns
    -------
    dict with keys:
        - ``bins``             : list of (lo, hi) tuples for each bin
        - ``mean_predicted``   : list of mean predicted probability per bin
        - ``fraction_positive``: list of observed event frequency per bin
        - ``counts``           : list of sample counts per bin
    """
    probs, outcomes = _validate_inputs(probs, outcomes)
    edges = np.linspace(0.0, 1.0 + 1e-9, n_bins + 1)

    bins_out: list[tuple[float, float]] = []
    mean_pred: list[float] = []
    frac_pos: list[float] = []
    counts_out: list[int] = []

    for lo, hi in zip(edges[:-1], edges[1:]):
        mask = (probs >= lo) & (probs < hi)
        n_k = int(mask.sum())
        bins_out.append((float(lo), float(hi)))
        counts_out.append(n_k)
        if n_k == 0:
            mean_pred.append(float((lo + hi) / 2))
            frac_pos.append(float("nan"))
        else:
            mean_pred.append(float(np.mean(probs[mask])))
            frac_pos.append(float(np.mean(outcomes[mask])))

    return {
        "bins": bins_out,
        "mean_predicted": mean_pred,
        "fraction_positive": frac_pos,
        "counts": counts_out,
    }


def full_decomposition(
    probs: np.ndarray,
    outcomes: np.ndarray,
) -> dict:
    """
    Full Brier score decomposition: BS = UNC - RES + REL.

    Where:
    - UNC (Uncertainty)  — irreducible component based on base rate
    - RES (Resolution)   — how well forecast separates outcome subgroups
    - REL (Reliability)  — calibration error (0 = perfectly calibrated)
    - BS                 — total Brier score
    - BSS                — skill score vs climatology

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.

    Returns
    -------
    dict with keys: bs, bss, unc, res, rel, n, base_rate
    """
    probs, outcomes = _validate_inputs(probs, outcomes)
    bs = brier_score(probs, outcomes)
    bss = brier_skill_score(probs, outcomes)
    unc = uncertainty(outcomes)
    res = resolution(probs, outcomes)
    # REL = BS - UNC + RES  (Murphy decomposition: BS = UNC - RES + REL)
    rel = bs - unc + res

    return {
        "bs": round(bs, 6),
        "bss": round(bss, 6),
        "unc": round(unc, 6),
        "res": round(res, 6),
        "rel": round(rel, 6),
        "n": int(len(outcomes)),
        "base_rate": round(float(np.mean(outcomes)), 4),
    }
