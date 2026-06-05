"""
Report generation for the Over 2.5 goals backtesting system.

Functions
---------
generate_text_report       - formatted single-strategy text report
generate_comparison_report - markdown table comparing all strategies
generate_json_report       - JSON-serialisable dict for a single result
save_report                - write report string to a file
print_calibration_report   - probability calibration by band
print_feature_importance   - logistic regression feature coefficients
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.engine import BacktestResult


# ---------------------------------------------------------------------------
# Single-strategy text report
# ---------------------------------------------------------------------------


def generate_text_report(result: BacktestResult, strategy_name: str) -> str:
    """
    Return a formatted text report for a single BacktestResult.

    Args:
        result:        BacktestResult produced by Backtester.run().
        strategy_name: Human-readable strategy label.

    Returns:
        Multi-line string suitable for printing or saving as .txt.
    """
    sep = "=" * 60
    thin = "-" * 60

    profit = result.final_bankroll - (
        result.final_bankroll - sum(t.profit for t in result.trades)
    )
    total_profit = sum(t.profit for t in result.trades)
    total_staked = sum(t.stake for t in result.trades)

    lines = [
        sep,
        f"  BACKTEST REPORT — {strategy_name.upper()}",
        sep,
        "",
        "  SUMMARY",
        thin,
        f"  Bets placed       : {result.n_bets}",
        f"  Wins              : {result.n_wins}",
        f"  Win rate          : {result.win_rate:.1f}%",
        f"  Total staked      : {total_staked:.2f}",
        f"  Total profit      : {total_profit:+.2f}",
        f"  ROI               : {result.roi:+.2f}%",
        f"  Yield             : {result.yield_pct:+.2f}%",
        f"  Final bankroll    : {result.final_bankroll:.2f}",
        "",
        "  RISK METRICS",
        thin,
        f"  Max drawdown      : {result.max_drawdown:.2f}",
        f"  Sharpe ratio      : {result.sharpe:.4f}",
        "",
        "  AVERAGE METRICS (per bet)",
        thin,
        f"  Avg odds          : {result.avg_odds:.3f}",
        f"  Avg probability   : {result.avg_prob:.1f}%",
        f"  Avg CLV           : {result.avg_clv:+.3f}%",
        "",
    ]

    if result.trades:
        lines += [
            "  TRADE LOG (last 10 bets)",
            thin,
            f"  {'Date':<12}  {'Home':<20}  {'Away':<20}  {'Odds':>5}  "
            f"{'Stake':>6}  {'P&L':>7}  {'Bankroll':>9}",
            "  " + "-" * 87,
        ]
        for t in result.trades[-10:]:
            date_str = str(t.date)[:10]
            home = t.home[:19]
            away = t.away[:19]
            lines.append(
                f"  {date_str:<12}  {home:<20}  {away:<20}  "
                f"{t.odds:>5.2f}  {t.stake:>6.2f}  {t.profit:>+7.2f}  "
                f"{t.bankroll_after:>9.2f}"
            )
        lines.append("")

    lines += [sep, ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Multi-strategy comparison (Markdown table)
# ---------------------------------------------------------------------------


def generate_comparison_report(comparison_df: pd.DataFrame) -> str:
    """
    Render a Markdown table comparing all strategies.

    Args:
        comparison_df: DataFrame returned by strategies.compare_strategies().

    Returns:
        Markdown-formatted string.
    """
    if comparison_df.empty:
        return "No strategies to compare.\n"

    display_cols = [
        "strategy",
        "n_bets",
        "win_rate_pct",
        "roi_pct",
        "yield_pct",
        "total_profit",
        "max_drawdown",
        "sharpe",
        "avg_odds",
        "avg_clv",
    ]
    # Keep only columns that actually exist
    cols = [c for c in display_cols if c in comparison_df.columns]
    df = comparison_df[cols].copy()

    # Column header aliases for readability
    rename = {
        "strategy": "Strategy",
        "n_bets": "Bets",
        "win_rate_pct": "WR%",
        "roi_pct": "ROI%",
        "yield_pct": "Yield%",
        "total_profit": "Profit",
        "max_drawdown": "MaxDD",
        "sharpe": "Sharpe",
        "avg_odds": "AvgOdds",
        "avg_clv": "AvgCLV",
    }
    df = df.rename(columns=rename)

    lines = ["## Strategy Comparison (sorted by Sharpe)\n"]

    # Build header
    headers = list(df.columns)
    col_widths = [
        max(len(str(h)), df[h].astype(str).map(len).max())
        for h in headers
    ]

    header_row = " | ".join(f"{h:<{col_widths[i]}}" for i, h in enumerate(headers))
    sep_row = " | ".join("-" * w for w in col_widths)
    lines.append("| " + header_row + " |")
    lines.append("| " + sep_row + " |")

    for _, row in df.iterrows():
        cells = " | ".join(
            f"{str(row[h]):<{col_widths[i]}}" for i, h in enumerate(headers)
        )
        lines.append("| " + cells + " |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------


def generate_json_report(result: BacktestResult) -> dict:
    """
    Return a JSON-serialisable dict for a BacktestResult.

    The `trades` list is condensed to the essential fields.

    Args:
        result: BacktestResult produced by Backtester.run().

    Returns:
        dict suitable for json.dumps().
    """
    trades_list = [
        {
            "date": t.date,
            "home": t.home,
            "away": t.away,
            "odds": round(t.odds, 3),
            "stake": round(t.stake, 2),
            "profit": round(t.profit, 2),
            "bankroll_after": round(t.bankroll_after, 2),
            "movement": t.movement,
            "sharp_label": t.sharp_label,
            "score": t.score,
            "prob": round(t.prob, 2),
        }
        for t in result.trades
    ]

    return {
        "summary": {
            "n_bets": result.n_bets,
            "n_wins": result.n_wins,
            "win_rate_pct": round(result.win_rate, 2),
            "roi_pct": round(result.roi, 2),
            "yield_pct": round(result.yield_pct, 2),
            "final_bankroll": round(result.final_bankroll, 2),
            "total_profit": round(
                sum(t.profit for t in result.trades), 2
            ),
            "max_drawdown": round(result.max_drawdown, 2),
            "sharpe": round(result.sharpe, 4),
            "avg_odds": round(result.avg_odds, 3),
            "avg_prob": round(result.avg_prob, 2),
            "avg_clv": round(result.avg_clv, 3),
        },
        "profit_balance": [round(p, 2) for p in result.profit_balance],
        "trades": trades_list,
    }


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


def save_report(report: str, path: Path, fmt: str = "txt") -> None:
    """
    Write a report string to disk.

    Args:
        report: Text or Markdown content to persist.
        path:   Destination file path (including filename).
        fmt:    Format hint — 'txt', 'md', or 'json'.  Used only if
                ``path`` has no suffix; the actual content is not
                transformed by this function.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.suffix:
        suffix_map = {"txt": ".txt", "md": ".md", "json": ".json", "both": ".txt"}
        path = path.with_suffix(suffix_map.get(fmt, ".txt"))

    path.write_text(report, encoding="utf-8")


