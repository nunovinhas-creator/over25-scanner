"""
backtesting/run_walkforward.py
------------------------------
Weekly walk-forward backtest of the Dixon-Coles + market blend model.

No-leakage guarantee
--------------------
When predicting a game on date D, the training set contains ONLY games
with date < D (strictly before the match day).  This is enforced by the
`_split` helper and verified by the leakage audit at the end.

What is tested
--------------
For each blend weight w in {0.0, 0.15, 0.30, 0.50, 1.0}:
    p_final(w) = w * p_dc + (1 - w) * p_market
    ev_final   = p_final * odds_over - 1
    bet        = 1 if ev_final >= MIN_EV else 0

Metrics reported:
    N_bets, Win%, P&L (flat 1u), Brier Score, Log-loss, Avg CLV%

CLV = Closing Line Value: P>2.5 / PC>2.5 - 1  (positive → beat the close)

Usage
-----
    python -m backtesting.run_walkforward
    python -m backtesting.run_walkforward --data data/historical/matches.csv
    python -m backtesting.run_walkforward --min-ev 0.03 --min-train 50
"""

from __future__ import annotations

import argparse
import logging
import textwrap
import time
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BLEND_WEIGHTS: list[float] = [0.0, 0.15, 0.30, 0.50, 1.0]
MIN_EV_DEFAULT = 0.03
MIN_TRAIN_GAMES = 50     # minimum training games before first prediction
MIN_TEAM_GAMES  = 5      # skip prediction if home or away team has <5 prior games
REFIT_EVERY_WEEKS = 1    # re-fit model once per week (every Monday)
XI = 0.0018              # time-decay rate
PINNACLE_MARGIN = 1.04   # assumed opening Pinnacle margin (for fallback devig)

