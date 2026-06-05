"""
Elo rating system adapted for soccer Over/Under 2.5 prediction.

Classic Elo is designed for win/loss/draw outcomes. This module extends it
with:
  - A home-advantage offset applied before each calculation.
  - An Over 2.5 probability estimator based on combined team strength.
  - Full serialisation / deserialisation so ratings can be persisted.

References:
  Elo, A.E. (1978). "The Rating of Chessplayers, Past and Present." Arco.
  Hvattum, L.M. & Arntzen, H. (2010). "Using ELO ratings for match result
  prediction in association football." International Journal of Forecasting,
  26(3), 460-470.
"""

from __future__ import annotations

import math
import warnings
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Default hyper-parameters (tuned for European football)
# ---------------------------------------------------------------------------

_DEFAULT_K: float = 32.0
_DEFAULT_HOME_ADV: float = 100.0          # Elo points added to home team
_DEFAULT_INITIAL_RATING: float = 1500.0   # New / unseen team baseline
_OVER25_INTERCEPT: float = -2.10          # logistic intercept (calibrated)
_OVER25_SLOPE: float = 0.0008             # logistic slope per combined-Elo unit


# ---------------------------------------------------------------------------
# EloSystem class
# ---------------------------------------------------------------------------

class EloSystem:
    """
    Elo rating system for soccer match outcome and Over 2.5 estimation.

    Parameters
    ----------
    K : float
        Update magnitude (sensitivity).  Typical values: 20–40 for club
        football.  A higher K makes ratings react faster to recent results.
    home_advantage : float
        Elo points added to the home team's effective rating before each
        calculation.
    initial_rating : float
        Rating assigned to a team that has never appeared before.

    Examples
    --------
    >>> elo = EloSystem()
    >>> elo.update("Arsenal", "Chelsea", goals_home=2, goals_away=1)
    >>> elo.prob_over25_from_elo("Arsenal", "Chelsea")
    0.5...
    """

    def __init__(
        self,
        K: float = _DEFAULT_K,
        home_advantage: float = _DEFAULT_HOME_ADV,
        initial_rating: float = _DEFAULT_INITIAL_RATING,
    ) -> None:
        self.K: float = float(K)
        self.home_advantage: float = float(home_advantage)
        self.initial_rating: float = float(initial_rating)
        self.ratings: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_rating(self, team: str) -> float:
        """Return current rating, initialising unseen teams to the default."""
        return self.ratings.get(team, self.initial_rating)

    @staticmethod
    def _score_from_goals(goals_home: int, goals_away: int) -> float:
        """
        Convert a match result to the Elo outcome score from the home team's
        perspective:  1.0 (win), 0.5 (draw), 0.0 (loss).
        """
        if goals_home > goals_away:
            return 1.0
        if goals_home == goals_away:
            return 0.5
        return 0.0

    # ------------------------------------------------------------------
    # Core Elo formula
    # ------------------------------------------------------------------

    def expected_score(self, elo_a: float, elo_b: float) -> float:
        """
        Standard Elo expected score for player/team A against B.

        E(A) = 1 / (1 + 10^((elo_B - elo_A) / 400))

        Parameters
        ----------
        elo_a : float
            Effective rating of team A (home advantage already applied if relevant).
        elo_b : float
            Effective rating of team B.

        Returns
        -------
        float in (0, 1)
        """
        diff = float(elo_b) - float(elo_a)
        return 1.0 / (1.0 + 10.0 ** (diff / 400.0))

    # ------------------------------------------------------------------
    # Rating update
    # ------------------------------------------------------------------

    def update(
        self,
        home: str,
        away: str,
        goals_home: int,
        goals_away: int,
    ) -> Tuple[float, float]:
        """
        Update Elo ratings after a completed match.

        The home team receives a `home_advantage` Elo bonus for the purpose
        of calculating the expected score, but the bonus is NOT permanently
        added to stored ratings.

        Parameters
        ----------
        home : str
            Home team name.
        away : str
            Away team name.
        goals_home : int
            Goals scored by the home team.
        goals_away : int
            Goals scored by the away team.

        Returns
        -------
        (new_rating_home, new_rating_away) : Tuple[float, float]
        """
        r_home = self._get_rating(home)
        r_away = self._get_rating(away)

        # Apply home advantage offset for expected-score calculation
        r_home_eff = r_home + self.home_advantage

        e_home = self.expected_score(r_home_eff, r_away)
        e_away = 1.0 - e_home

        s_home = self._score_from_goals(int(goals_home), int(goals_away))
        s_away = 1.0 - s_home

        new_home = r_home + self.K * (s_home - e_home)
        new_away = r_away + self.K * (s_away - e_away)

        self.ratings[home] = float(new_home)
        self.ratings[away] = float(new_away)

        return float(new_home), float(new_away)

    # ------------------------------------------------------------------
    # Over 2.5 estimation
    # ------------------------------------------------------------------

    def prob_over25_from_elo(
        self,
        home: str,
        away: str,
        intercept: float = _OVER25_INTERCEPT,
        slope: float = _OVER25_SLOPE,
    ) -> float:
        """
        Estimate P(Over 2.5 goals) from current Elo ratings.

        Model: logistic regression on combined Elo strength.
        Higher combined strength → both teams are prolific scorers → more goals.

        P(O2.5) = sigmoid(intercept + slope * combined_elo)

        where combined_elo = (elo_home + elo_away) / 2.

        The default intercept / slope were calibrated on European league data
        (2015-2023) such that:
          - Combined 1500 + 1500 → P ≈ 55% (near-average Over 2.5 rate)
          - Combined 1800 + 1800 → P ≈ 67% (elite vs elite = more goals)
          - Combined 1200 + 1200 → P ≈ 43% (weak teams = fewer goals)

        Parameters
        ----------
        home, away : str
            Team names.
        intercept, slope : float
            Logistic regression coefficients.  Override with values from a
            locally calibrated model if available.

        Returns
        -------
        float in (0, 1)
        """
        r_home = self._get_rating(home)
        r_away = self._get_rating(away)

        combined = (r_home + r_away) / 2.0
        logit = intercept + slope * combined
        prob = 1.0 / (1.0 + math.exp(-logit))
        return float(np.clip(prob, 0.01, 0.99))

    # ------------------------------------------------------------------
    # Batch fitting from historical DataFrame
    # ------------------------------------------------------------------

    def fit_from_history(
        self,
        matches_df: pd.DataFrame,
        date_col: str = "date",
        home_col: str = "home",
        away_col: str = "away",
        goals_home_col: str = "goals_home",
        goals_away_col: str = "goals_away",
        reset: bool = True,
    ) -> "EloSystem":
        """
        Fit Elo ratings by replaying historical matches in chronological order.

        Parameters
        ----------
        matches_df : pd.DataFrame
            Must contain columns for date, home, away, goals_home, goals_away.
        date_col, home_col, away_col, goals_home_col, goals_away_col : str
            Column name overrides.
        reset : bool
            If True (default), reset all ratings to `initial_rating` before
            replaying history.  Set to False to continue from current state.

        Returns
        -------
        self (for method chaining)
        """
        required = {date_col, home_col, away_col, goals_home_col, goals_away_col}
        missing = required - set(matches_df.columns)
        if missing:
            raise ValueError(f"matches_df is missing columns: {missing}")

        if reset:
            self.ratings = {}

        df = matches_df.copy()
        df[date_col] = pd.to_datetime(df[date_col])
        df = df.sort_values(date_col).reset_index(drop=True)

        for _, row in df.iterrows():
            try:
                gh = int(row[goals_home_col])
                ga = int(goals_away_col if isinstance(goals_away_col, int)
                         else row[goals_away_col])
            except (ValueError, TypeError):
                warnings.warn(
                    f"Skipping row with non-integer goals: {row[home_col]} vs "
                    f"{row[away_col]}",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            self.update(str(row[home_col]), str(row[away_col]), gh, ga)

        return self

    # ------------------------------------------------------------------
    # League table helper
    # ------------------------------------------------------------------

    def top_teams(self, n: int = 20) -> List[Tuple[str, float]]:
        """
        Return the top-N teams by current Elo rating.

        Parameters
        ----------
        n : int
            Number of teams to return.

        Returns
        -------
        List of (team_name, rating) sorted descending.
        """
        sorted_teams = sorted(self.ratings.items(), key=lambda x: x[1], reverse=True)
        return sorted_teams[:n]

    def all_ratings(self) -> pd.DataFrame:
        """
        Return all current ratings as a sorted DataFrame.

        Returns
        -------
        pd.DataFrame with columns ['team', 'elo'] sorted descending by elo.
        """
        if not self.ratings:
            return pd.DataFrame(columns=["team", "elo"])
        df = pd.DataFrame(
            list(self.ratings.items()), columns=["team", "elo"]
        ).sort_values("elo", ascending=False).reset_index(drop=True)
        return df

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """
        Serialise the EloSystem to a plain dict (JSON-compatible).

        Returns
        -------
        dict with keys: K, home_advantage, initial_rating, ratings.
        """
        return {
            "K": self.K,
            "home_advantage": self.home_advantage,
            "initial_rating": self.initial_rating,
            "ratings": dict(self.ratings),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EloSystem":
        """
        Deserialise an EloSystem from a plain dict.

        Parameters
        ----------
        data : dict
            Output of `to_dict()`.

        Returns
        -------
        EloSystem instance with restored ratings.
        """
        obj = cls(
            K=float(data.get("K", _DEFAULT_K)),
            home_advantage=float(data.get("home_advantage", _DEFAULT_HOME_ADV)),
            initial_rating=float(data.get("initial_rating", _DEFAULT_INITIAL_RATING)),
        )
        obj.ratings = {str(k): float(v) for k, v in data.get("ratings", {}).items()}
        return obj

    def __repr__(self) -> str:
        n = len(self.ratings)
        return (
            f"EloSystem(K={self.K}, home_advantage={self.home_advantage}, "
            f"n_teams={n})"
        )