# ---------------------------------------------------------------------------
# Probability calibration analysis
# ---------------------------------------------------------------------------


def print_calibration_report(picks_df: pd.DataFrame) -> None:
    """
    Print a probability calibration analysis broken down by probability band.

    Bands: 50–60%, 60–70%, 70–80%, 80%+

    Columns used:
        prob_over25   - system probability estimate (%)
        result_over25 - WIN / LOSS outcome

    Output columns:
        Band | N | Avg Prob | Actual WR | Over-confidence gap
    """
    df = picks_df.copy()
    df["prob_over25"] = pd.to_numeric(df["prob_over25"], errors="coerce")
    df = df.dropna(subset=["prob_over25", "result_over25"])
    df["won"] = df["result_over25"].str.upper() == "WIN"

    bands = [
        ("50–60%", 50.0, 60.0),
        ("60–70%", 60.0, 70.0),
        ("70–80%", 70.0, 80.0),
        ("80%+",   80.0, 101.0),
    ]

    header = f"{'Band':<8}  {'N':>4}  {'Avg Prob':>9}  {'Actual WR':>10}  {'Gap (over-conf)':>16}"
    print("\n" + "=" * 56)
    print("  PROBABILITY CALIBRATION REPORT")
    print("=" * 56)
    print(f"  {header}")
    print("  " + "-" * 54)

    for label, lo, hi in bands:
        mask = (df["prob_over25"] >= lo) & (df["prob_over25"] < hi)
        sub = df[mask]
        n = len(sub)
        if n == 0:
            print(f"  {label:<8}  {n:>4}  {'—':>9}  {'—':>10}  {'—':>16}")
            continue
        avg_prob = sub["prob_over25"].mean()
        actual_wr = sub["won"].mean() * 100.0
        gap = avg_prob - actual_wr  # positive = overconfident
        gap_str = f"{gap:+.1f}%"
        print(
            f"  {label:<8}  {n:>4}  {avg_prob:>8.1f}%  {actual_wr:>9.1f}%  {gap_str:>16}"
        )

    total_n = len(df)
    total_wr = df["won"].mean() * 100.0
    total_avg_prob = df["prob_over25"].mean()
    total_gap = total_avg_prob - total_wr
    print("  " + "-" * 54)
    print(
        f"  {'TOTAL':<8}  {total_n:>4}  {total_avg_prob:>8.1f}%  "
        f"{total_wr:>9.1f}%  {total_gap:+16.1f}%"
    )
    print("=" * 56 + "\n")


