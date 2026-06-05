"""
Data extraction layer.

All public functions:
- Return an empty list / empty DataFrame on any error (logged at WARNING/ERROR).
- Accept an optional explicit path / URL parameter; fall back to config
  defaults when not provided.
- Do NOT raise exceptions to callers — the pipeline continues even if one
  source fails.

External dependencies (must be installed):
    requests  — HTTP client
    pandas    — DataFrame operations
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BSD_BASE_URL = "https://sports.bzzoiro.com"
BSD_EVENTS_PATH = "/events/"
BSD_REQUEST_TIMEOUT = 30  # seconds

GAS_REQUEST_TIMEOUT = 20

# football-data.co.uk base URL template
# e.g. https://www.football-data.co.uk/mmz4281/2324/E0.csv
FD_BASE = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

# Canonical column mapping from football-data.co.uk to internal schema
_FD_COLUMN_MAP: dict[str, str] = {
    "Div": "league_code",
    "Date": "date",
    "Time": "time",
    "HomeTeam": "home",
    "AwayTeam": "away",
    "FTHG": "goals_home",
    "FTAG": "goals_away",
    "FTR": "result",
    "HTHG": "ht_goals_home",
    "HTAG": "ht_goals_away",
    "HTR": "ht_result",
    "HS": "shots_home",
    "AS": "shots_away",
    "HST": "shots_on_target_home",
    "AST": "shots_on_target_away",
    "HF": "fouls_home",
    "AF": "fouls_away",
    "HC": "corners_home",
    "AC": "corners_away",
    "HY": "yellow_home",
    "AY": "yellow_away",
    "HR": "red_home",
    "AR": "red_away",
    # Pinnacle odds (when present)
    "PSH": "pinn_home",
    "PSD": "pinn_draw",
    "PSA": "pinn_away",
    "PSCO": "pinn_over25",
    "PSCU": "pinn_under25",
    # Bet365 odds
    "B365H": "b365_home",
    "B365D": "b365_draw",
    "B365A": "b365_away",
    "B365>2.5": "b365_over25",
    "B365<2.5": "b365_under25",
}


# ---------------------------------------------------------------------------
# Local picks
# ---------------------------------------------------------------------------


def load_picks_local(path: Optional[Path] = None) -> list[dict]:
    """
    Load picks from the local ``data/picks.json`` file.

    Parameters
    ----------
    path:
        Explicit path to a JSON picks file.  If ``None``, attempts to auto-
        locate ``data/picks.json`` relative to this file's project root.

    Returns
    -------
    list[dict]
        Parsed records, or ``[]`` on any error.
    """
    if path is None:
        # Walk up to project root (contains CLAUDE.md or index.html)
        here = Path(__file__).resolve().parent
        for candidate in [here.parent / "data", here / "data"]:
            p = candidate / "picks.json"
            if p.exists():
                path = p
                break

    if path is None or not Path(path).exists():
        logger.error(
            "load_picks_local: picks.json not found (searched: %s)", path
        )
        return []

    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            logger.error(
                "load_picks_local: expected a JSON array, got %s", type(data).__name__
            )
            return []
        logger.info("load_picks_local: loaded %d records from %s", len(data), path)
        return data
    except json.JSONDecodeError as exc:
        logger.error("load_picks_local: JSON parse error in %s: %s", path, exc)
        return []
    except OSError as exc:
        logger.error("load_picks_local: cannot read %s: %s", path, exc)
        return []


# ---------------------------------------------------------------------------
# Google Apps Script (GAS)
# ---------------------------------------------------------------------------


def load_picks_from_gas(sheet_url: str) -> list[dict]:
    """
    Fetch picks from a Google Apps Script endpoint via HTTP GET.

    The GAS handler is expected to return a JSON array of pick objects
    (same schema as picks.json).  A ``?action=get`` query parameter is
    appended automatically.

    Parameters
    ----------
    sheet_url:
        Full GAS web-app URL, e.g.
        ``https://script.google.com/macros/s/AKfycb.../exec``.

    Returns
    -------
    list[dict]
        Parsed records, or ``[]`` on any error.
    """
    if not sheet_url:
        logger.error("load_picks_from_gas: sheet_url is empty")
        return []

    url = sheet_url.rstrip("/")
    params = {"action": "get"}

    try:
        resp = requests.get(
            url,
            params=params,
            timeout=GAS_REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("load_picks_from_gas: request timed out after %ds", GAS_REQUEST_TIMEOUT)
        return []
    except requests.exceptions.HTTPError as exc:
        logger.error("load_picks_from_gas: HTTP error %s", exc)
        return []
    except requests.exceptions.RequestException as exc:
        logger.error("load_picks_from_gas: request failed: %s", exc)
        return []

    try:
        payload = resp.json()
    except ValueError:
        # GAS sometimes returns HTML error pages; log first 200 chars
        logger.error(
            "load_picks_from_gas: non-JSON response (first 200 chars): %s",
            resp.text[:200],
        )
        return []

    # GAS may wrap data in an envelope: {status: "ok", data: [...]}
    if isinstance(payload, dict):
        data = payload.get("data") or payload.get("picks") or payload.get("records")
        if not isinstance(data, list):
            logger.error(
                "load_picks_from_gas: unexpected envelope shape: %s",
                list(payload.keys()),
            )
            return []
        payload = data

    if not isinstance(payload, list):
        logger.error(
            "load_picks_from_gas: expected a list, got %s", type(payload).__name__
        )
        return []

    logger.info("load_picks_from_gas: fetched %d records from GAS", len(payload))
    return payload


# ---------------------------------------------------------------------------
# BSD Sports API
# ---------------------------------------------------------------------------


def fetch_bsd_events(
    api_key: str,
    date: Optional[str] = None,
    timeout: int = BSD_REQUEST_TIMEOUT,
) -> list[dict]:
    """
    Fetch soccer events from the BSD Sports API.

    Endpoint: ``GET https://sports.bzzoiro.com/events/``

    Parameters
    ----------
    api_key:
        BSD API key (passed as ``x-api-key`` header).
    date:
        ISO-8601 date string ``'YYYY-MM-DD'`` to filter events.
        If ``None``, the API default is used (typically today).
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    list[dict]
        Raw event records, or ``[]`` on any error.

    Notes
    -----
    The BSD API returns events under a top-level ``data`` or ``events``
    key; both variants are handled.
    """
    if not api_key:
        logger.error("fetch_bsd_events: api_key is empty")
        return []

    url = urljoin(BSD_BASE_URL, BSD_EVENTS_PATH)
    headers = {
        "x-api-key": api_key,
        "Accept": "application/json",
    }
    params: dict = {}
    if date:
        params["date"] = date

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("fetch_bsd_events: request timed out after %ds", timeout)
        return []
    except requests.exceptions.HTTPError as exc:
        logger.error(
            "fetch_bsd_events: HTTP %s error for URL %s", exc.response.status_code, url
        )
        return []
    except requests.exceptions.RequestException as exc:
        logger.error("fetch_bsd_events: request failed: %s", exc)
        return []

    try:
        payload = resp.json()
    except ValueError:
        logger.error(
            "fetch_bsd_events: non-JSON response (first 200 chars): %s",
            resp.text[:200],
        )
        return []

    # Unwrap common envelope shapes
    if isinstance(payload, dict):
        events = (
            payload.get("data")
            or payload.get("events")
            or payload.get("results")
            or []
        )
    elif isinstance(payload, list):
        events = payload
    else:
        logger.error(
            "fetch_bsd_events: unexpected payload type %s", type(payload).__name__
        )
        return []

    if not isinstance(events, list):
        logger.error(
            "fetch_bsd_events: events is not a list (type: %s)", type(events).__name__
        )
        return []

    logger.info(
        "fetch_bsd_events: fetched %d events from BSD API (date=%s)",
        len(events),
        date or "default",
    )
    return events


# ---------------------------------------------------------------------------
# football-data.co.uk
# ---------------------------------------------------------------------------

# Map league names (as used in picks.json) to football-data.co.uk file codes
LEAGUE_CODE_MAP: dict[str, str] = {
    "Premier League": "E0",
    "Championship": "E1",
    "League One": "E2",
    "League Two": "E3",
    "La Liga": "SP1",
    "La Liga 2": "SP2",
    "Bundesliga": "D1",
    "Bundesliga 2": "D2",
    "Serie A": "I1",
    "Serie B": "I2",
    "Ligue 1": "F1",
    "Ligue 2": "F2",
    "Eredivisie": "N1",
    "Primeira Liga": "P1",
    "Super Lig": "T1",
    "Belgian Pro League": "B1",
    "Scottish Premiership": "SC0",
}


def fetch_football_data(
    league_code: str,
    season: str,
    timeout: int = 30,
) -> pd.DataFrame:
    """
    Fetch historical match data from football-data.co.uk.

    Parameters
    ----------
    league_code:
        Either a league name (e.g. ``'Premier League'``) which is mapped
        to the file code, or a direct file code (e.g. ``'E0'``).
    season:
        Season string in ``'YYYYYYY'`` two-year format, e.g. ``'2324'``
        for the 2023/24 season.  Four-digit years are also supported:
        ``'2324'`` or ``'2023'`` both map to the same season file.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    pd.DataFrame
        Match records with standardised column names (see ``_FD_COLUMN_MAP``).
        Returns an empty DataFrame on any error.

    Examples
    --------
    >>> df = fetch_football_data("Premier League", "2324")
    >>> df.columns.tolist()[:4]
    ['league_code', 'date', 'home', 'away', ...]
    """
    # Resolve league name → file code
    code = LEAGUE_CODE_MAP.get(league_code, league_code)

    # Normalise season: accept '2023/24' → '2324', '2023-24' → '2324'
    season_clean = season.replace("/", "").replace("-", "").replace(" ", "")
    if len(season_clean) == 4 and season_clean.isdigit():
        # Full year '2023' → '2324'
        yr = int(season_clean)
        season_clean = f"{str(yr)[2:]}{str(yr + 1)[2:]}"

    url = FD_BASE.format(season=season_clean, code=code)

    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        logger.error("fetch_football_data: request timed out for %s", url)
        return pd.DataFrame()
    except requests.exceptions.HTTPError as exc:
        logger.error(
            "fetch_football_data: HTTP %s for %s", exc.response.status_code, url
        )
        return pd.DataFrame()
    except requests.exceptions.RequestException as exc:
        logger.error("fetch_football_data: request failed for %s: %s", url, exc)
        return pd.DataFrame()

    try:
        from io import StringIO

        df = pd.read_csv(StringIO(resp.text), on_bad_lines="skip")
    except Exception as exc:  # noqa: BLE001
        logger.error("fetch_football_data: CSV parse error for %s: %s", url, exc)
        return pd.DataFrame()

    if df.empty:
        logger.warning("fetch_football_data: empty CSV from %s", url)
        return pd.DataFrame()

    # Drop rows that are entirely NaN (common at end of football-data files)
    df.dropna(how="all", inplace=True)

    # Rename columns to internal standard names
    rename_map = {k: v for k, v in _FD_COLUMN_MAP.items() if k in df.columns}
    df.rename(columns=rename_map, inplace=True)

    # Parse date
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], dayfirst=True, errors="coerce")

    # Add computed column: over_25 outcome (1 if total goals > 2, else 0)
    if "goals_home" in df.columns and "goals_away" in df.columns:
        gh = pd.to_numeric(df["goals_home"], errors="coerce")
        ga = pd.to_numeric(df["goals_away"], errors="coerce")
        df["over_25"] = ((gh + ga) > 2).astype("Int8")
        df["goals_total"] = (gh + ga).astype("Int8")

    logger.info(
        "fetch_football_data: fetched %d matches for %s %s from %s",
        len(df),
        league_code,
        season_clean,
        url,
    )
    return df
