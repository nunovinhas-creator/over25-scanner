"""
pipeline/historical.py
----------------------
Download and normalise historical football match data from football-data.co.uk.

Columns normalised to:
  Div, Date, Season, HomeTeam, AwayTeam, FTHG, FTAG, over25,
  P>2.5, P<2.5, PC>2.5, PC<2.5,
  B365>2.5, B365<2.5, Avg>2.5, Max>2.5

Output:  data/historical/matches.csv  (always)
         data/historical/matches.parquet  (if pyarrow is available)

Usage
-----
    # Download 5 seasons from football-data.co.uk:
    python -m pipeline.historical --download-all

    # Update only the current season (2025-26):
    python -m pipeline.historical --update

    # Generate synthetic data (no network needed — cloud / CI):
    python -m pipeline.historical --synthetic
"""

from __future__ import annotations

import argparse
import hashlib
import io
import logging
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.football-data.co.uk/mmz4281/{epoch}/{div}.csv"

EPOCHS = ["2526", "2425", "2324", "2223", "2122"]  # newest first
CURRENT_EPOCH = "2526"

DIVISIONS = ["E0", "E1", "SP1", "SP2", "I1", "I2", "D1", "D2", "F1", "F2", "P1", "N1", "B1"]

# Map division → league name (for dc_ratings.json key)
DIV_LEAGUE = {
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

# Approximate team counts per division (affects n_games per season)
DIV_N_TEAMS = {
    "E0": 20, "E1": 24, "SP1": 20, "SP2": 22,
    "I1": 20, "I2": 20, "D1": 18, "D2": 18,
    "F1": 20, "F2": 20, "P1": 18, "N1": 18, "B1": 16,
}

# Season start years (for synthetic date generation)
EPOCH_SEASON_START = {
    "2526": 2025, "2425": 2024, "2324": 2023, "2223": 2022, "2122": 2021,
}

# Columns we keep from the raw CSV (any subset that exists is retained)
KEEP_COLS = [
    "Div", "Date", "HomeTeam", "AwayTeam",
    "FTHG", "FTAG",
    "P>2.5", "P<2.5", "PC>2.5", "PC<2.5",
    "B365>2.5", "B365<2.5",
    "Avg>2.5", "Max>2.5",
]

# De-duplicate key
DEDUP_COLS = ["Div", "Date", "HomeTeam", "AwayTeam"]

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,text/csv,*/*",
}


def _fetch_csv(url: str, max_retries: int = 4, timeout: int = 30) -> Optional[bytes]:
    """Fetch a URL with retries and exponential backoff. Returns None on failure."""
    req = urllib.request.Request(url, headers=_HEADERS)
    delay = 2
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            if attempt == max_retries:
                logger.warning("Failed to fetch %s after %d attempts: %s", url, max_retries + 1, exc)
                return None
            logger.debug("Attempt %d failed (%s); retrying in %ds", attempt + 1, exc, delay)
            time.sleep(delay)
            delay *= 2
    return None


def _download_one(epoch: str, div: str) -> Optional[pd.DataFrame]:
    url = BASE_URL.format(epoch=epoch, div=div)
    logger.info("Downloading %s", url)
    raw = _fetch_csv(url)
    if raw is None:
        return None
    try:
        df = pd.read_csv(io.BytesIO(raw), encoding="latin-1", on_bad_lines="skip")
        return df
    except Exception as exc:
        logger.warning("Could not parse %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(df: pd.DataFrame, epoch: str) -> pd.DataFrame:
    """Keep only relevant columns, parse dates, add over25 flag."""
    existing = [c for c in KEEP_COLS if c in df.columns]
    out = df[existing].copy()

    # Parse Date (dd/mm/yy or dd/mm/yyyy)
    if "Date" in out.columns:
        out["Date"] = pd.to_datetime(out["Date"], dayfirst=True, errors="coerce")
        out = out.dropna(subset=["Date"])

    # Ensure Div column
    if "Div" not in out.columns:
        out["Div"] = "?"

    # Season tag
    out["Season"] = epoch

    # Over 2.5 flag
    for col in ("FTHG", "FTAG"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "FTHG" in out.columns and "FTAG" in out.columns:
        out["over25"] = ((out["FTHG"] + out["FTAG"]) >= 3).astype(int)

    # Coerce odds columns to float
    odds_cols = [c for c in out.columns if ">" in c or "<" in c or "B365" in c or "Avg" in c or "Max" in c]
    for col in odds_cols:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return out


def _dedup(df: pd.DataFrame) -> pd.DataFrame:
    """Remove duplicate rows by Div+Date+HomeTeam+AwayTeam."""
    key = [c for c in DEDUP_COLS if c in df.columns]
    if not key:
        return df
    return df.drop_duplicates(subset=key)


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

# Per-division goal-scoring averages (home, away)
_DIV_GOAL_PARAMS: dict[str, tuple[float, float]] = {
    "E0": (1.55, 1.15), "E1": (1.45, 1.10), "SP1": (1.50, 1.05),
    "SP2": (1.35, 1.00), "I1": (1.40, 1.10), "I2": (1.30, 0.95),
    "D1": (1.60, 1.25), "D2": (1.45, 1.15), "F1": (1.40, 1.10),
    "F2": (1.35, 1.05), "P1": (1.35, 1.00), "N1": (1.65, 1.30),
    "B1": (1.50, 1.20),
}


def _synthetic_season(
    div: str,
    epoch: str,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Generate one division-season of synthetic match data."""
    n_teams = DIV_N_TEAMS.get(div, 20)
    teams = [f"T{i+1:02d}" for i in range(n_teams)]

    # Per-team attack/defence multipliers (log-normal, mean=1, σ=0.25)
    attack  = rng.lognormal(0.0, 0.25, n_teams)
    defence = rng.lognormal(0.0, 0.25, n_teams)

    mu_h, mu_a = _DIV_GOAL_PARAMS.get(div, (1.45, 1.10))

    # Season calendar: Aug→May, skip Dec 24/25 and Jan 1
    season_start_year = EPOCH_SEASON_START.get(epoch, 2021)
    aug_start = date(season_start_year, 8, 5)

    rows = []
    # Round-robin home and away
    matchdays: list[list[tuple[int, int]]] = []
    all_pairs = [(h, a) for h in range(n_teams) for a in range(n_teams) if h != a]
    rng.shuffle(all_pairs)  # type: ignore[arg-type]

    # Distribute pairs across ~38-44 matchdays
    gd_size = n_teams // 2
    for i in range(0, len(all_pairs), gd_size):
        matchdays.append(all_pairs[i : i + gd_size])

    # Assign calendar dates (roughly one matchday per week)
    current_day = aug_start
    for gd_idx, gd in enumerate(matchdays):
        # Advance by 7 days; skip Christmas/New Year
        if current_day > date(season_start_year + 1, 5, 31):
            break
        for h_idx, a_idx in gd:
            h_name = teams[h_idx]
            a_name = teams[a_idx]
            lh = mu_h * attack[h_idx] / defence[a_idx]
            la = mu_a * attack[a_idx] / defence[h_idx]
            gh = int(rng.poisson(max(lh, 0.1)))
            ga = int(rng.poisson(max(la, 0.1)))

            # True probability of over 2.5 (Poisson)
            from scipy.stats import poisson as _poisson
            p_true = float(1.0 - sum(
                _poisson.pmf(k, lh) * _poisson.pmf(j, la)
                for k in range(4) for j in range(4)
                if k + j <= 2
            ))
            p_true = float(np.clip(p_true, 0.05, 0.95))

            # Simulate odds with bookmaker margin
            # Pinnacle opening: ~4% margin
            pin_margin   = rng.uniform(0.038, 0.045)
            p_over_book  = p_true * (1 + pin_margin)
            p_under_book = (1 - p_true) * (1 + pin_margin)
            p_odds = 1.0 / p_over_book
            u_odds = 1.0 / p_under_book

            # Closing: slightly tighter margin + small noise
            noise = rng.normal(0, 0.005)
            p_close_true = float(np.clip(p_true + noise, 0.05, 0.95))
            close_margin = rng.uniform(0.032, 0.038)
            pc_odds = 1.0 / (p_close_true * (1 + close_margin))
            uc_odds = 1.0 / ((1 - p_close_true) * (1 + close_margin))

            # Bet365: wider margin ~6%
            b365_margin   = rng.uniform(0.055, 0.065)
            b365_p_odds   = 1.0 / (p_true * (1 + b365_margin))
            b365_u_odds   = 1.0 / ((1 - p_true) * (1 + b365_margin))

            rows.append({
                "Div":      div,
                "Date":     current_day,
                "Season":   epoch,
                "HomeTeam": h_name,
                "AwayTeam": a_name,
                "FTHG":     gh,
                "FTAG":     ga,
                "over25":   int(gh + ga >= 3),
                "P>2.5":    round(p_odds, 3),
                "P<2.5":    round(u_odds, 3),
                "PC>2.5":   round(pc_odds, 3),
                "PC<2.5":   round(uc_odds, 3),
                "B365>2.5": round(b365_p_odds, 3),
                "B365<2.5": round(b365_u_odds, 3),
                "Avg>2.5":  round((p_odds + b365_p_odds) / 2, 3),
                "Max>2.5":  round(max(p_odds, b365_p_odds), 3),
            })
        current_day += timedelta(days=7)

    return pd.DataFrame(rows)


def generate_synthetic(seed: int = 42) -> pd.DataFrame:
    """Generate synthetic data for 5 seasons × 13 divisions (≥10 000 games)."""
    rng = np.random.default_rng(seed)
    parts = []
    for epoch in EPOCHS:
        for div in DIVISIONS:
            df = _synthetic_season(div, epoch, rng)
            if not df.empty:
                parts.append(df)
    combined = pd.concat(parts, ignore_index=True)
    combined = _dedup(combined)
    combined.sort_values(["Date", "Div"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined


# ---------------------------------------------------------------------------
# Download all / update
# ---------------------------------------------------------------------------

def download_all(epochs: list[str] = EPOCHS) -> pd.DataFrame:
    """Download all epochs × divisions from football-data.co.uk."""
    parts = []
    for epoch in epochs:
        for div in DIVISIONS:
            df = _download_one(epoch, div)
            if df is not None and not df.empty:
                parts.append(_normalise(df, epoch))
    if not parts:
        raise RuntimeError("No data downloaded — is the network reachable?")
    combined = pd.concat(parts, ignore_index=True)
    combined = _dedup(combined)
    combined.sort_values(["Date", "Div"], inplace=True, na_position="last")
    return combined


def update_current(existing_path: Path) -> pd.DataFrame:
    """Download only the current epoch and merge with existing data."""
    new_parts = []
    for div in DIVISIONS:
        df = _download_one(CURRENT_EPOCH, div)
        if df is not None and not df.empty:
            new_parts.append(_normalise(df, CURRENT_EPOCH))

    if not new_parts:
        raise RuntimeError("No data for current epoch — network unreachable?")

    new_df = pd.concat(new_parts, ignore_index=True)

    if existing_path.exists():
        old_df = pd.read_csv(existing_path, parse_dates=["Date"])
        # Drop old current-epoch rows and replace with fresh download
        old_df = old_df[old_df["Season"] != CURRENT_EPOCH]
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df

    combined = _dedup(combined)
    combined.sort_values(["Date", "Div"], inplace=True, na_position="last")
    return combined


# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------

def save(df: pd.DataFrame, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "matches.csv"
    df.to_csv(csv_path, index=False)
    logger.info("Saved %d rows to %s", len(df), csv_path)

    try:
        import pyarrow  # noqa: F401
        parquet_path = out_dir / "matches.parquet"
        df_pq = df.copy()
        if "Season" in df_pq.columns:
            df_pq["Season"] = df_pq["Season"].astype(str)
        df_pq.to_parquet(parquet_path, index=False)
        logger.info("Saved parquet to %s", parquet_path)
    except ImportError:
        logger.debug("pyarrow not available; skipping parquet output")


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Historical football data pipeline")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--download-all", action="store_true",
                      help="Download all 5 seasons from football-data.co.uk")
    mode.add_argument("--update", action="store_true",
                      help="Download current season and merge with existing data")
    mode.add_argument("--synthetic", action="store_true",
                      help="Generate synthetic data (no network needed)")
    p.add_argument("--out-dir", type=Path,
                   default=Path(__file__).resolve().parent.parent / "data" / "historical",
                   help="Output directory (default: data/historical)")
    p.add_argument("--seed", type=int, default=42, help="RNG seed for synthetic mode")
    return p


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(message)s")
    args = _build_parser().parse_args(argv)

    if args.synthetic:
        logger.info("Generating synthetic data (5 epochs × 13 divisions)…")
        df = generate_synthetic(seed=args.seed)
    elif args.update:
        csv_path = args.out_dir / "matches.csv"
        logger.info("Updating current epoch (%s) into %s…", CURRENT_EPOCH, csv_path)
        df = update_current(csv_path)
    else:
        logger.info("Downloading all epochs: %s", EPOCHS)
        df = download_all()

    save(df, args.out_dir)

    n = len(df)
    seasons = df["Season"].nunique() if "Season" in df.columns else "?"
    divs = df["Div"].nunique() if "Div" in df.columns else "?"
    print(f"\nDone: {n:,} matches | {seasons} seasons | {divs} divisions")
    if n < 10_000:
        logger.warning("Dataset has fewer than 10 000 games (%d); check inputs.", n)


if __name__ == "__main__":
    main()
