"""
ROI and betting performance metrics.

All monetary functions assume unit staking (1 unit per bet) unless otherwise
stated.  The ``picks_df`` passed to most functions is expected to be the clean
DataFrame produced by ``data.schema.picks_schema.validate_picks``.

Expected columns used:
    result_over25    — 'WIN' | 'LOSS' | 'PUSH' | 'VOID' | NaN
    odds_over        — float, decimal odds at pick time
    odds_over_close  — float, Pinnacle closing odds
    clv              — float, Closing Line Value %
    prob_over25      — float, model probability (0-100 scale)
    score_sistema    — int, composite score 0-100
    movimento        — str, SHORTENING/DRIFTING/STABLE/STEAM
    sharp_label      — str, STEAM/SHARP/WATCH or empty
    score_band       — str, e.g. '55-65'
    odds_band        — str, e.g. '1.80-2.10'
    saved_at         — pd.Timestamp, pick save time (for ordering)
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolved_mask(picks_df: pd.DataFrame) -> pd.Series:
    """Boolean mask for settled bets (WIN or LOSS only — excludes VOID/PUSH)."""
    return picks_df["result_over25"].isin(["WIN", "LOSS"])


def _returns(picks_df: pd.DataFrame) -> pd.Series:
    """
    Return per-bet P&L in units (flat staking, 1 unit per bet).

    WIN  →  odds_over - 1
    LOSS → -1
    PUSH/VOID → 0 (stake returned)
    """
    result = picks_df["result_over25"].fillna("")
    odds = pd.to_numeric(picks_df["odds_over"], errors="coerce").fillna(0.0)

    pl = pd.Series(np.nan, index=picks_df.index)
    pl[result == "WIN"] = odds[result == "WIN"] - 1.0
    pl[result == "LOSS"] = -1.0
    pl[result.isin(["PUSH", "VOID"])] = 0.0
    return pl


# ---------------------------------------------------------------------------
# Basic ROI / Yield
# ---------------------------------------------------------------------------


def roi(stakes: np.ndarray, returns: np.ndarray) -> float:
    """
    Simple ROI percentage.

    ROI = (sum(returns) - sum(stakes)) / sum(stakes) * 100

    Parameters
    ----------
    stakes:
        Amount staked per bet.
    returns:
        Amount returned per bet (stake + profit on WIN; 0 on LOSS).

    Returns
    -------
    float
        ROI in percent.  Positive = profitable.
    """
    stakes = np.asarray(stakes, dtype=float)
    returns = np.asarray(returns, dtype=float)
    total_staked = stakes.sum()
    if total_staked == 0:
        return 0.0
    return float((returns.sum() - total_staked) / total_staked * 100.0)


def yield_pct(stakes: np.ndarray, profits: np.ndarray) -> float:
    """
    Yield percentage (profit per unit staked).

    Yield = sum(profits) / sum(stakes) * 100

    Parameters
    ----------
    stakes:
        Amount staked per bet.
    profits:
        Net profit per bet (positive on WIN, negative on LOSS).

    Returns
    -------
    float
        Yield in percent.
    """
    stakes = np.asarray(stakes, dtype=float)
    profits = np.asarray(profits, dtype=float)
    total_staked = stakes.sum()
    if total_staked == 0:
        return 0.0
    return float(profits.sum() / total_staked * 100.0)


# ---------------------------------------------------------------------------
# Profit Balance (ProphitBet metric)
# ---------------------------------------------------------------------------


def profit_balance(picks_df: pd.DataFrame) -> float:
    """
    ProphitBet's profit_balance metric.

    profit_balance = wins / n_settled
    accuracy       = wins / n_settled  (i.e. hit rate)

    The insight is:  if profit_balance < accuracy, the portfolio is
    *mathematically profitable* regardless of staking.  In practice,
    profit_balance here is the ratio of winning bets weighted by their
    net P&L relative to flat stakes — a value < hit_rate implies
    positive expectation.

    Specifically:
        profit_balance = (total_profit / n_settled + 1) / 2

    For a fair comparison:  if profit_balance < hit_rate → profitable.

    Parameters
    ----------
    picks_df:
        Clean picks DataFrame.

    Returns
    -------
    float
        profit_balance value in [0, 1].
    """
    df = picks_df[_resolved_mask(picks_df)].copy()
    if df.empty:
        return 0.0

    pl = _returns(df)
    n = len(df)
    total_profit = pl.sum()

    # Normalise to [0,1]: profit per bet shifted to 0-1 range
    pb = (total_profit / n + 1.0) / 2.0
    return float(np.clip(pb, 0.0, 1.0))


# ---------------------------------------------------------------------------
# CLV Analysis
# ---------------------------------------------------------------------------


def clv_analysis(picks_df: pd.DataFrame) -> dict:
    """
    Closing Line Value (CLV) distribution analysis.

    CLV > 0 means we obtained better odds than the closing price →
    positive long-run expectation (assuming Pinnacle closing line is
    the best probability estimate).

    Parameters
    ----------
    picks_df:
        Clean picks DataFrame.  Requires ``clv`` and ``result_over25``.

    Returns
    -------
    dict with keys:
        mean_clv, median_clv, std_clv, positive_pct, negative_pct,
        correlation_with_outcome (point-biserial), n_with_clv,
        clv_edge_estimate (mean_clv / 100 as fractional edge)
    """
    df = picks_df.dropna(subset=["clv"]).copy()
    df = df[_resolved_mask(df)]

    if df.empty:
        logger.warning("clv_analysis: no settled picks with CLV data")
        return {
            "mean_clv": float("nan"),
            "median_clv": float("nan"),
            "std_clv": float("nan"),
            "positive_pct": float("nan"),
            "negative_pct": float("nan"),
            "correlation_with_outcome": float("nan"),
            "n_with_clv": 0,
            "clv_edge_estimate": float("nan"),
        }

    clv = df["clv"].astype(float)
    outcomes = (df["result_over25"] == "WIN").astype(float)

    corr = float(clv.corr(outcomes)) if len(clv) > 1 else float("nan")

    return {
        "mean_clv": round(float(clv.mean()), 4),
        "median_clv": round(float(clv.median()), 4),
        "std_clv": round(float(clv.std()), 4),
        "positive_pct": round(float((clv > 0).mean() * 100), 2),
        "negative_pct": round(float((clv < 0).mean() * 100), 2),
        "correlation_with_outcome": round(corr, 4),
        "n_with_clv": int(len(clv)),
        "clv_edge_estimate": round(float(clv.mean()) / 100.0, 6),
    }


# ---------------------------------------------------------------------------
# ROI by filter
# ---------------------------------------------------------------------------


def roi_by_filter(
    picks_df: pd.DataFrame,
    filter_col: str,
    filter_vals: Optional[list] = None,
) -> pd.DataFrame:
    """
    Compute ROI statistics broken down by a categorical column.

    Typical use cases:
    - ``filter_col='movimento'``,  ``filter_vals=['SHORTENING', 'DRIFTING']``
    - ``filter_col='sharp_label'``, ``filter_vals=['STEAM', 'SHARP', 'WATCH']``
    - ``filter_col='score_band'``
    - ``filter_col='odds_band'``

    Parameters
    ----------
    picks_df:
        Clean picks DataFrame.
    filter_col:
        Column to group by.
    filter_vals:
        Subset of values to include.  If ``None``, all unique values are used.

    Returns
    -------
    pd.DataFrame
        Columns: filter_col (index), n_bets, n_win, n_loss, hit_rate,
        total_profit, roi_pct, avg_odds, avg_clv.
    """
    df = picks_df[_resolved_mask(picks_df)].copy()
    if df.empty or filter_col not in df.columns:
        logger.warning("roi_by_filter: empty DataFrame or missing column '%s'", filter_col)
        return pd.DataFrame(
            columns=[filter_col, "n_bets", "n_win", "n_loss", "hit_rate",
                     "total_profit", "roi_pct", "avg_odds", "avg_clv"]
        )

    df["_pl"] = _returns(df)
    df["_is_win"] = (df["result_over25"] == "WIN").astype(int)

    if filter_vals is not None:
        df = df[df[filter_col].isin(filter_vals)]

    df[filter_col] = df[filter_col].fillna("(blank)")

    rows = []
    for val, grp in df.groupby(filter_col, sort=True):
        n = len(grp)
        n_win = int(grp["_is_win"].sum())
        n_loss = n - n_win
        total_profit = float(grp["_pl"].sum())
        roi_v = float(total_profit / n * 100.0) if n > 0 else 0.0
        avg_odds = float(
            pd.to_numeric(grp["odds_over"], errors="coerce").mean()
        ) if "odds_over" in grp.columns else float("nan")
        avg_clv = float(
            pd.to_numeric(grp["clv"], errors="coerce").mean()
        ) if "clv" in grp.columns else float("nan")

        rows.append(
            {
                filter_col: val,
                "n_bets": n,
                "n_win": n_win,
                "n_loss": n_loss,
                "hit_rate": round(n_win / n, 4) if n > 0 else float("nan"),
                "total_profit": round(total_profit, 4),
                "roi_pct": round(roi_v, 4),
                "avg_odds": round(avg_odds, 4),
                "avg_clv": round(avg_clv, 4),
            }
        )

    return pd.DataFrame(rows).set_index(filter_col)


# ---------------------------------------------------------------------------
# Rolling ROI
# ---------------------------------------------------------------------------


def rolling_roi(picks_df: pd.DataFrame, window: int = 10) -> pd.Series:
    """
    Rolling window ROI for trend detection.

    Useful for spotting regime changes: improving or deteriorating edge
    over time.

    Parameters
    ----------
    picks_df:
        Clean picks DataFrame, sorted chronologically.
    window:
        Number of bets per rolling window.

    Returns
    -------
    pd.Series
        Rolling ROI (%) indexed like the input DataFrame.
        NaN for the first ``window-1`` positions.
    """
    df = picks_df[_resolved_mask(picks_df)].copy()
    if "saved_at" in df.columns:
        df = df.sort_values("saved_at")

    pl = _returns(df)

    def _window_roi(arr: np.ndarray) -> float:
        if len(arr) == 0:
            return float("nan")
        return float(arr.sum() / len(arr) * 100.0)

    rolling = pl.rolling(window=window, min_periods=window).apply(
        _window_roi, raw=True
    )
    return rolling.rename("rolling_roi_pct")


# ---------------------------------------------------------------------------
# Kelly vs flat staking comparison
# ---------------------------------------------------------------------------


def kelly_roi_comparison(picks_df: pd.DataFrame) -> dict:
    """
    Compare flat staking vs Kelly criterion staking ROI.

    Kelly fraction for each bet: f = (prob * odds - 1) / (odds - 1)
    where prob is ``prob_over25 / 100`` (model probability).

    Fractional Kelly (half-Kelly) is also included for practical risk
    management.

    Parameters
    ----------
    picks_df:
        Clean picks DataFrame.  Requires ``prob_over25``, ``odds_over``,
        ``result_over25``.

    Returns
    -------
    dict with keys:
        flat_roi_pct, kelly_roi_pct, half_kelly_roi_pct,
        avg_kelly_fraction, n_bets, n_positive_ev, n_negative_ev
    """
    df = picks_df[_resolved_mask(picks_df)].copy()
    df = df.dropna(subset=["prob_over25", "odds_over"])
    if df.empty:
        return {
            "flat_roi_pct": float("nan"),
            "kelly_roi_pct": float("nan"),
            "half_kelly_roi_pct": float("nan"),
            "avg_kelly_fraction": float("nan"),
            "n_bets": 0,
            "n_positive_ev": 0,
            "n_negative_ev": 0,
        }

    prob = pd.to_numeric(df["prob_over25"], errors="coerce") / 100.0
    odds = pd.to_numeric(df["odds_over"], errors="coerce")

    # Kelly fraction: f = (p*b - q) / b  where b = odds-1, q = 1-p
    b = odds - 1.0
    kelly = (prob * b - (1.0 - prob)) / b
    kelly = kelly.clip(lower=0.0)  # never bet negative Kelly
    half_kelly = kelly / 2.0

    is_win = (df["result_over25"] == "WIN").astype(float)

    # Flat: +1 on win (odds-1), -1 on loss, total staked = n
    flat_pl = np.where(is_win == 1, (odds - 1.0), -1.0)
    flat_total = len(df)

    # Kelly: stake = kelly fraction, profit = fraction * (odds-1) on win
    kelly_arr = kelly.values
    kelly_pl = np.where(is_win == 1, kelly_arr * (odds.values - 1.0), -kelly_arr)
    kelly_total = kelly_arr.sum()

    # Half-Kelly
    hk_arr = half_kelly.values
    hk_pl = np.where(is_win == 1, hk_arr * (odds.values - 1.0), -hk_arr)
    hk_total = hk_arr.sum()

    flat_roi = float(flat_pl.sum() / flat_total * 100.0) if flat_total > 0 else float("nan")
    kelly_roi_v = float(kelly_pl.sum() / kelly_total * 100.0) if kelly_total > 0 else float("nan")
    hk_roi_v = float(hk_pl.sum() / hk_total * 100.0) if hk_total > 0 else float("nan")

    ev = prob * (odds - 1.0) - (1.0 - prob)  # expected value per unit
    n_pos = int((ev > 0).sum())
    n_neg = int((ev <= 0).sum())

    return {
        "flat_roi_pct": round(flat_roi, 4),
        "kelly_roi_pct": round(kelly_roi_v, 4),
        "half_kelly_roi_pct": round(hk_roi_v, 4),
        "avg_kelly_fraction": round(float(kelly.mean()), 6),
        "n_bets": int(len(df)),
        "n_positive_ev": n_pos,
        "n_negative_ev": n_neg,
    }


# ---------------------------------------------------------------------------
# Sharpe ratio
# ---------------------------------------------------------------------------


def sharpe_ratio(returns: np.ndarray, rf: float = 0.0) -> float:
    """
    Sharpe ratio of a betting returns series.

    Sharpe = (mean(R) - rf) / std(R)

    Parameters
    ----------
    returns:
        Per-bet P&L array (e.g. +0.9 on a WIN at 1.90, -1 on LOSS).
    rf:
        Risk-free rate per bet (default 0.0).

    Returns
    -------
    float
        Annualised (sqrt-N scaled) Sharpe ratio.  Returns NaN if std = 0.
    """
    returns = np.asarray(returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    if len(returns) < 2:
        return float("nan")

    mu = float(np.mean(returns)) - rf
    sigma = float(np.std(returns, ddof=1))
    if sigma == 0.0:
        return float("nan")

    # Scale: assume ~1000 bets/year (arbitrary; caller can rescale)
    return float(mu / sigma * np.sqrt(len(returns)))
