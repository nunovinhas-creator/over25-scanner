"""
Pipeline configuration.

All runtime settings are held in a single ``Config`` dataclass.
Values are read from environment variables first; a ``.env`` file in the
project root is used as a fallback so that local development works without
exporting shell variables.

Usage
-----
    from pipeline.config import load_config
    cfg = load_config()
    print(cfg.PICKS_FILE)
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# .env loader (stdlib-only fallback; python-dotenv used when available)
# ---------------------------------------------------------------------------


def _load_dotenv(dotenv_path: Path) -> None:
    """
    Minimal .env parser — only runs if python-dotenv is not installed.

    Handles:
    - ``KEY=VALUE`` pairs (unquoted or single/double-quoted values)
    - Lines starting with ``#`` are comments
    - Blank lines are ignored
    - Does NOT overwrite already-set environment variables
    """
    if not dotenv_path.exists():
        return

    try:
        import dotenv  # type: ignore

        dotenv.load_dotenv(dotenv_path, override=False)
        return
    except ImportError:
        pass  # fall through to manual parser

    with dotenv_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Supported leagues
# ---------------------------------------------------------------------------

_DEFAULT_LEAGUES: list[str] = [
    # Top 5 European leagues
    "Premier League",
    "La Liga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
    # Additional high-liquidity leagues
    "Eredivisie",
    "Primeira Liga",
    "Championship",
    "Super Lig",
    "Belgian Pro League",
    "Scottish Premiership",
    # International / continental
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Conference League",
]


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """
    Runtime configuration for the Over 2.5 scanner pipeline.

    All path attributes are resolved to absolute ``pathlib.Path`` objects
    so they work regardless of the caller's working directory.

    Parameters
    ----------
    BSD_API_KEY:
        API key for the BSD Sports API (https://sports.bzzoiro.com).
        Required for ``extract.fetch_bsd_events``.
    GAS_SHEET_URL:
        Google Apps Script endpoint URL for reading / writing picks.
        Required for ``extract.load_picks_from_gas``.
    DATA_DIR:
        Root directory for all data artefacts (default: ``<project>/data``).
    MODELS_DIR:
        Directory where trained model artefacts are saved
        (default: ``<project>/models``).
    PICKS_FILE:
        Path to the canonical local picks JSON file
        (default: ``DATA_DIR / "picks.json"``).
    LEAGUES:
        Whitelist of league strings to retain during filtering.
    MIN_SCORE:
        Minimum system score (0-100) for a pick to be considered.
    MIN_PROB:
        Minimum Over 2.5 model probability (0-1) for a pick.
    MAX_ODDS:
        Maximum acceptable opening odds for Over 2.5.
    MIN_ODDS:
        Minimum acceptable opening odds for Over 2.5 (avoid extreme
        favourites with tiny edge).
    """

    BSD_API_KEY: str = ""
    GAS_SHEET_URL: str = ""

    # Directories
    DATA_DIR: Path = field(default_factory=lambda: _project_root() / "data")
    MODELS_DIR: Path = field(default_factory=lambda: _project_root() / "models")

    # Files (resolved lazily — after DATA_DIR is set — in __post_init__)
    PICKS_FILE: Path = field(init=False)

    # Domain filters
    LEAGUES: list[str] = field(default_factory=lambda: list(_DEFAULT_LEAGUES))
    MIN_SCORE: int = 45
    MIN_PROB: float = 0.50
    MAX_ODDS: float = 3.5
    MIN_ODDS: float = 1.3

    def __post_init__(self) -> None:
        # Resolve paths
        self.DATA_DIR = Path(self.DATA_DIR).resolve()
        self.MODELS_DIR = Path(self.MODELS_DIR).resolve()
        self.PICKS_FILE = self.DATA_DIR / "picks.json"

        # Validate numeric thresholds
        if not 0 <= self.MIN_SCORE <= 100:
            raise ValueError(f"MIN_SCORE must be in [0, 100], got {self.MIN_SCORE}")
        if not 0.0 <= self.MIN_PROB <= 1.0:
            raise ValueError(f"MIN_PROB must be in [0, 1], got {self.MIN_PROB}")
        if self.MIN_ODDS <= 1.0:
            raise ValueError(f"MIN_ODDS must be > 1.0, got {self.MIN_ODDS}")
        if self.MAX_ODDS <= self.MIN_ODDS:
            raise ValueError(
                f"MAX_ODDS ({self.MAX_ODDS}) must be greater than MIN_ODDS ({self.MIN_ODDS})"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root() -> Path:
    """
    Resolve the project root directory.

    Searches upward from this file's location for the first directory that
    contains a ``CLAUDE.md`` or ``index.html`` marker file.  Falls back to
    the grandparent of this file (``pipeline/config.py`` → project root).
    """
    here = Path(__file__).resolve().parent  # pipeline/
    for candidate in [here.parent, here.parent.parent]:
        if (candidate / "CLAUDE.md").exists() or (candidate / "index.html").exists():
            return candidate
    return here.parent  # best-effort fallback


def _setup_logging(log_dir: Optional[Path] = None) -> None:
    """
    Configure root logger with:
    - Coloured StreamHandler to stderr (DEBUG+ in dev, INFO+ otherwise)
    - RotatingFileHandler writing to ``log_dir/pipeline.log``
      (max 5 MB × 3 backups)
    """
    root = logging.getLogger()
    if root.handlers:
        return  # already configured

    level_env = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_env, logging.INFO)
    root.setLevel(logging.DEBUG)  # let handlers filter

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # --- stderr handler ---
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    # --- rotating file handler ---
    if log_dir is None:
        log_dir = _project_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "pipeline.log"
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        root.warning("Could not create rotating log file at %s: %s", log_file, exc)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def load_config(dotenv_path: Optional[Path] = None) -> Config:
    """
    Build and return a ``Config`` instance from environment variables.

    Resolution order:
    1. Shell environment variables (highest priority).
    2. ``.env`` file at ``dotenv_path`` (or ``<project_root>/.env`` if not
       specified).
    3. Dataclass defaults (lowest priority).

    Parameters
    ----------
    dotenv_path:
        Explicit path to a ``.env`` file.  ``None`` → auto-detect.

    Returns
    -------
    Config
    """
    root = _project_root()

    if dotenv_path is None:
        dotenv_path = root / ".env"
    _load_dotenv(dotenv_path)

    cfg = Config(
        BSD_API_KEY=os.environ.get("BSD_API_KEY", ""),
        GAS_SHEET_URL=os.environ.get("GAS_SHEET_URL", ""),
        DATA_DIR=Path(os.environ.get("DATA_DIR", root / "data")),
        MODELS_DIR=Path(os.environ.get("MODELS_DIR", root / "models")),
        MIN_SCORE=int(os.environ.get("MIN_SCORE", 45)),
        MIN_PROB=float(os.environ.get("MIN_PROB", 0.50)),
        MAX_ODDS=float(os.environ.get("MAX_ODDS", 3.5)),
        MIN_ODDS=float(os.environ.get("MIN_ODDS", 1.3)),
    )

    _setup_logging(cfg.DATA_DIR.parent / "logs")
    return cfg
