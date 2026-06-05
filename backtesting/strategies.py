"""
Pre-defined strategy configurations and filter presets for the Over 2.5 scanner backtester.

Strategy catalogue
------------------
baseline         - All picks, flat stake, no extra filters
shortening_only  - Only SHORTENING Pinnacle movement
sharp_only       - Only STEAM or SHARP signal
shortsharp       - SHORTENING + STEAM/SHARP
high_score       - System score >= 55
high_xg          - xG total >= 3.0 (applied as a pre-filter on the DataFrame)
value_only       - Expected Value > 0  (prob/100 * odds > 1)
kelly_sizing     - shortsharp + half-Kelly staking
conservative     - shortsharp + high_score + value_only + half-Kelly
"""

from __future__ import annotations

import random
from typing import Any

import numpy as np
import pandas as pd

from backtesting.engine import BacktestConfig, Backtester, BacktestResult


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------

STRATEGIES: dict[str, BacktestConfig] = {
    "baseline": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
    ),
    "shortening_only": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
        require_shortening=True,
    ),
    "sharp_only": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
        require_sharp=True,
    ),
    "shortsharp": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
        require_shortening=True,
        require_sharp=True,
    ),
    "high_score": BacktestConfig(
        min_score=55,
        stake_type="flat",
        flat_stake=10.0,
    ),
    # high_xg: xg >= 3.0 filter is applied via apply_strategy_filters() before run()
    "high_xg": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
    ),
    # value_only: EV > 0 filter is applied via apply_strategy_filters() before run()
    "value_only": BacktestConfig(
        min_score=45,
        stake_type="flat",
        flat_stake=10.0,
    ),
    "kelly_sizing": BacktestConfig(
        min_score=45,
        stake_type="half_kelly",
        kelly_fraction=0.5,
        require_shortening=True,
        require_sharp=True,
    ),
    "conservative": BacktestConfig(
        min_score=55,
        stake_type="half_kelly",
        kelly_fraction=0.5,
        require_shortening=True,
        require_sharp=True,
    ),
}

# Filters that must be applied to the DataFrame *before* passing to Backtester.run()
# (i.e. not expressible purely through BacktestConfig fields).
_DATAFRAME_FILTERS: dict[str, Any] = {
    "high_xg": lambda df: df[pd.to_numeric(df["xg_total"], errors="coerce") >= 3.0],
    "value_only": lambda df: df[
        (
            pd.to_numeric(df["prob_over25"], errors="coerce") / 100.0
            * pd.to_numeric(df["odds_over"], errors="coerce")
        )
        > 1.0
    ],
    "conservative": lambda df: df[
        (
            pd.to_numeric(df["prob_over25"], errors="coerce") / 100.0
            * pd.to_numeric(df["odds_over"], errors="coerce")
        )
        > 1.0
    ],
}


# ---------------------------------------------------------------------------
# Filter application
# ---------------------------------------------------------------------------


def apply_strategy_filters(
    picks_df: pd.DataFrame, strategy_name: str
) -> pd.DataFrame:
    """
    Apply any DataFrame-level pre-filters for the named strategy.

    BacktestConfig-level filters (score, odds, shortening, sharp) are applied
    inside Backtester.run(); this function handles filters that require
    arithmetic on DataFrame columns before the engine sees them.

    Args:
        picks_df:      Full picks DataFrame.
        strategy_name: Key in STRATEGIES dict.

    Returns:
        Filtered copy of picks_df.
    """
    if strategy_name not in STRATEGIES:
        raise ValueError(
            f"Unknown strategy '{strategy_name}'. "
            f"Available: {sorted(STRATEGIES.keys())}"
        )
    fn = _DATAFRAME_FILTERS.get(strategy_name)
    if fn is not None:
        return fn(picks_df.copy()).reset_index(drop=True)
    return picks_df.copy()


# ---------------------------------------------------------------------------
# Strategy comparison
# ---------------------------------------------------------------------------