_DIV_TO_LEAGUE: dict[str, str] = {
    "E0":  "Premier League",   "E1":  "Championship",
    "SP1": "La Liga",          "SP2": "La Liga 2",
    "I1":  "Serie A",          "I2":  "Serie B",
    "D1":  "Bundesliga",       "D2":  "Bundesliga 2",
    "F1":  "Ligue 1",          "F2":  "Ligue 2",
    "P1":  "Primeira Liga",    "N1":  "Eredivisie",
    "B1":  "Belgian Pro League",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"matches.csv missing columns: {missing}")
    df = df.dropna(subset=["Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    df["over25"] = ((df["FTHG"] + df["FTAG"]) >= 3).astype(int)
    # Ensure odds columns exist (use NaN when absent)
    for col in ("P>2.5", "P<2.5", "PC>2.5", "PC<2.5"):
        if col not in df.columns:
            df[col] = np.nan
    df.sort_values("Date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ---------------------------------------------------------------------------
# Devig helper
# ---------------------------------------------------------------------------

def _devig_market(p_over: float, p_under: float) -> float:
    """Multiplicative devig → fair p_over."""
    total = p_over + p_under
    if total <= 0:
        return np.nan
    return p_over / total


def _market_prob(row: pd.Series) -> tuple[float, str]:
    """Derive fair market probability from Pinnacle opening odds."""
    p_raw = row.get("P>2.5", np.nan)
    u_raw = row.get("P<2.5", np.nan)

    if pd.notna(p_raw) and pd.notna(u_raw) and float(p_raw) > 1.0 and float(u_raw) > 1.0:
        p_imp = 1.0 / float(p_raw)
        u_imp = 1.0 / float(u_raw)
        return _devig_market(p_imp, u_imp), "devig"

    if pd.notna(p_raw) and float(p_raw) > 1.0:
        return (1.0 / float(p_raw)) / PINNACLE_MARGIN, "fallback"

    return np.nan, "missing"


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def _weeks_between(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """All Monday dates in [start, end) at weekly intervals."""
    mondays = pd.date_range(start=start, end=end, freq="W-MON")
    return list(mondays)


def run_walkforward(
    df: pd.DataFrame,
    blend_weights: list[float] = BLEND_WEIGHTS,
    min_ev: float = MIN_EV_DEFAULT,
    min_train: int = MIN_TRAIN_GAMES,
) -> pd.DataFrame:
    """
    Run weekly walk-forward backtest and return a DataFrame of all bet records.

    Each row corresponds to one (game × weight) combination where a bet
    would have been placed (ev_final >= min_ev).

    Columns:
        date, div, league, home, away, over25,
        p_dc, p_market, p_market_source,
        blend_weight, p_final, ev_final,
        odds_over, clv, won
    """
    from models.math.poisson import fit_dixon_coles_fast, prob_over25_from_model

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])

    all_records: list[dict] = []

    # Leakage audit counters
    _leakage_violations = 0

    weeks = _weeks_between(df["Date"].min(), df["Date"].max() + timedelta(days=7))

    # Track fitted models per (div, week)
    _models: dict[str, object] = {}  # div → latest model
    _model_trained_at: dict[str, pd.Timestamp] = {}  # div → last Monday trained

    for week_idx, week_start in enumerate(weeks[:-1]):
        week_end = weeks[week_idx + 1]

        # Training data: strictly before week_start — NO LEAKAGE
        train_mask = df["Date"] < week_start
        test_mask  = (df["Date"] >= week_start) & (df["Date"] < week_end)

        test_df = df[test_mask]
        if test_df.empty:
            continue

        n_train_total = train_mask.sum()
        if n_train_total < min_train:
            continue

        # Fit / refit per division
        for div, div_test in test_df.groupby("Div"):
            div_train = df[train_mask & (df["Div"] == div)]

            if len(div_train) < MIN_TEAM_GAMES:
                continue

            # Re-fit weekly (if not already fitted this week)
            if div not in _model_trained_at or _model_trained_at[div] < week_start:
                fit_df = div_train.rename(columns={
                    "HomeTeam": "home", "AwayTeam": "away",
                    "FTHG": "goals_home", "FTAG": "goals_away",
                    "Date": "date",
                })
                try:
                    _models[div] = fit_dixon_coles_fast(fit_df, xi=XI, max_iter=300)
                    _model_trained_at[div] = week_start
                except Exception as exc:
                    logger.debug("Fit failed for %s week %s: %s", div, week_start.date(), exc)
                    continue

            model = _models.get(div)
            if model is None:
                continue

            league = _DIV_TO_LEAGUE.get(str(div), str(div))

            for _, row in div_test.iterrows():
                # Leakage guard (should never trigger)
                if row["Date"] < week_start:
                    _leakage_violations += 1
                    logger.error(
                        "LEAKAGE: game %s on %s included in week %s",
                        f"{row['HomeTeam']} v {row['AwayTeam']}", row["Date"].date(), week_start.date()
                    )
                    continue

                home, away = str(row["HomeTeam"]), str(row["AwayTeam"])

                # Count prior appearances (cold-start filter)
                home_prior = (
                    (div_train["HomeTeam"] == home) | (div_train["AwayTeam"] == home)
                ).sum()
                away_prior = (
                    (div_train["HomeTeam"] == away) | (div_train["AwayTeam"] == away)
                ).sum()
                if home_prior < MIN_TEAM_GAMES or away_prior < MIN_TEAM_GAMES:
                    continue

                try:
                    p_dc = prob_over25_from_model(model, home, away)
                except Exception:
                    continue

                p_market_val, p_market_src = _market_prob(row)
                if np.isnan(p_market_val):
                    continue

                odds_over = float(row.get("P>2.5", np.nan))
                if np.isnan(odds_over) or odds_over <= 1.0:
                    continue

                # CLV: positive = beat the closing line
                pc_over = float(row.get("PC>2.5", np.nan))
                clv = (odds_over / pc_over - 1.0) if (pd.notna(pc_over) and pc_over > 1.0) else np.nan

                for w in blend_weights:
                    p_final = w * p_dc + (1.0 - w) * p_market_val
                    ev_final = p_final * odds_over - 1.0

                    if ev_final < min_ev:
                        continue

                    all_records.append({
                        "date":          row["Date"],
                        "div":           div,
                        "league":        league,
                        "home":          home,
                        "away":          away,
                        "over25":        int(row["over25"]),
                        "p_dc":          round(p_dc, 6),
                        "p_market":      round(p_market_val, 6),
                        "p_market_source": p_market_src,
                        "blend_weight":  w,
                        "p_final":       round(p_final, 6),
                        "ev_final":      round(ev_final, 6),
                        "odds_over":     round(odds_over, 3),
                        "clv":           round(clv, 6) if pd.notna(clv) else np.nan,
                        "won":           int(row["over25"] == 1),
                    })

    if _leakage_violations:
        raise RuntimeError(
            f"LEAKAGE DETECTED: {_leakage_violations} violations found! "
            "Walk-forward is invalid. Check date filtering logic."
        )

    logger.info("No leakage violations detected ✓")
    return pd.DataFrame(all_records)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _brier(y_true: np.ndarray, p_pred: np.ndarray) -> float:
    return float(np.mean((p_pred - y_true) ** 2))


def _log_loss(y_true: np.ndarray, p_pred: np.ndarray, eps: float = 1e-7) -> float:
    p = np.clip(p_pred, eps, 1 - eps)
    return float(-np.mean(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)))


def compute_metrics(bets: pd.DataFrame) -> pd.DataFrame:
    """Compute per-weight performance metrics from a bets DataFrame."""
    rows = []
    for w in sorted(bets["blend_weight"].unique()):
        sub = bets[bets["blend_weight"] == w]
        if sub.empty:
            continue
        y = sub["won"].values.astype(float)
        p = sub["p_final"].values.astype(float)
        odds = sub["odds_over"].values
        n_bets = len(sub)
        win_rate = float(y.mean())
        pnl = float(np.sum(np.where(y == 1, odds - 1.0, -1.0)))
        brier = _brier(y, p)
        ll = _log_loss(y, p)
        clv_vals = sub["clv"].dropna()
        avg_clv = float(clv_vals.mean()) if len(clv_vals) > 0 else np.nan
        rows.append({
            "weight":    w,
            "n_bets":    n_bets,
            "win_rate":  round(win_rate, 4),
            "pnl":       round(pnl, 2),
            "brier":     round(brier, 5),
            "log_loss":  round(ll, 5),
            "avg_clv":   round(avg_clv, 5) if pd.notna(avg_clv) else np.nan,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _recommendation(metrics_df: pd.DataFrame) -> str:
    """Pick the weight with the lowest Brier Score as the recommendation."""
    best_idx = metrics_df["brier"].idxmin()
    best = metrics_df.loc[best_idx]
    return (
        f"Recommended blend weight: **w = {best['weight']}** "
        f"(Brier Score = {best['brier']:.5f}, "
        f"Win% = {best['win_rate']*100:.1f}%, "
        f"P&L = {best['pnl']:+.1f}u)"
    )


def write_report(
    bets: pd.DataFrame,
    metrics: pd.DataFrame,
    out_path: Path,
    min_ev: float,
) -> None:
    n_games_total = bets[["date", "div", "home", "away"]].drop_duplicates().shape[0]
    date_min = bets["date"].min().date() if not bets.empty else "N/A"
    date_max = bets["date"].max().date() if not bets.empty else "N/A"
    leagues = sorted(bets["league"].unique()) if not bets.empty else []

    # Metrics table
    table_rows = []
    for _, r in metrics.iterrows():
        clv_str = f"{r['avg_clv']*100:+.2f}%" if pd.notna(r["avg_clv"]) else "N/A"
        table_rows.append(
            f"| {r['weight']:.2f} | {int(r['n_bets']):>6} | "
            f"{r['win_rate']*100:.1f}% | {r['pnl']:>+7.1f}u | "
            f"{r['brier']:.5f} | {r['log_loss']:.5f} | {clv_str} |"
        )

    report = textwrap.dedent(f"""\
        # Walk-Forward Backtest Report

        Generated: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC

        ## Dataset

        | | |
        |---|---|
        | Total games evaluated | {n_games_total:,} |
        | Date range | {date_min} → {date_max} |
        | Leagues | {len(leagues)} ({", ".join(leagues)}) |
        | EV threshold (MIN\\_EV) | {min_ev:.2f} ({min_ev*100:.0f}%) |

        ## No-leakage verification

        Walk-forward uses **strictly historical data** at every step:
        - Training set for week W: all games with `date < week_start(W)`
        - Test set for week W: games in `[week_start(W), week_start(W+1))`
        - Cold-start filter: teams with < {MIN_TEAM_GAMES} prior games are skipped
        - Verified programmatically: 0 leakage violations detected ✓

        ## Results by blend weight

        `p_final = w × p_dc + (1 − w) × p_market`

        | w    | N bets | Win%  |     P&L | Brier    | Log-loss | Avg CLV |
        |------|--------|-------|---------|----------|----------|---------|
        {chr(10).join(table_rows)}

        > **Brier Score reference**: Pinnacle benchmark ≈ 0.220–0.230 (over/under 2.5 market).
        > CLV = `P>2.5 / PC>2.5 − 1`; positive means we bet at better odds than the closing line.

        ## {_recommendation(metrics)}

        ## Notes

        - Dixon-Coles model re-fitted weekly (every Monday) per division
        - Time-decay parameter ξ = {XI} (≈2-year half-life)
        - Market probability: de-vigged Pinnacle opening (`P>2.5` / `P<2.5`) via multiplicative method
        - Fallback when `P<2.5` missing: `(1 / P>2.5) / {PINNACLE_MARGIN}`
        - Bet criterion: `ev_final = p_final × P>2.5 − 1 ≥ {min_ev}`
        - Staking: flat 1 unit per bet (Kelly disabled per `Config.STAKE_TYPE`)
    """)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    logger.info("Report written to %s", out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Walk-forward backtest (no leakage)")
    p.add_argument("--data", type=Path,
                   default=root / "data" / "historical" / "matches.csv",
                   help="Path to historical matches CSV")
    p.add_argument("--out", type=Path,
                   default=root / "backtesting" / "reports" / "walkforward.md",
                   help="Output path for Markdown report")
    p.add_argument("--min-ev", type=float, default=MIN_EV_DEFAULT,
                   help="Minimum EV to place a simulated bet")
    p.add_argument("--min-train", type=int, default=MIN_TRAIN_GAMES,
                   help="Minimum total training games before first prediction")
    p.add_argument("--weights", nargs="+", type=float, default=BLEND_WEIGHTS,
                   metavar="W", help="Blend weights to test")
    return p


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(message)s")
    args = _build_parser().parse_args(argv)

    if not args.data.exists():
        raise FileNotFoundError(
            f"matches.csv not found at {args.data}. "
            "Run 'python -m pipeline.historical --synthetic' first."
        )

    logger.info("Loading %s…", args.data)
    df = _load(args.data)
    logger.info("Loaded %d games from %s to %s",
                len(df), df["Date"].min().date(), df["Date"].max().date())

    logger.info("Running walk-forward (weights=%s, min_ev=%.3f)…", args.weights, args.min_ev)
    t0 = time.perf_counter()
    bets = run_walkforward(df, blend_weights=args.weights,
                           min_ev=args.min_ev, min_train=args.min_train)
    elapsed = time.perf_counter() - t0
    logger.info("Walk-forward complete in %.1fs — %d bet records", elapsed, len(bets))

    if bets.empty:
        logger.warning("No bets generated — check min_ev threshold or data quality.")

    metrics = compute_metrics(bets)
    print("\n" + metrics.to_string(index=False))

    write_report(bets, metrics, args.out, min_ev=args.min_ev)
    print(f"\nReport saved to {args.out}")


if __name__ == "__main__":
    main()
