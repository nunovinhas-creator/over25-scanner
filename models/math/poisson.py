"""
Dixon-Coles Poisson model for Over/Under 2.5 goals prediction.

Reference: Dixon & Coles (1997) "Modelling Association Football Scores and
Inefficiencies in the Football Betting Market", Applied Statistics 46(2), 265-280.
"""

from __future__ import annotations

import warnings
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import poisson


# ---------------------------------------------------------------------------
# Time-decay weights
# ---------------------------------------------------------------------------

def dixon_coles_weights(dates: pd.Series, xi: float = 0.0018) -> np.ndarray:
    """
    Compute time-decay weights for matches so that recent matches carry
    more weight during model fitting.

    Parameters
    ----------
    dates : pd.Series
        Series of match dates (datetime-like or str).
    xi : float
        Decay rate (per day).  Dixon & Coles used xi ≈ 0.0018.
        Higher → faster decay.  xi=0 → all weights equal 1.

    Returns
    -------
    weights : np.ndarray of float
        Weight for each match, in [0, 1].
    """
    dates = pd.to_datetime(dates)
    t_max = dates.max()
    delta_days = (t_max - dates).dt.total_seconds() / 86_400.0
    weights = np.exp(-xi * delta_days.values)
    return weights.astype(np.float64)


# ---------------------------------------------------------------------------
# Dixon-Coles tau correction
# ---------------------------------------------------------------------------

def _tau(lambda_h: float, lambda_a: float, x: int, y: int, rho: float) -> float:
    """
    Dixon-Coles low-score correction factor tau(x, y).

    Only applied to (0,0), (1,0), (0,1), (1,1) scorelines.
    """
    if x == 0 and y == 0:
        return 1 - lambda_h * lambda_a * rho
    if x == 1 and y == 0:
        return 1 + lambda_a * rho
    if x == 0 and y == 1:
        return 1 + lambda_h * rho
    if x == 1 and y == 1:
        return 1 - rho
    return 1.0


def _dc_log_likelihood(
    params: np.ndarray,
    home_teams: np.ndarray,
    away_teams: np.ndarray,
    goals_home: np.ndarray,
    goals_away: np.ndarray,
    weights: np.ndarray,
    team_index: dict,
    n_teams: int,
) -> float:
    """
    Negative weighted log-likelihood for the Dixon-Coles model.

    Parameter layout (length = 2*n_teams + 2):
        params[0:n_teams]          → attack strengths (alpha)
        params[n_teams:2*n_teams]  → defence weaknesses (beta)
        params[2*n_teams]          → home advantage (gamma, additive on log scale)
        params[2*n_teams + 1]      → rho (low-score correction)
    """
    n = n_teams
    alpha = params[:n]
    beta = params[n : 2 * n]
    gamma = params[2 * n]
    rho = params[2 * n + 1]

    ll = 0.0
    for i in range(len(home_teams)):
        hi = team_index[home_teams[i]]
        ai = team_index[away_teams[i]]
        lh = np.exp(alpha[hi] + beta[ai] + gamma)
        la = np.exp(alpha[ai] + beta[hi])
        gh = int(goals_home[i])
        ga = int(goals_away[i])
        tau = _tau(lh, la, gh, ga, rho)
        if tau <= 0:
            return 1e10  # infeasible
        ll += weights[i] * (
            poisson.logpmf(gh, lh) + poisson.logpmf(ga, la) + np.log(tau)
        )
    return -ll


# ---------------------------------------------------------------------------
# Model fitting
# ---------------------------------------------------------------------------

def fit_dixon_coles(
    matches_df: pd.DataFrame,
    xi: float = 0.0018,
    max_iter: int = 2000,
) -> dict:
    """
    Fit a Dixon-Coles model from historical match data.

    Parameters
    ----------
    matches_df : pd.DataFrame
        Must contain columns: home, away, goals_home, goals_away, date.
    xi : float
        Time-decay rate passed to `dixon_coles_weights`.
    max_iter : int
        Maximum iterations for the optimiser.

    Returns
    -------
    model : dict with keys
        - 'attack'  : dict {team: alpha}
        - 'defence' : dict {team: beta}
        - 'home_adv': float gamma
        - 'rho'     : float rho
        - 'teams'   : list of team names
        - 'converged': bool
    """
    required = {"home", "away", "goals_home", "goals_away", "date"}
    missing = required - set(matches_df.columns)
    if missing:
        raise ValueError(f"matches_df is missing columns: {missing}")

    df = matches_df.copy()
    df["goals_home"] = df["goals_home"].astype(int)
    df["goals_away"] = df["goals_away"].astype(int)

    teams = sorted(set(df["home"]) | set(df["away"]))
    team_index = {t: i for i, t in enumerate(teams)}
    n_teams = len(teams)

    weights = dixon_coles_weights(df["date"], xi=xi)

    home_arr = df["home"].values
    away_arr = df["away"].values
    gh_arr = df["goals_home"].values
    ga_arr = df["goals_away"].values

    # Initial parameters: all strengths/weaknesses = 0, gamma = 0.1, rho = -0.1
    x0 = np.zeros(2 * n_teams + 2)
    x0[2 * n_teams] = 0.1       # home advantage
    x0[2 * n_teams + 1] = -0.1  # rho

    # Constraint: sum of attack params = 0 (identifiability)
    constraints = [
        {
            "type": "eq",
            "fun": lambda p: np.sum(p[:n_teams]),
        }
    ]

    # Bounds: rho in (-1, 1) to keep tau > 0 in feasible region
    bounds = (
        [(None, None)] * (2 * n_teams)
        + [(None, None)]        # gamma
        + [(-0.99, 0.99)]       # rho
    )

    result = minimize(
        _dc_log_likelihood,
        x0,
        args=(home_arr, away_arr, gh_arr, ga_arr, weights, team_index, n_teams),
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"maxiter": max_iter, "ftol": 1e-9},
    )

    params = result.x
    alpha = params[:n_teams]
    beta = params[n_teams : 2 * n_teams]
    gamma = float(params[2 * n_teams])
    rho = float(params[2 * n_teams + 1])

    return {
        "attack": {t: float(alpha[team_index[t]]) for t in teams},
        "defence": {t: float(beta[team_index[t]]) for t in teams},
        "home_adv": gamma,
        "rho": rho,
        "teams": teams,
        "converged": result.success,
        "_optimizer_message": result.message,
    }