def compare_strategies(picks_df: pd.DataFrame) -> pd.DataFrame:
    """
    Run all strategies against picks_df and return a comparison DataFrame.

    Returns:
        DataFrame with one row per strategy, sorted by Sharpe ratio descending.
    """
    engine = Backtester()
    rows = []

    for name, config in STRATEGIES.items():
        filtered_df = apply_strategy_filters(picks_df, name)
        result: BacktestResult = engine.run(filtered_df, config)

        rows.append(
            {
                "strategy": name,
                "stake_type": config.stake_type,
                "n_bets": result.n_bets,
                "n_wins": result.n_wins,
                "win_rate_pct": round(result.win_rate, 2),
                "roi_pct": round(result.roi, 2),
                "yield_pct": round(result.yield_pct, 2),
                "final_bankroll": round(result.final_bankroll, 2),
                "total_profit": round(result.final_bankroll - config.initial_bankroll, 2),
                "max_drawdown": round(result.max_drawdown, 2),
                "sharpe": round(result.sharpe, 4),
                "avg_odds": round(result.avg_odds, 3),
                "avg_prob": round(result.avg_prob, 2),
                "avg_clv": round(result.avg_clv, 3),
            }
        )

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("sharpe", ascending=False).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Monte Carlo simulation
# ---------------------------------------------------------------------------


def monte_carlo_strategy(
    picks_df: pd.DataFrame,
    config: BacktestConfig,
    n_sim: int = 1000,
    seed: int | None = 42,
) -> dict:
    """
    Shuffle bet order N times and run the same backtest each time.

    This reveals how much of a strategy's performance is luck-of-ordering
    versus genuine edge (especially relevant for Kelly staking where
    bet order changes bankroll size dynamically).

    Args:
        picks_df:  Picks DataFrame (already pre-filtered if needed).
        config:    BacktestConfig to use for each simulation.
        n_sim:     Number of Monte Carlo iterations (default 1000).
        seed:      Random seed for reproducibility (None = non-deterministic).

    Returns:
        dict with keys:
            roi_distribution    - list of ROI % values (length n_sim)
            drawdown_distribution - list of max drawdown values
            sharpe_distribution - list of Sharpe values
            win_rate_distribution - list of win-rate % values
            roi_mean            - mean ROI across simulations
            roi_std             - std of ROI
            roi_p5              - 5th percentile ROI (downside risk)
            roi_p50             - median ROI
            roi_p95             - 95th percentile ROI (upside)
            drawdown_mean       - mean max drawdown
            drawdown_p95        - 95th percentile max drawdown (worst case)
            n_sim               - number of simulations run
            n_bets_per_sim      - number of bets in each simulation
    """
    engine = Backtester()
    rng = random.Random(seed)

    roi_vals: list[float] = []
    dd_vals: list[float] = []
    sharpe_vals: list[float] = []
    wr_vals: list[float] = []

    # Filter once, shuffle index each iteration
    df_filtered_config = engine._filter(picks_df, config)
    if df_filtered_config.empty:
        return {
            "roi_distribution": [],
            "drawdown_distribution": [],
            "sharpe_distribution": [],
            "win_rate_distribution": [],
            "roi_mean": 0.0,
            "roi_std": 0.0,
            "roi_p5": 0.0,
            "roi_p50": 0.0,
            "roi_p95": 0.0,
            "drawdown_mean": 0.0,
            "drawdown_p95": 0.0,
            "n_sim": 0,
            "n_bets_per_sim": 0,
        }

    indices = list(df_filtered_config.index)

    for _ in range(n_sim):
        shuffled_indices = rng.sample(indices, len(indices))
        shuffled_df = df_filtered_config.loc[shuffled_indices].reset_index(drop=True)

        # Run without re-filtering (already filtered above)
        # Temporarily create a permissive config with same staking
        permissive = BacktestConfig(
            initial_bankroll=config.initial_bankroll,
            stake_type=config.stake_type,
            flat_stake=config.flat_stake,
            kelly_fraction=config.kelly_fraction,
            min_odds=0.0,
            max_odds=999.0,
            min_score=0,
            require_shortening=False,
            require_sharp=False,
            min_prob=0.0,
        )
        result = engine.run(shuffled_df, permissive)

        roi_vals.append(result.roi)
        dd_vals.append(result.max_drawdown)
        sharpe_vals.append(result.sharpe)
        wr_vals.append(result.win_rate)

    roi_arr = np.array(roi_vals)
    dd_arr = np.array(dd_vals)

    return {
        "roi_distribution": roi_vals,
        "drawdown_distribution": dd_vals,
        "sharpe_distribution": sharpe_vals,
        "win_rate_distribution": wr_vals,
        "roi_mean": float(np.mean(roi_arr)),
        "roi_std": float(np.std(roi_arr, ddof=1)),
        "roi_p5": float(np.percentile(roi_arr, 5)),
        "roi_p50": float(np.percentile(roi_arr, 50)),
        "roi_p95": float(np.percentile(roi_arr, 95)),
        "drawdown_mean": float(np.mean(dd_arr)),
        "drawdown_p95": float(np.percentile(dd_arr, 95)),
        "n_sim": n_sim,
        "n_bets_per_sim": len(indices),
    }
