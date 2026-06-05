"""
Deterministic walk-forward backtesting engine for Over 2.5 goals scanner.

Usage:
    from backtesting.engine import Backtester, BacktestConfig
    config = BacktestConfig(min_score=55, require_shortening=True)
    result = Backtester().run(picks_df, config)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    """Represents a single executed bet."""

    date: str
    home: str
    away: str
    odds: float
    stake: float
    profit: float
    bankroll_after: float
    movement: str
    sharp_label: str
    score: int
    prob: float


@dataclass
class BacktestConfig:
    """Configuration parameters for a single backtest run."""

    initial_bankroll: float = 1000.0
    stake_type: Literal["flat", "kelly", "half_kelly"] = "flat"
    flat_stake: float = 10.0
    kelly_fraction: float = 0.5
    min_odds: float = 1.3
    max_odds: float = 3.5
    min_score: int = 45
    require_shortening: bool = False
    require_sharp: bool = False
    min_prob: float = 0.0

    def summary(self) -> str:
        parts = [
            f"stake={self.stake_type}",
            f"min_score={self.min_score}",
            f"min_odds={self.min_odds}",
            f"max_odds={self.max_odds}",
        ]
        if self.require_shortening:
            parts.append("shortening=True")
        if self.require_sharp:
            parts.append("sharp=True")
        if self.min_prob > 0:
            parts.append(f"min_prob={self.min_prob}")
        return ", ".join(parts)


@dataclass
class BacktestResult:
    """Complete result of a backtest run."""

    trades: list[Trade] = field(default_factory=list)
    final_bankroll: float = 0.0
    roi: float = 0.0           # (profit / total_staked) * 100
    yield_pct: float = 0.0     # profit / (n_bets * flat_stake) * 100, same as roi for flat
    win_rate: float = 0.0
    max_drawdown: float = 0.0  # peak-to-trough in absolute £
    sharpe: float = 0.0        # Sharpe on per-bet returns (annualised not applied)
    n_bets: int = 0
    n_wins: int = 0
    avg_odds: float = 0.0
    avg_prob: float = 0.0
    avg_clv: float = 0.0
    profit_balance: list[float] = field(default_factory=list)  # cumulative P&L curve


# ---------------------------------------------------------------------------
# Kelly criterion helper
# ---------------------------------------------------------------------------


def kelly_full(prob: float, odds: float) -> float:
    """
    Full Kelly fraction for a binary bet.

    Args:
        prob:  decimal win probability (0–1).
        odds:  decimal odds (e.g. 2.0).

    Returns:
        Fraction of bankroll to stake (clamped to [0, 1]).
    """
    if odds <= 1.0 or prob <= 0.0 or prob >= 1.0:
        return 0.0
    b = odds - 1.0          # net profit per unit staked
    q = 1.0 - prob
    f = (b * prob - q) / b  # classic Kelly formula: (bp - q) / b
    return max(0.0, min(1.0, f))


# ---------------------------------------------------------------------------
# Core backtester
# ---------------------------------------------------------------------------


class Backtester:
    """
    Walk-forward backtesting engine.

    The engine sorts picks by date, applies config filters sequentially,
    and simulates each qualifying bet using the chosen staking plan.
    """

    # Sharp labels considered "strong signal"
    SHARP_LABELS = {"STEAM", "SHARP"}

    def _filter(self, picks_df: pd.DataFrame, config: BacktestConfig) -> pd.DataFrame:
        """Apply config-level filters to the picks DataFrame."""
        df = picks_df.copy()

        # Coerce numeric columns safely
        for col in ("odds_over", "score_sistema", "prob_over25"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df[df["odds_over"].between(config.min_odds, config.max_odds)]
        df = df[df["score_sistema"] >= config.min_score]

        if config.min_prob > 0.0:
            df = df[df["prob_over25"] >= config.min_prob]

        if config.require_shortening:
            df = df[df["movimento"] == "SHORTENING"]

        if config.require_sharp:
            df = df[df["sharp_label"].isin(self.SHARP_LABELS)]

        return df

    def _compute_stake(
        self, bankroll: float, prob: float, odds: float, config: BacktestConfig
    ) -> float:
        """Return stake in £ for this bet based on staking plan."""
        if config.stake_type == "flat":
            return config.flat_stake

        kf = kelly_full(prob / 100.0, odds)

        if config.stake_type == "kelly":
            return bankroll * kf

        # half_kelly
        return bankroll * config.kelly_fraction * kf

    def _max_drawdown(self, bankroll_curve: list[float]) -> float:
        """Peak-to-trough maximum drawdown (absolute £ value)."""
        if not bankroll_curve:
            return 0.0
        peak = bankroll_curve[0]
        max_dd = 0.0
        for value in bankroll_curve:
            if value > peak:
                peak = value
            dd = peak - value
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _sharpe(self, per_bet_returns: list[float]) -> float:
        """
        Sharpe ratio computed on per-bet returns (profit / stake).

        Uses population std (ddof=1 when n>1) to avoid division by zero.
        Returns 0.0 if fewer than 2 bets.
        """
        if len(per_bet_returns) < 2:
            return 0.0
        arr = np.array(per_bet_returns, dtype=float)
        mean = arr.mean()
        std = arr.std(ddof=1)
        if std == 0.0:
            return 0.0
        return float(mean / std)

    def run(self, picks_df: pd.DataFrame, config: BacktestConfig) -> BacktestResult:
        """
        Execute a single walk-forward backtest.

        Args:
            picks_df:  DataFrame with all picks (must contain at minimum the
                       columns used by the engine).
            config:    BacktestConfig controlling filters and staking.

        Returns:
            BacktestResult with all metrics populated.
        """
        df = self._filter(picks_df, config)

        # Sort chronologically
        if "data" in df.columns:
            df = df.sort_values("data", kind="mergesort").reset_index(drop=True)

        if df.empty:
            return BacktestResult(
                final_bankroll=config.initial_bankroll,
            )

        bankroll = config.initial_bankroll
        trades: list[Trade] = []
        bankroll_curve: list[float] = [bankroll]
        per_bet_returns: list[float] = []
        cumulative_profit: list[float] = []
        total_staked = 0.0
        cum_profit = 0.0

        # Pre-coerce CLV to float (may be string or NaN)
        clv_series = pd.to_numeric(picks_df.get("clv", pd.Series(dtype=float)), errors="coerce")
        clv_map: dict[str, float] = {}
        if "id" in picks_df.columns:
            clv_map = dict(zip(picks_df["id"].astype(str), clv_series))

        for _, row in df.iterrows():
            prob = float(row["prob_over25"])
            odds = float(row["odds_over"])
            outcome = str(row.get("result_over25", "")).upper()

            stake = self._compute_stake(bankroll, prob, odds, config)
            stake = max(0.01, stake)  # floor to avoid zero/negative stakes

            won = outcome == "WIN"
            profit = stake * (odds - 1.0) if won else -stake
            bankroll += profit
            total_staked += stake
            cum_profit += profit

            per_bet_returns.append(profit / stake)
            bankroll_curve.append(bankroll)
            cumulative_profit.append(cum_profit)

            row_id = str(row.get("id", ""))
            clv_val = clv_map.get(row_id, float("nan"))

            trades.append(
                Trade(
                    date=str(row.get("data", "")),
                    home=str(row.get("casa", "")),
                    away=str(row.get("fora", "")),
                    odds=odds,
                    stake=stake,
                    profit=profit,
                    bankroll_after=bankroll,
                    movement=str(row.get("movimento", "")),
                    sharp_label=str(row.get("sharp_label", "")),
                    score=int(row["score_sistema"]),
                    prob=prob,
                )
            )

        n_bets = len(trades)
        n_wins = sum(1 for t in trades if t.profit > 0)
        total_profit = bankroll - config.initial_bankroll
        roi = (total_profit / total_staked * 100.0) if total_staked > 0 else 0.0

        # Yield: profit / (n_bets * flat_stake) — identical to ROI for flat staking
        yield_pct = (
            (total_profit / (n_bets * config.flat_stake) * 100.0)
            if n_bets > 0
            else 0.0
        )

        # Average CLV
        clv_vals = [clv_map.get(str(row.get("id", "")), float("nan")) for _, row in df.iterrows()]
        valid_clv = [v for v in clv_vals if not math.isnan(v)]
        avg_clv = float(np.mean(valid_clv)) if valid_clv else 0.0

        return BacktestResult(
            trades=trades,
            final_bankroll=bankroll,
            roi=roi,
            yield_pct=yield_pct,
            win_rate=(n_wins / n_bets * 100.0) if n_bets > 0 else 0.0,
            max_drawdown=self._max_drawdown(bankroll_curve),
            sharpe=self._sharpe(per_bet_returns),
            n_bets=n_bets,
            n_wins=n_wins,
            avg_odds=float(np.mean([t.odds for t in trades])) if trades else 0.0,
            avg_prob=float(np.mean([t.prob for t in trades])) if trades else 0.0,
            avg_clv=avg_clv,
            profit_balance=cumulative_profit,
        )

    def run_grid(
        self, picks_df: pd.DataFrame, configs: list[BacktestConfig]
    ) -> pd.DataFrame:
        """
        Grid search over a list of BacktestConfig objects.

        Args:
            picks_df: Full picks DataFrame.
            configs:  List of BacktestConfig instances to evaluate.

        Returns:
            DataFrame with one row per config, sorted by Sharpe ratio descending.
        """
        rows = []
        for cfg in configs:
            res = self.run(picks_df, cfg)
            rows.append(
                {
                    "config_summary": cfg.summary(),
                    "stake_type": cfg.stake_type,
                    "min_score": cfg.min_score,
                    "require_shortening": cfg.require_shortening,
                    "require_sharp": cfg.require_sharp,
                    "min_prob": cfg.min_prob,
                    "n_bets": res.n_bets,
                    "n_wins": res.n_wins,
                    "win_rate": round(res.win_rate, 2),
                    "roi_pct": round(res.roi, 2),
                    "yield_pct": round(res.yield_pct, 2),
                    "final_bankroll": round(res.final_bankroll, 2),
                    "max_drawdown": round(res.max_drawdown, 2),
                    "sharpe": round(res.sharpe, 4),
                    "avg_odds": round(res.avg_odds, 3),
                    "avg_prob": round(res.avg_prob, 2),
                    "avg_clv": round(res.avg_clv, 3),
                    "total_profit": round(res.final_bankroll - cfg.initial_bankroll, 2),
                }
            )
        grid_df = pd.DataFrame(rows)
        if not grid_df.empty:
            grid_df = grid_df.sort_values("sharpe", ascending=False).reset_index(drop=True)
        return grid_df
