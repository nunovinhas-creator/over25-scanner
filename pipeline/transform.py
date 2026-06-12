"""
Feature engineering and data transformation layer.

All functions accept and return ``pd.DataFrame`` objects using the schema
produced by ``data.schema.picks_schema.validate_picks``.

Key design decisions:
- Functions are stateless and pure where possible (no side effects).
- ``add_calibrated_prob`` is the one exception — it requires a fitted
  calibrator object (e.g. sklearn's ``CalibratedClassifierCV``).
- ``create_feature_matrix`` returns a scaled NumPy-backed DataFrame ready
  for model training / evaluation.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Expected value threshold for is_value_bet flag
_EV_THRESHOLD = 0.0

# Default blend weight (overridden by Config.MODEL_WEIGHT when called from ETL)
_DEFAULT_MODEL_WEIGHT = 0.30

# Common abbreviations → canonical names (lowercase)
_TEAM_ABBREVS: dict[str, str] = {
    "man city": "manchester city",
    "man utd": "manchester united",
    "man united": "manchester united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "west ham": "west ham united",
    "newcastle": "newcastle united",
    "wolves": "wolverhampton wanderers",
    "brighton": "brighton & hove albion",
    "brentford": "brentford fc",
    "leicester": "leicester city",
    "norwich": "norwich city",
    "southampton": "southampton fc",
    "leeds": "leeds united",
    "atletico": "atletico madrid",
    "atletico madrid": "atletico madrid",
    "athletic": "athletic bilbao",
    "athletic bilbao": "athletic bilbao",
    "real betis": "real betis",
    "bayer": "bayer leverkusen",
    "bayer leverkusen": "bayer leverkusen",
    "dortmund": "borussia dortmund",
    "borussia dortmund": "borussia dortmund",
    "mgladbach": "borussia monchengladbach",
    "gladbach": "borussia monchengladbach",
    "psv": "psv eindhoven",
    "ajax": "afc ajax",
    "saint-etienne": "saint-etienne",
    "st etienne": "saint-etienne",
}

# Feature columns for the model training matrix
_FEATURE_COLS = [
    "prob_over25",
    "xg_total",
    "btts_prob",
    "div",
    "score_sistema",
    "has_sharp",
    "is_shortening",
]


# ---------------------------------------------------------------------------
# Market probability blend
# ---------------------------------------------------------------------------


def compute_final_probability(
    prob_over25: float,
    odds_over: float,
    odds_under: Optional[float] = None,
    model_weight: float = _DEFAULT_MODEL_WEIGHT,
) -> dict:
    """
    Blend the model's raw probability with the de-vigged market probability.

    The market (Pinnacle closing line) is better calibrated than the system
    model (system WR≈49% vs announced 64.7%).  Blending anchors the final
    probability toward the market, reducing overconfidence.

    Formula::

        p_final = model_weight * p_model + (1 - model_weight) * p_market
        ev_final = p_final * odds_over - 1

    Parameters
    ----------
    prob_over25 : float
        System model probability (0–100 scale).
    odds_over : float
        Decimal odds for Over 2.5.
    odds_under : float or None
        Decimal odds for Under 2.5.  When absent (common in picks.json),
        a 5% assumed margin fallback is used and ``p_market_source`` is
        set to ``"fallback"``.
    model_weight : float
        Weight given to the model probability (default 0.30).

    Returns
    -------
    dict with keys:
        p_model         — model probability in [0, 1]
        p_market        — de-vigged market probability in [0, 1]
        p_market_source — 'devig' | 'fallback'
        p_final         — blended probability in [0, 1]
        ev_final        — blended EV (e.g. 0.03 = 3% edge)
    """
    from models.math.devig import metodo_multiplicativo  # lazy import

    p_model = max(0.0, min(1.0, float(prob_over25) / 100.0))

    # Market probability
    try:
        ou = float(odds_under) if odds_under is not None else None
        ov = float(odds_over)
        if ou and ou > 1.0 and ov > 1.0:
            p_market, _ = metodo_multiplicativo(ov, ou)
            p_market_source = "devig"
        else:
            raise ValueError("no valid odds_under")
    except (TypeError, ValueError):
        # Fallback: assume 5% total margin → implied p_market = (1/odds_over)/1.05
        p_market = (1.0 / float(odds_over)) / 1.05
        p_market_source = "fallback"

    p_market = max(0.0, min(1.0, p_market))
    p_final = model_weight * p_model + (1.0 - model_weight) * p_market
    ev_final = p_final * float(odds_over) - 1.0

    return {
        "p_model": round(p_model, 6),
        "p_market": round(p_market, 6),
        "p_market_source": p_market_source,
        "p_final": round(p_final, 6),
        "ev_final": round(ev_final, 6),
    }


# ---------------------------------------------------------------------------
# DC-calibrated probability blend (FASE 4/5)
# ---------------------------------------------------------------------------


def compute_final_probability_dc(
    home: str,
    away: str,
    league: str,
    dc_ratings: dict,
    calibrator_fn,
    odds_over: float,
    odds_under: Optional[float] = None,
    model_weight: float = _DEFAULT_MODEL_WEIGHT,
) -> dict:
    """
    Compute blended probability using Dixon-Coles ratings + isotonic calibrator.

    Parameters
    ----------
    home, away : str
        Team names (will be matched against dc_ratings keys).
    league : str
        Canonical league name (must match key in dc_ratings).
    dc_ratings : dict
        Full dc_ratings.json structure as loaded by json.load().
    calibrator_fn : callable
        Function p_arr → p_arr_calibrated (from _calibrator_fn_from_data).
    odds_over : float
        Decimal odds for Over 2.5.
    odds_under : float or None
        Decimal odds for Under 2.5.
    model_weight : float
        Blend weight for the DC model (default 0.30).

    Returns
    -------
    dict with keys:
        p_model_source  — 'dc' | 'market_only'
        p_dc_raw        — raw Dixon-Coles probability (or NaN if unavailable)
        p_model         — calibrated DC probability (or p_market if fallback)
        p_market        — de-vigged market probability
        p_market_source — 'devig' | 'fallback'
        p_final         — blended probability
        ev_final        — p_final * odds_over - 1
        odds_band       — categorical: '<1.50' | '1.50–1.70' | '1.70–2.00' | '2.00–2.50' | '>2.50'
    """
    from models.math.poisson import prob_over25_poisson  # lazy import
    from models.math.devig import metodo_multiplicativo  # lazy import

    # -- Market probability ---------------------------------------------------
    try:
        ou = float(odds_under) if odds_under is not None else None
        ov = float(odds_over)
        if ou and ou > 1.0 and ov > 1.0:
            p_market, _ = metodo_multiplicativo(ov, ou)
            p_market_source = "devig"
        else:
            raise ValueError("no valid odds_under")
    except (TypeError, ValueError):
        p_market = (1.0 / float(odds_over)) / 1.05
        p_market_source = "fallback"
    p_market = float(np.clip(p_market, 0.0, 1.0))

    # -- Dixon-Coles probability ----------------------------------------------
    p_dc_raw: float = float("nan")
    p_model_source = "market_only"
    p_model = p_market

    league_data = dc_ratings.get(league)
    if league_data is not None:
        teams = league_data.get("teams", {})
        home_data = teams.get(home)
        away_data = teams.get(away)
        if home_data and away_data:
            try:
                # Reconstruct lambda_h, lambda_a from DC log-linear model
                alpha_h = home_data["attack"]
                beta_h = home_data["defence"]
                alpha_a = away_data["attack"]
                beta_a = away_data["defence"]
                gamma = league_data["home_adv"]
                rho = league_data.get("rho", 0.0)
                lambda_h = float(np.exp(alpha_h + beta_a + gamma))
                lambda_a = float(np.exp(alpha_a + beta_h))
                p_dc_raw = float(prob_over25_poisson(lambda_h, lambda_a, rho=rho))
                p_calibrated = float(calibrator_fn(np.array([p_dc_raw]))[0])
                p_model = float(np.clip(p_calibrated, 0.0, 1.0))
                p_model_source = "dc"
            except Exception:
                logger.warning(
                    "compute_final_probability_dc: DC prediction failed for %s vs %s",
                    home, away,
                )

    # -- Blend ----------------------------------------------------------------
    if p_model_source == "dc":
        p_final = model_weight * p_model + (1.0 - model_weight) * p_market
    else:
        p_final = p_market
    ev_final = p_final * float(odds_over) - 1.0

    # -- Odds band ------------------------------------------------------------
    ov = float(odds_over)  # reuse local var
    if ov < 1.50:
        odds_band = "<1.50"
    elif ov < 1.70:
        odds_band = "1.50–1.70"
    elif ov < 2.00:
        odds_band = "1.70–2.00"
    elif ov <= 2.50:
        odds_band = "2.00–2.50"
    else:
        odds_band = ">2.50"

    return {
        "p_model_source": p_model_source,
        "p_dc_raw": round(p_dc_raw, 6) if not (p_dc_raw != p_dc_raw) else None,
        "p_model": round(p_model, 6),
        "p_market": round(p_market, 6),
        "p_market_source": p_market_source,
        "p_final": round(p_final, 6),
        "ev_final": round(ev_final, 6),
        "odds_band": odds_band,
    }


# ---------------------------------------------------------------------------
# String normalisation
# ---------------------------------------------------------------------------


def normalize_team_names(name: str) -> str:
    """
    Normalise a team name to a canonical lowercase ASCII string.

    Steps applied in order:
    1. Strip leading/trailing whitespace.
    2. Lowercase.
    3. Strip Unicode accents (NFD decomposition → remove Mn category).
    4. Replace common abbreviations (see ``_TEAM_ABBREVS``).
    5. Remove punctuation characters except ``-`` and spaces.
    6. Collapse multiple spaces.

    Parameters
    ----------
    name:
        Raw team name string (any language / casing).

    Returns
    -------
    str
        Canonical lowercase ASCII team name.

    Examples
    --------
    >>> normalize_team_names("Man City")
    'manchester city'
    >>> normalize_team_names("Saint-Étienne")
    'saint-etienne'
    >>> normalize_team_names("FC Bayern München")
    'fc bayern munchen'
    """
    if not name or not isinstance(name, str):
        return ""

    s = name.strip().lower()

    # Remove accents
    nfd = unicodedata.normalize("NFD", s)
    s = "".join(c for c in nfd if unicodedata.category(c) != "Mn")

    # Abbreviation lookup (after accent removal)
    if s in _TEAM_ABBREVS:
        return _TEAM_ABBREVS[s]

    # Remove punctuation except hyphen and space
    s = re.sub(r"[^\w\s\-]", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()

    # Check abbreviation again after cleaning
    return _TEAM_ABBREVS.get(s, s)


# ---------------------------------------------------------------------------
# Pick enrichment
# ---------------------------------------------------------------------------


def enrich_picks(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add derived columns to a validated picks DataFrame.

    New columns added
    -----------------
    ev_pct : float
        Expected Value percentage per unit staked.
        EV% = (prob_over25/100 × odds_over - 1) × 100

    half_kelly : float
        Half-Kelly staking fraction.
        Kelly f* = (p × b - q) / b, where b = odds_over − 1.
        half_kelly = f* / 2, clipped at [0, 1].

    is_value_bet : bool
        True when EV% > 0 (i.e. model edge beats implied odds).

    score_tier : str
        Categorical tier based on score_sistema:
        - 'low'    : score < 45
        - 'medium' : 45 ≤ score < 65
        - 'high'   : score ≥ 65

    movement_sharp_combo : str
        Concatenation of ``movimento`` and ``sharp_label`` for
        interaction analysis, e.g. ``'SHORTENING_STEAM'``.
        Empty components are replaced with ``'NONE'``.

    Parameters
    ----------
    df:
        Clean picks DataFrame from ``validate_picks``.

    Returns
    -------
    pd.DataFrame
        Original DataFrame with additional columns appended.
    """
    df = df.copy()

    # --- Numeric inputs (coerce to float for safety) -----------------------
    prob = pd.to_numeric(df.get("prob_over25", pd.Series(dtype=float)), errors="coerce") / 100.0
    odds = pd.to_numeric(df.get("odds_over", pd.Series(dtype=float)), errors="coerce")
    score = pd.to_numeric(df.get("score_sistema", pd.Series(dtype=float)), errors="coerce")

    # --- EV% ----------------------------------------------------------------
    # EV% = (p × odds − 1) × 100  (equivalent to implied edge %)
    implied_prob = 1.0 / odds  # bookmaker's implied probability
    ev_pct = (prob * odds - 1.0) * 100.0
    df["ev_pct"] = ev_pct.round(4)

    # --- Kelly --------------------------------------------------------------
    b = odds - 1.0  # net odds (e.g. 0.9 for 1.90)
    q = 1.0 - prob
    kelly_full = (prob * b - q) / b
    kelly_full = kelly_full.clip(lower=0.0, upper=1.0)
    df["half_kelly"] = (kelly_full / 2.0).round(6)

    # --- is_value_bet -------------------------------------------------------
    df["is_value_bet"] = df["ev_pct"] > _EV_THRESHOLD

    # --- score_tier ---------------------------------------------------------
    def _score_tier(s: float) -> str:
        if pd.isna(s):
            return "unknown"
        if s < 45:
            return "low"
        if s < 65:
            return "medium"
        return "high"

    df["score_tier"] = score.apply(_score_tier)

    # --- movement_sharp_combo -----------------------------------------------
    mov = df.get("movimento", pd.Series("", index=df.index)).fillna("NONE").astype(str)
    sharp = df.get("sharp_label", pd.Series("", index=df.index)).fillna("NONE").astype(str)
    mov = mov.replace("", "NONE")
    sharp = sharp.replace("", "NONE")
    df["movement_sharp_combo"] = mov + "_" + sharp

    # --- Blended market probability -----------------------------------------
    odds_under_col = df.get("odds_under", pd.Series(dtype=float))
    blend_results = []
    for idx in df.index:
        p25 = pd.to_numeric(df.at[idx, "prob_over25"] if "prob_over25" in df.columns else None, errors="coerce")
        ov = pd.to_numeric(df.at[idx, "odds_over"] if "odds_over" in df.columns else None, errors="coerce")
        ou_raw = odds_under_col.get(idx) if hasattr(odds_under_col, "get") else None
        ou = pd.to_numeric(ou_raw, errors="coerce") if ou_raw is not None else None

        if pd.notna(p25) and pd.notna(ov) and ov > 1.0:
            blend_results.append(compute_final_probability(float(p25), float(ov), float(ou) if pd.notna(ou) else None))
        else:
            blend_results.append({
                "p_model": float("nan"),
                "p_market": float("nan"),
                "p_market_source": "unavailable",
                "p_final": float("nan"),
                "ev_final": float("nan"),
            })

    blend_df = pd.DataFrame(blend_results, index=df.index)
    for col in blend_df.columns:
        df[col] = blend_df[col]

    logger.debug(
        "enrich_picks: added ev_pct, half_kelly, is_value_bet, score_tier, "
        "movement_sharp_combo, p_model, p_market, p_final, ev_final to %d rows",
        len(df),
    )
    return df


