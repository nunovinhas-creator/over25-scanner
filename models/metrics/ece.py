"""
Expected Calibration Error (ECE) and related calibration metrics.

A well-calibrated model should have ECE → 0: when it says "60% chance",
roughly 60% of such events should occur.

References
----------
- Naeini, M.P., Cooper, G., Hauskrecht, M. (2015) "Obtaining Well Calibrated
  Probabilities Using Bayesian Binning". AAAI-15.
- Guo, C., Pleiss, G., Sun, Y., Weinberger, K.Q. (2017) "On Calibration of
  Modern Neural Networks". ICML-17.
"""

from __future__ import annotations

import logging
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate(probs: np.ndarray, outcomes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Coerce, validate and strip NaN pairs."""
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


def _bin_data(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int,
    strategy: Literal["uniform", "quantile"],
) -> list[dict]:
    """
    Bin (prob, outcome) pairs and compute per-bin statistics.

    Returns a list of dicts with keys: lo, hi, n, mean_prob, frac_pos, gap.
    Bins with zero samples are omitted.
    """
    if strategy == "uniform":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    elif strategy == "quantile":
        quantiles = np.linspace(0.0, 100.0, n_bins + 1)
        edges = np.percentile(probs, quantiles)
        # Ensure strictly increasing edges for boundary conditions
        edges = np.unique(edges)
        if len(edges) < 2:
            edges = np.linspace(0.0, 1.0, n_bins + 1)
    else:
        raise ValueError(f"strategy must be 'uniform' or 'quantile', got '{strategy}'")

    bins = []
    for i in range(len(edges) - 1):
        lo, hi = edges[i], edges[i + 1]
        if i < len(edges) - 2:
            mask = (probs >= lo) & (probs < hi)
        else:
            mask = (probs >= lo) & (probs <= hi)  # include right edge for last bin

        n = int(mask.sum())
        if n == 0:
            continue

        mean_p = float(np.mean(probs[mask]))
        frac_p = float(np.mean(outcomes[mask]))
        bins.append(
            {
                "lo": float(lo),
                "hi": float(hi),
                "n": n,
                "mean_prob": mean_p,
                "frac_pos": frac_p,
                "gap": frac_p - mean_p,  # positive = underconfident, negative = overconfident
            }
        )

    return bins


# ---------------------------------------------------------------------------
# Primary calibration metrics
# ---------------------------------------------------------------------------


def ece(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
    strategy: Literal["uniform", "quantile"] = "uniform",
) -> float:
    """
    Expected Calibration Error (ECE).

    ECE = sum_k( (n_k / N) * |frac_pos_k - mean_pred_k| )

    Parameters
    ----------
    probs:
        Predicted probabilities in [0, 1], shape (N,).
    outcomes:
        Binary outcomes (0 or 1), shape (N,).
    n_bins:
        Number of calibration bins.
    strategy:
        ``'uniform'`` — equal-width bins (default);
        ``'quantile'`` — equal-frequency bins.

    Returns
    -------
    float
        ECE in [0, 1]; 0 = perfectly calibrated.
    """
    probs, outcomes = _validate(probs, outcomes)
    n = len(probs)
    bins = _bin_data(probs, outcomes, n_bins, strategy)

    ece_val = sum((b["n"] / n) * abs(b["gap"]) for b in bins)
    return float(ece_val)


def mce(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Maximum Calibration Error (MCE).

    MCE = max_k( |frac_pos_k - mean_pred_k| )

    MCE highlights the worst single bin — useful for detecting pathological
    probability regions.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.
    n_bins:
        Number of equal-width bins.

    Returns
    -------
    float
        MCE in [0, 1].
    """
    probs, outcomes = _validate(probs, outcomes)
    bins = _bin_data(probs, outcomes, n_bins, "uniform")
    if not bins:
        return 0.0
    return float(max(abs(b["gap"]) for b in bins))


def ace(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> float:
    """
    Average Calibration Error (ACE) — simple unweighted mean of per-bin gaps.

    Unlike ECE (which weights by bin frequency), ACE gives equal weight to
    all non-empty bins, making it sensitive to mis-calibration in low-count
    regions.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.
    n_bins:
        Number of equal-width bins.

    Returns
    -------
    float
        ACE in [0, 1].
    """
    probs, outcomes = _validate(probs, outcomes)
    bins = _bin_data(probs, outcomes, n_bins, "uniform")
    if not bins:
        return 0.0
    return float(np.mean([abs(b["gap"]) for b in bins]))


# ---------------------------------------------------------------------------
# Overconfidence / underconfidence detection
# ---------------------------------------------------------------------------


def detect_overconfidence(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> list[dict]:
    """
    Identify probability bands where the model is mis-calibrated.

    A band is overconfident if mean_pred > frac_pos (model predicts
    too high); underconfident if mean_pred < frac_pos.

    Severity is classified as:
    - ``'mild'``     — |gap| in [0.05, 0.10)
    - ``'moderate'`` — |gap| in [0.10, 0.20)
    - ``'severe'``   — |gap| ≥ 0.20

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.
    n_bins:
        Number of equal-width bins.

    Returns
    -------
    list[dict]
        Each item has: band (tuple), predicted (float), actual (float),
        gap (float), direction ('over' or 'under'), severity (str), n (int).
        Bins with |gap| < 0.05 or n < 5 are excluded.
    """
    probs, outcomes = _validate(probs, outcomes)
    bins = _bin_data(probs, outcomes, n_bins, "uniform")

    results = []
    for b in bins:
        gap = b["gap"]
        abs_gap = abs(gap)
        if abs_gap < 0.05 or b["n"] < 5:
            continue

        if abs_gap < 0.10:
            severity = "mild"
        elif abs_gap < 0.20:
            severity = "moderate"
        else:
            severity = "severe"

        results.append(
            {
                "band": (round(b["lo"], 3), round(b["hi"], 3)),
                "predicted": round(b["mean_prob"], 4),
                "actual": round(b["frac_pos"], 4),
                "gap": round(gap, 4),
                "direction": "under" if gap > 0 else "over",
                "severity": severity,
                "n": b["n"],
            }
        )

    # Sort by severity descending, then by |gap|
    severity_order = {"severe": 0, "moderate": 1, "mild": 2}
    results.sort(key=lambda x: (severity_order[x["severity"]], -abs(x["gap"])))
    return results


# ---------------------------------------------------------------------------
# Full calibration report
# ---------------------------------------------------------------------------


def calibration_report(
    probs: np.ndarray,
    outcomes: np.ndarray,
) -> dict:
    """
    Generate a comprehensive calibration report.

    Computes ECE (uniform), ECE (quantile), MCE, ACE and provides a
    human-readable recommendation string.

    Parameters
    ----------
    probs:
        Predicted probabilities.
    outcomes:
        Binary outcomes.

    Returns
    -------
    dict with keys:
        ece_uniform, ece_quantile, mce, ace,
        overconfident_bands, underconfident_bands,
        base_rate, n, recommendation (str)
    """
    probs, outcomes = _validate(probs, outcomes)

    ece_u = ece(probs, outcomes, n_bins=10, strategy="uniform")
    ece_q = ece(probs, outcomes, n_bins=10, strategy="quantile")
    mce_v = mce(probs, outcomes, n_bins=10)
    ace_v = ace(probs, outcomes, n_bins=10)

    bands = detect_overconfidence(probs, outcomes, n_bins=10)
    over_bands = [b for b in bands if b["direction"] == "over"]
    under_bands = [b for b in bands if b["direction"] == "under"]

    # Recommendation logic
    if ece_u < 0.03:
        rec = (
            "Calibration is excellent (ECE < 3%). No recalibration required."
        )
    elif ece_u < 0.07:
        if over_bands:
            rec = (
                f"Mild overconfidence detected in {len(over_bands)} band(s). "
                "Consider Platt scaling or isotonic regression."
            )
        elif under_bands:
            rec = (
                f"Mild underconfidence detected in {len(under_bands)} band(s). "
                "Consider Platt scaling."
            )
        else:
            rec = "Moderate calibration error (ECE 3-7%). Platt scaling recommended."
    else:
        severe = [b for b in bands if b["severity"] == "severe"]
        if severe:
            rec = (
                f"Poor calibration (ECE >= 7%) with {len(severe)} severe band(s). "
                "Isotonic regression or temperature scaling strongly recommended."
            )
        else:
            rec = (
                "Poor calibration (ECE >= 7%). Isotonic regression or Platt "
                "scaling recommended before using probabilities for staking."
            )

    return {
        "ece_uniform": round(ece_u, 6),
        "ece_quantile": round(ece_q, 6),
        "mce": round(mce_v, 6),
        "ace": round(ace_v, 6),
        "overconfident_bands": over_bands,
        "underconfident_bands": under_bands,
        "base_rate": round(float(np.mean(outcomes)), 4),
        "n": int(len(outcomes)),
        "recommendation": rec,
    }
