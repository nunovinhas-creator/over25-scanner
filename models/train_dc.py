"""
models/train_dc.py
------------------
Train a Dixon-Coles model per league on the last 2 seasons of historical data
and export team attack/defence ratings to data/dc_ratings.json.

Usage
-----
    python -m models.train_dc
    python -m models.train_dc --data data/historical/matches.csv
    python -m models.train_dc --xi 0.0018 --min-games 30
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Two-season cutoff: use all matches in the 2 most recent seasons per division
N_SEASONS = 2

MIN_GAMES_TO_FIT = 20  # skip a division if fewer historical games are available

# Map Div code → canonical league name used throughout the rest of the pipeline
_DIV_TO_LEAGUE: dict[str, str] = {
    "E0":  "Premier League",
    "E1":  "Championship",
    "SP1": "La Liga",
    "SP2": "La Liga 2",
    "I1":  "Serie A",
    "I2":  "Serie B",
    "D1":  "Bundesliga",
    "D2":  "Bundesliga 2",
    "F1":  "Ligue 1",
    "F2":  "Ligue 2",
    "P1":  "Primeira Liga",
    "N1":  "Eredivisie",
    "B1":  "Belgian Pro League",
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_matches(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"matches.csv is missing columns: {missing}")
    df = df.dropna(subset=list(required))
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    # Normalise column names expected by fit_dixon_coles_fast
    df = df.rename(columns={"HomeTeam": "home", "AwayTeam": "away",
                             "FTHG": "goals_home", "FTAG": "goals_away",
                             "Date": "date"})
    return df


def _last_n_seasons(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Return rows belonging to the n most recent seasons in the dataframe."""
    if "Season" in df.columns:
        seasons = sorted(df["Season"].unique())[-n:]
        return df[df["Season"].isin(seasons)]
    # Fallback: use date-based cutoff (~2 seasons = 730 days)
    cutoff = df["date"].max() - pd.Timedelta(days=730)
    return df[df["date"] >= cutoff]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train_all(
    matches_df: pd.DataFrame,
    xi: float = 0.0018,
    min_games: int = MIN_GAMES_TO_FIT,
) -> dict:
    """
    Fit a Dixon-Coles model for each division present in matches_df.

    Returns a dict keyed by canonical league name:
    {
        "Premier League": {
            "teams": { "TeamA": {"attack": 0.12, "defence": -0.05}, ... },
            "home_adv": 0.27,
            "rho": -0.09,
            "n_games": 760,
            "converged": true,
            "fitted_at": "2026-06-11T..."
        },
        ...
    }
    """
    from models.math.poisson import fit_dixon_coles_fast

    ratings: dict = {}
    fitted_at = datetime.utcnow().isoformat()

    for div, group in matches_df.groupby("Div"):
        league = _DIV_TO_LEAGUE.get(str(div), str(div))
        train_df = _last_n_seasons(group, N_SEASONS).copy()

        if len(train_df) < min_games:
            logger.warning("Skipping %s (%s): only %d games", div, league, len(train_df))
            continue

        logger.info("Fitting %s (%s) — %d games…", league, div, len(train_df))
        t0 = time.perf_counter()
        try:
            model = fit_dixon_coles_fast(train_df, xi=xi)
        except Exception as exc:
            logger.error("Fit failed for %s: %s", league, exc)
            continue
        elapsed = time.perf_counter() - t0
        logger.info("  → converged=%s  rho=%.4f  home_adv=%.4f  (%.2fs)",
                    model["converged"], model["rho"], model["home_adv"], elapsed)

        teams_out: dict[str, dict] = {}
        for team in model["teams"]:
            teams_out[team] = {
                "attack":  round(model["attack"][team],  6),
                "defence": round(model["defence"][team], 6),
            }

        ratings[league] = {
            "teams":     teams_out,
            "home_adv":  round(model["home_adv"], 6),
            "rho":       round(model["rho"], 6),
            "n_games":   len(train_df),
            "converged": model["converged"],
            "fitted_at": fitted_at,
        }

    return ratings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description="Train Dixon-Coles ratings per league")
    p.add_argument("--data", type=Path,
                   default=root / "data" / "historical" / "matches.csv",
                   help="Path to historical matches CSV")
    p.add_argument("--out", type=Path,
                   default=root / "data" / "dc_ratings.json",
                   help="Output path for dc_ratings.json")
    p.add_argument("--xi", type=float, default=0.0018, help="Time-decay rate")
    p.add_argument("--min-games", type=int, default=MIN_GAMES_TO_FIT,
                   help="Minimum games per division to attempt fitting")
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
    df = _load_matches(args.data)
    logger.info("Loaded %d rows across %d divisions", len(df), df["Div"].nunique())

    ratings = train_all(df, xi=args.xi, min_games=args.min_games)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(ratings, fh, indent=2, ensure_ascii=False)

    print(f"\nFitted {len(ratings)} leagues → {args.out}")
    for league, info in sorted(ratings.items()):
        status = "✓" if info["converged"] else "✗"
        print(f"  {status} {league:25s}  n={info['n_games']:4d}  "
              f"rho={info['rho']:+.4f}  home={info['home_adv']:+.4f}")


if __name__ == "__main__":
    main()