# ---------------------------------------------------------------------------
# Form features
# ---------------------------------------------------------------------------


def compute_form_features(
    matches_df: pd.DataFrame,
    team: str,
    n: int = 5,
) -> dict:
    """
    Compute recent form statistics for a single team.

    Searches ``matches_df`` for the last ``n`` games where ``team`` appears
    as home or away.  Results are computed from the perspective of the
    requested team (i.e. goals scored = goals for that team).

    Parameters
    ----------
    matches_df:
        Historical match DataFrame.  Expected columns:
        ``home``, ``away``, ``goals_home``, ``goals_away``, ``date``
        (and optionally ``over_25``).  Column names should match the
        output of ``extract.fetch_football_data``.
    team:
        Team name (normalised or raw — normalisation is applied internally).
    n:
        Number of most-recent games to consider.

    Returns
    -------
    dict with keys:
        goals_scored_avg, goals_conceded_avg, over25_rate, btts_rate,
        n_games (actual number of games found, ≤ n)
    """
    if matches_df.empty:
        return _empty_form()

    team_norm = normalize_team_names(team)

    # Normalise team columns for matching
    if "home" not in matches_df.columns or "away" not in matches_df.columns:
        logger.warning("compute_form_features: matches_df missing 'home'/'away' columns")
        return _empty_form()

    df = matches_df.copy()
    df["_home_norm"] = df["home"].astype(str).apply(normalize_team_names)
    df["_away_norm"] = df["away"].astype(str).apply(normalize_team_names)

    team_mask = (df["_home_norm"] == team_norm) | (df["_away_norm"] == team_norm)
    team_games = df[team_mask].copy()

    if "date" in team_games.columns:
        team_games = team_games.sort_values("date", ascending=False)

    team_games = team_games.head(n)

    if team_games.empty:
        logger.debug("compute_form_features: no games found for team '%s'", team)
        return _empty_form()

    gh = pd.to_numeric(team_games.get("goals_home", pd.Series(dtype=float)), errors="coerce")
    ga = pd.to_numeric(team_games.get("goals_away", pd.Series(dtype=float)), errors="coerce")

    # Goals scored / conceded from team perspective
    is_home = team_games["_home_norm"] == team_norm
    goals_scored = np.where(is_home, gh, ga)
    goals_conceded = np.where(is_home, ga, gh)

    total_goals = gh + ga
    over25 = (total_goals > 2).astype(float)
    btts = ((gh > 0) & (ga > 0)).astype(float)

    n_actual = len(team_games)
    return {
        "goals_scored_avg": round(float(np.nanmean(goals_scored)), 4),
        "goals_conceded_avg": round(float(np.nanmean(goals_conceded)), 4),
        "over25_rate": round(float(np.nanmean(over25)), 4),
        "btts_rate": round(float(np.nanmean(btts)), 4),
        "n_games": n_actual,
    }