def _lambda_from_model(model: dict, home: str, away: str) -> Tuple[float, float]:
    """
    Compute expected goals from a fitted Dixon-Coles model for a single match.
    Falls back to global mean (1.35) for unseen teams.
    """
    default_attack = 0.0
    default_defence = 0.0

    alpha_h = model["attack"].get(home, default_attack)
    beta_a = model["defence"].get(away, default_defence)
    alpha_a = model["attack"].get(away, default_attack)
    beta_h = model["defence"].get(home, default_defence)
    gamma = model["home_adv"]

    lambda_h = np.exp(alpha_h + beta_a + gamma)
    lambda_a = np.exp(alpha_a + beta_h)
    return float(lambda_h), float(lambda_a)


# ---------------------------------------------------------------------------
# Over 2.5 probability from Poisson convolution
# ---------------------------------------------------------------------------

def prob_over25_poisson(
    lambda_h: float,
    lambda_a: float,
    rho: float = 0.0,
    max_goals: int = 10,
) -> float:
    """
    Compute P(goals_home + goals_away > 2.5) using Poisson convolution
    with optional Dixon-Coles rho correction for low scorelines.

    Parameters
    ----------
    lambda_h : float
        Expected goals for home team (must be > 0).
    lambda_a : float
        Expected goals for away team (must be > 0).
    rho : float
        Dixon-Coles low-score correction.  Use 0.0 for simple Poisson.
    max_goals : int
        Truncation at max_goals per team (sum cut-off = 2*max_goals).

    Returns
    -------
    p_over25 : float in [0, 1]
    """
    lambda_h = max(lambda_h, 1e-6)
    lambda_a = max(lambda_a, 1e-6)

    g = np.arange(0, max_goals + 1)
    pmf_h = poisson.pmf(g, lambda_h)
    pmf_a = poisson.pmf(g, lambda_a)

    p_under_or_equal = 0.0
    for gh in range(max_goals + 1):
        for ga in range(max_goals + 1):
            if gh + ga <= 2:  # 0, 1, or 2 goals → Under 2.5
                p_joint = pmf_h[gh] * pmf_a[ga]
                # Apply Dixon-Coles correction to low-score cells
                if rho != 0.0:
                    tau = _tau(lambda_h, lambda_a, gh, ga, rho)
                    tau = max(tau, 1e-9)  # numerical safety
                    p_joint *= tau
                p_under_or_equal += p_joint

    p_over25 = 1.0 - p_under_or_equal
    return float(np.clip(p_over25, 0.0, 1.0))


def prob_over25_from_model(
    model: dict,
    home: str,
    away: str,
    max_goals: int = 10,
) -> float:
    """
    Predict P(Over 2.5) for a future match using a fitted Dixon-Coles model.

    Parameters
    ----------
    model : dict
        Output of `fit_dixon_coles`.
    home, away : str
        Team names (unseen teams fall back to league-average strength).
    max_goals : int
        Truncation limit.

    Returns
    -------
    float in [0, 1]
    """
    lh, la = _lambda_from_model(model, home, away)
    rho = model.get("rho", 0.0)
    return prob_over25_poisson(lh, la, rho=rho, max_goals=max_goals)


# ---------------------------------------------------------------------------
# Quick version using raw xG (no fitting)
# ---------------------------------------------------------------------------

def prob_over25_from_xg(xg_h: float, xg_a: float, max_goals: int = 10) -> float:
    """
    Simplified Over 2.5 probability using xG values directly as Poisson
    rate parameters — no model fitting required.

    This is useful at prediction time when a pre-match xG estimate is
    available from a feed (e.g. the BSD API's xg_total split).

    Parameters
    ----------
    xg_h : float
        Pre-match expected goals for home team.
    xg_a : float
        Pre-match expected goals for away team.
    max_goals : int
        Poisson truncation.

    Returns
    -------
    float in [0, 1]
    """
    if xg_h is None or xg_a is None or np.isnan(xg_h) or np.isnan(xg_a):
        raise ValueError("xg_h and xg_a must be non-null finite numbers")
    xg_h = max(float(xg_h), 1e-6)
    xg_a = max(float(xg_a), 1e-6)
    return prob_over25_poisson(xg_h, xg_a, rho=0.0, max_goals=max_goals)


# ---------------------------------------------------------------------------
# Convenience: batch prediction
# ---------------------------------------------------------------------------

def batch_predict(
    model: dict,
    fixtures: pd.DataFrame,
    max_goals: int = 10,
) -> pd.Series:
    """
    Predict Over 2.5 probability for a DataFrame of fixtures.

    Parameters
    ----------
    model : dict
        Output of `fit_dixon_coles`.
    fixtures : pd.DataFrame
        Must contain columns 'home' and 'away'.
    max_goals : int

    Returns
    -------
    pd.Series of float probabilities, same index as fixtures.
    """
    probs = []
    for _, row in fixtures.iterrows():
        p = prob_over25_from_model(model, row["home"], row["away"], max_goals)
        probs.append(p)
    return pd.Series(probs, index=fixtures.index, name="prob_over25_dc")
