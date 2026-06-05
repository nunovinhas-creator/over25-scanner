"""
CLI entrypoint for the Over 2.5 goals backtesting system.

Usage examples
--------------
# Run all strategies with default settings
python -m backtesting.run_backtest

# Run a single strategy
python -m backtesting.run_backtest --strategy conservative

# Custom bankroll, picks file, and output directory
python -m backtesting.run_backtest --bankroll 2000 --picks data/picks.json --output backtesting/reports/

# Run Monte Carlo simulation for the best strategy
python -m backtesting.run_backtest --strategy shortsharp --monte-carlo

# Output JSON only
python -m backtesting.run_backtest --strategy baseline --format json

Expected output (truncated example)
-------------------------------------
============================================================
  BACKTEST REPORT — CONSERVATIVE
============================================================

  SUMMARY
------------------------------------------------------------
  Bets placed       : 12
  Wins              : 9
  Win rate          : 75.0%
  Total staked      : 87.43
  Total profit      : +32.17
  ROI               : +36.80%
  Yield             : +26.81%
  Final bankroll    : 1032.17

  RISK METRICS
------------------------------------------------------------
  Max drawdown      : 14.22
  Sharpe ratio      : 0.6821

  AVERAGE METRICS (per bet)
------------------------------------------------------------
  Avg odds          : 1.947
  Avg probability   : 74.3%
  Avg CLV           : -1.240%

============================================================

## Strategy Comparison (sorted by Sharpe)

| Strategy        | Bets | WR%   | ROI%   | Yield%  | Profit  | MaxDD   | Sharpe | AvgOdds | AvgCLV |
| --------------- | ---- | ----- | ------ | ------- | ------- | ------- | ------ | ------- | ------ |
| conservative    |   12 | 75.00 | +36.80 | +26.81  |  +32.17 |   14.22 | 0.6821 |   1.947 | -1.240 |
| kelly_sizing    |   15 | 66.67 | +22.10 | +18.50  |  +19.63 |   18.40 | 0.5103 |   1.961 | -0.870 |
...

[MONTE CARLO — shortsharp / 1000 simulations]
  ROI  mean=+8.4%  std=11.2%  p5=-9.8%  p50=+8.1%  p95=+28.3%
  MaxDD mean=42.10  p95=71.50
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


REQUIRED_COLUMNS = {
    "score_sistema",
    "prob_over25",
    "odds_over",
    "movimento",
    "sharp_label",
    "result_over25",
    "clv",
    "xg_total",
    "btts_prob",
    "div",
}

OPTIONAL_COLUMNS = {"id", "data", "casa", "fora", "liga"}


class PicksSchemaError(ValueError):
    """Raised when the picks DataFrame fails validation."""


def validate_picks(df: pd.DataFrame) -> None:
    """
    Validate that the picks DataFrame contains the required columns
    and has at least one row with a known outcome.

    Raises PicksSchemaError on failure.
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise PicksSchemaError(
            f"picks.json is missing required columns: {sorted(missing)}"
        )

    # At least one row must have a result
    outcomes = df["result_over25"].str.upper().unique()
    if not any(o in {"WIN", "LOSS"} for o in outcomes):
        raise PicksSchemaError(
            "No WIN/LOSS outcomes found in result_over25 column."
        )

    # Warn (don't raise) about rows with unparseable odds
    bad_odds = pd.to_numeric(df["odds_over"], errors="coerce").isna().sum()
    if bad_odds > 0:
        print(
            f"[warn] {bad_odds} row(s) have unparseable odds_over — "
            "they will be skipped during backtesting.",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Picks loader
# ---------------------------------------------------------------------------


def load_picks(path: Path) -> pd.DataFrame:
    """Load picks from a JSON file and return a validated DataFrame."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Picks file not found: {path}")

    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    if not isinstance(data, list):
        raise PicksSchemaError("picks.json must be a JSON array.")

    df = pd.DataFrame(data)
    validate_picks(df)
    return df


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _save_result(
    result,
    strategy_name: str,
    output_dir: Path,
    fmt: str,
) -> None:
    """Save text and/or JSON report for a single strategy result."""
    from backtesting.report import (
        generate_json_report,
        generate_text_report,
        save_report,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    slug = strategy_name.replace(" ", "_").lower()

    if fmt in ("txt", "both"):
        txt = generate_text_report(result, strategy_name)
        save_report(txt, output_dir / f"{slug}.txt", fmt="txt")
        print(txt)

    if fmt in ("json", "both"):
        report_dict = generate_json_report(result)
        json_str = json.dumps(report_dict, indent=2, ensure_ascii=False)
        save_report(json_str, output_dir / f"{slug}.json", fmt="json")
        if fmt == "json":
            print(json_str)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """
    Parse CLI arguments and execute the requested backtests.

    Returns exit code (0 = success, 1 = error).
    """
    parser = argparse.ArgumentParser(
        prog="run_backtest",
        description="Over 2.5 goals scanner — walk-forward backtesting CLI",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--strategy",
        default="all",
        help=(
            "Strategy name to run, or 'all' to run every strategy. "
            "Available: baseline, shortening_only, sharp_only, shortsharp, "
            "high_score, high_xg, value_only, kelly_sizing, conservative"
        ),
    )
    parser.add_argument(
        "--picks",
        default="data/picks.json",
        help="Path to picks JSON file.",
    )
    parser.add_argument(
        "--bankroll",
        type=float,
        default=1000.0,
        help="Starting bankroll.",
    )
    parser.add_argument(
        "--output",
        default="backtesting/reports/",
        help="Directory to save report files.",
    )
    parser.add_argument(
        "--monte-carlo",
        action="store_true",
        dest="monte_carlo",
        help="Run 1000 Monte Carlo simulations for the chosen strategy.",
    )
    parser.add_argument(
        "--format",
        choices=["txt", "json", "both"],
        default="both",
        help="Output format for saved reports.",
    )
    parser.add_argument(
        "--calibration",
        action="store_true",
        help="Print probability calibration report.",
    )
    parser.add_argument(
        "--feature-importance",
        action="store_true",
        dest="feature_importance",
        help="Print logistic regression feature importance.",
    )
    parser.add_argument(
        "--mc-sims",
        type=int,
        default=1000,
        dest="mc_sims",
        help="Number of Monte Carlo simulations.",
    )

    args = parser.parse_args()

    # --- Load picks ---
    picks_path = Path(args.picks)
    try:
        picks_df = load_picks(picks_path)
    except (FileNotFoundError, PicksSchemaError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1

    print(f"[info] Loaded {len(picks_df)} picks from {picks_path}", file=sys.stderr)

    # --- Import strategy/engine components ---
    from backtesting.engine import BacktestConfig, Backtester
    from backtesting.report import (
        generate_comparison_report,
        print_calibration_report,
        print_feature_importance,
    )
    from backtesting.strategies import (
        STRATEGIES,
        apply_strategy_filters,
        compare_strategies,
        monte_carlo_strategy,
    )

    output_dir = Path(args.output)
    engine = Backtester()

    # Override initial bankroll in all strategies
    def _with_bankroll(cfg: BacktestConfig) -> BacktestConfig:
        from dataclasses import replace
        return replace(cfg, initial_bankroll=args.bankroll)

    # --- Calibration report ---
    if args.calibration:
        print_calibration_report(picks_df)

    # --- Feature importance ---
    if args.feature_importance:
        print_feature_importance(picks_df)

    # --- Run strategies ---
    if args.strategy == "all":
        print("[info] Running all strategies...\n", file=sys.stderr)

        comparison_df = compare_strategies(picks_df)
        md_report = generate_comparison_report(comparison_df)
        print(md_report)

        # Save comparison markdown
        from backtesting.report import save_report
        save_report(md_report, output_dir / "comparison.md", fmt="md")

        # Save individual reports for each strategy
        for name, cfg in STRATEGIES.items():
            filtered_df = apply_strategy_filters(picks_df, name)
            result = engine.run(filtered_df, _with_bankroll(cfg))
            _save_result(result, name, output_dir, args.format)

        # Monte Carlo for best strategy (highest Sharpe)
        if args.monte_carlo and not comparison_df.empty:
            best_name = comparison_df.iloc[0]["strategy"]
            print(
                f"\n[monte-carlo] Running {args.mc_sims} simulations "
                f"for best strategy: {best_name}",
                file=sys.stderr,
            )
            best_cfg = _with_bankroll(STRATEGIES[best_name])
            best_filtered = apply_strategy_filters(picks_df, best_name)
            mc_result = monte_carlo_strategy(best_filtered, best_cfg, n_sim=args.mc_sims)
            _print_mc_result(mc_result, best_name)

    else:
        strategy_name = args.strategy
        if strategy_name not in STRATEGIES:
            print(
                f"[error] Unknown strategy '{strategy_name}'. "
                f"Available: {sorted(STRATEGIES.keys())}",
                file=sys.stderr,
            )
            return 1

        cfg = _with_bankroll(STRATEGIES[strategy_name])
        filtered_df = apply_strategy_filters(picks_df, strategy_name)
        result = engine.run(filtered_df, cfg)
        _save_result(result, strategy_name, output_dir, args.format)

        if args.monte_carlo:
            print(
                f"\n[monte-carlo] Running {args.mc_sims} simulations "
                f"for strategy: {strategy_name}",
                file=sys.stderr,
            )
            mc_result = monte_carlo_strategy(
                filtered_df, cfg, n_sim=args.mc_sims
            )
            _print_mc_result(mc_result, strategy_name)

    return 0


def _print_mc_result(mc: dict, strategy_name: str) -> None:
    """Print a compact Monte Carlo summary to stdout."""
    if not mc["roi_distribution"]:
        print(f"\n[MONTE CARLO — {strategy_name}] No qualifying bets.\n")
        return

    print(f"\n[MONTE CARLO — {strategy_name} / {mc['n_sim']} simulations]")
    print(
        f"  ROI   mean={mc['roi_mean']:+.1f}%  "
        f"std={mc['roi_std']:.1f}%  "
        f"p5={mc['roi_p5']:+.1f}%  "
        f"p50={mc['roi_p50']:+.1f}%  "
        f"p95={mc['roi_p95']:+.1f}%"
    )
    print(
        f"  MaxDD mean={mc['drawdown_mean']:.2f}  "
        f"p95={mc['drawdown_p95']:.2f}"
    )
    print(f"  Bets per sim: {mc['n_bets_per_sim']}\n")


if __name__ == "__main__":
    sys.exit(main())