def _empty_form() -> dict:
    return {
        "goals_scored_avg": float("nan"),
        "goals_conceded_avg": float("nan"),
        "over25_rate": float("nan"),
        "btts_rate": float("nan"),
        "n_games": 0,
    }


# ---------------------------------------------------------------------------
# Calibrated probability
# ---------------------------------------------------------------------------


def add_calibrated_prob(
    df: pd.DataFrame,
    calibrator: Any,
) -> pd.DataFrame:
    """
    Apply a fitted probability calibrator to the ``prob_over25`` column.

    The calibrator must implement the scikit-learn estimator interface
    (i.e. have a ``predict_proba`` method returning an (N, 2) array where
    column 1 is P(over 2.5)).

    Parameters
    ----------
    df:
        Clean picks DataFrame with ``prob_over25`` column (0-100 scale).
    calibrator:
        Fitted calibrator object (e.g. ``CalibratedClassifierCV``,
        ``IsotonicRegression``, or custom wrapper).

    Returns
    -------
    pd.DataFrame
        Original DataFrame with an additional ``prob_over25_calibrated``
        column (0-100 scale) and ``calibration_delta`` = calibrated − raw.
    """
    df = df.copy()
    raw = pd.to_numeric(df["prob_over25"], errors="coerce") / 100.0
    raw_filled = raw.fillna(0.5)

    try:
        if hasattr(calibrator, "predict_proba"):
            cal_probs = calibrator.predict_proba(raw_filled.values.reshape(-1, 1))[:, 1]
        elif hasattr(calibrator, "transform"):
            cal_probs = calibrator.transform(raw_filled.values)
        elif callable(calibrator):
            cal_probs = np.array([calibrator(p) for p in raw_filled.values])
        else:
            raise TypeError(
                f"calibrator must have predict_proba/transform or be callable; "
                f"got {type(calibrator)}"
            )

        cal_probs = np.clip(cal_probs, 0.0, 1.0)
        df["prob_over25_calibrated"] = np.where(raw.isna(), np.nan, cal_probs * 100.0)
        df["calibration_delta"] = df["prob_over25_calibrated"] - df["prob_over25"]
        logger.info(
            "add_calibrated_prob: applied calibration to %d rows; "
            "mean delta = %.4f%%",
            len(df),
            df["calibration_delta"].mean(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("add_calibrated_prob: calibrator failed: %s", exc)
        df["prob_over25_calibrated"] = df["prob_over25"]
        df["calibration_delta"] = 0.0

    return df


# ---------------------------------------------------------------------------
# Feature matrix
# ---------------------------------------------------------------------------


def create_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """
    Select and scale features for model training / evaluation.

    Features used (in order):
        prob_over25    — model probability (0-100, divided to 0-1)
        xg_total       — total expected goals
        btts_prob      — BTTS probability (0-100, divided to 0-1)
        div            — Pinnacle/recreational divergence %
        score_sistema  — composite score (0-100, divided to 0-1)
        has_sharp      — binary flag (0/1)
        is_shortening  — 1 if movimento == 'SHORTENING', else 0

    All features are normalised to approximately [0, 1] range by dividing
    by their natural maxima.  Missing values are imputed with column medians.

    Parameters
    ----------
    df:
        Enriched picks DataFrame (output of ``enrich_picks`` recommended).

    Returns
    -------
    pd.DataFrame
        Float64 DataFrame with columns corresponding to ``_FEATURE_COLS``
        (``is_shortening`` replaces ``movimento``), same row count as input.
        Index is preserved.
    """
    df = df.copy()

    # Build is_shortening from movimento
    if "movimento" in df.columns:
        df["is_shortening"] = (df["movimento"] == "SHORTENING").astype(float)
    else:
        df["is_shortening"] = 0.0

    feature_df = pd.DataFrame(index=df.index)

    # prob_over25: 0-100 → 0-1
    feature_df["prob_over25"] = (
        pd.to_numeric(df.get("prob_over25", pd.Series(dtype=float)), errors="coerce") / 100.0
    )

    # xg_total: typical range 1-6; normalise by 6.0
    feature_df["xg_total"] = (
        pd.to_numeric(df.get("xg_total", pd.Series(dtype=float)), errors="coerce") / 6.0
    )

    # btts_prob: 0-100 → 0-1
    feature_df["btts_prob"] = (
        pd.to_numeric(df.get("btts_prob", pd.Series(dtype=float)), errors="coerce") / 100.0
    )

    # div: typical range 0-20; normalise by 20.0
    feature_df["div"] = (
        pd.to_numeric(df.get("div", pd.Series(dtype=float)), errors="coerce") / 20.0
    )

    # score_sistema: 0-100 → 0-1
    feature_df["score_sistema"] = (
        pd.to_numeric(df.get("score_sistema", pd.Series(dtype=float)), errors="coerce") / 100.0
    )

    # has_sharp: already 0/1
    feature_df["has_sharp"] = (
        pd.to_numeric(df.get("has_sharp", pd.Series(dtype=float)), errors="coerce").clip(0, 1)
    )

    # is_shortening: already 0/1
    feature_df["is_shortening"] = df["is_shortening"].astype(float)

    # Clip scaled values to [0, 1] to catch outliers
    for col in ["prob_over25", "xg_total", "btts_prob", "div", "score_sistema"]:
        feature_df[col] = feature_df[col].clip(lower=0.0, upper=2.0)  # generous upper for xg

    # Impute missing values with column medians
    for col in feature_df.columns:
        if feature_df[col].isna().any():
            median_val = feature_df[col].median()
            if pd.isna(median_val):
                median_val = 0.0
            n_imputed = feature_df[col].isna().sum()
            feature_df[col] = feature_df[col].fillna(median_val)
            logger.debug(
                "create_feature_matrix: imputed %d NaN in '%s' with median=%.4f",
                n_imputed,
                col,
                median_val,
            )

    return feature_df.astype(np.float64)