# ---------------------------------------------------------------------------
# Feature importance via logistic regression
# ---------------------------------------------------------------------------


def print_feature_importance(picks_df: pd.DataFrame) -> None:
    """
    Fit a logistic regression on the five core features and print
    coefficients sorted by absolute value (largest = most predictive).

    Features: prob_over25, xg_total, btts_prob, score_sistema, div
    Target:   result_over25 (WIN=1, LOSS=0)

    Requires scikit-learn.  Falls back to a descriptive message if not
    installed.
    """
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        print(
            "\n[feature_importance] scikit-learn not installed. "
            "Run: pip install scikit-learn\n"
        )
        return

    feature_cols = ["prob_over25", "xg_total", "btts_prob", "score_sistema", "div"]
    target_col = "result_over25"

    df = picks_df.copy()
    for col in feature_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=feature_cols + [target_col])

    if len(df) < 5:
        print("\n[feature_importance] Insufficient data (need ≥ 5 rows).\n")
        return

    X = df[feature_cols].values
    y = (df[target_col].str.upper() == "WIN").astype(int).values

    # Standardise so coefficients are comparable
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        max_iter=1000, solver="lbfgs", random_state=42, C=1.0
    )
    model.fit(X_scaled, y)

    coefs = model.coef_[0]
    pairs = sorted(
        zip(feature_cols, coefs), key=lambda x: abs(x[1]), reverse=True
    )

    print("\n" + "=" * 50)
    print("  LOGISTIC REGRESSION FEATURE IMPORTANCE")
    print(f"  (n={len(df)}, standardised coefficients)")
    print("=" * 50)
    print(f"  {'Feature':<16}  {'Coefficient':>12}  {'Direction'}")
    print("  " + "-" * 48)
    for feat, coef in pairs:
        direction = "↑ helps" if coef > 0 else "↓ hurts"
        print(f"  {feat:<16}  {coef:>+12.4f}  {direction}")
    print("=" * 50 + "\n")

    # Intercept
    print(f"  Intercept: {model.intercept_[0]:+.4f}")
    try:
        from sklearn.metrics import accuracy_score

        acc = accuracy_score(y, model.predict(X_scaled))
        print(f"  Train accuracy: {acc:.1%}")
    except Exception:
        pass
    print()
