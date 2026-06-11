"""
ETL orchestration for the Over 2.5 scanner pipeline.

Entry points
------------
- ``run_etl(config)``       — full pipeline: extract → validate → transform → save
- ``update_picks_file(...)`` — merge new picks into local picks.json
- ``export_calibration_params(...)`` — serialize calibrator to JSON
- ``generate_daily_summary(...)`` — today's key metrics dict

CLI usage (from project root)::

    python -m pipeline.etl --extract        # extract + validate only
    python -m pipeline.etl --validate       # validate existing picks.json
    python -m pipeline.etl --transform      # validate + transform (feature matrix)
    python -m pipeline.etl --full           # full pipeline
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports (avoid importing heavy modules at import time)
# ---------------------------------------------------------------------------


def _get_config_class():
    from pipeline.config import Config, load_config  # noqa: PLC0415

    return Config, load_config


def _get_extract():
    from pipeline.extract import (  # noqa: PLC0415
        fetch_bsd_events,
        load_picks_from_gas,
        load_picks_local,
    )

    return load_picks_local, load_picks_from_gas, fetch_bsd_events


def _get_schema():
    from data.schema.picks_schema import validate_picks  # noqa: PLC0415
    from data.schema.bsd_schema import validate_bsd_events  # noqa: PLC0415

    return validate_picks, validate_bsd_events


def _get_transform():
    from pipeline.transform import enrich_picks, create_feature_matrix  # noqa: PLC0415

    return enrich_picks, create_feature_matrix


# ---------------------------------------------------------------------------
# Core ETL functions
# ---------------------------------------------------------------------------


def update_picks_file(
    new_picks: list[dict],
    existing_path: Path,
) -> int:
    """
    Merge new pick records into the existing picks.json file.

    Deduplication is performed on the ``id`` field.  If a new pick has the
    same ``id`` as an existing pick, the existing record is kept unchanged
    (source-of-truth is the sheet).

    Parameters
    ----------
    new_picks:
        List of raw pick dicts to merge in.
    existing_path:
        Path to the picks.json file (created if it does not exist).

    Returns
    -------
    int
        Number of new records actually added (0 if all were duplicates).
    """
    existing_path = Path(existing_path)

    # Load existing records
    if existing_path.exists():
        try:
            with existing_path.open("r", encoding="utf-8") as fh:
                existing: list[dict] = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(
                "update_picks_file: cannot read existing file %s: %s",
                existing_path,
                exc,
            )
            existing = []
    else:
        existing = []
        existing_path.parent.mkdir(parents=True, exist_ok=True)

    # Build existing ID set
    existing_ids: set[str] = {str(r.get("id", "")) for r in existing if r.get("id")}

    # Filter to only truly new records
    to_add = [r for r in new_picks if str(r.get("id", "")) not in existing_ids]
    n_added = len(to_add)

    if n_added == 0:
        logger.info(
            "update_picks_file: no new records to add (%d duplicates skipped)",
            len(new_picks),
        )
        return 0

    merged = existing + to_add

    try:
        with existing_path.open("w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2, default=str)
    except OSError as exc:
        logger.error("update_picks_file: cannot write to %s: %s", existing_path, exc)
        return 0

    logger.info(
        "update_picks_file: added %d new records; total now %d in %s",
        n_added,
        len(merged),
        existing_path,
    )
    return n_added


def export_calibration_params(calibrator: Any, path: Path) -> None:
    """
    Serialize a fitted calibrator to a JSON file.

    Handles two common calibrator types:
    - ``sklearn.isotonic.IsotonicRegression`` — stores X/y thresholds.
    - ``sklearn.linear_model.LogisticRegression`` (Platt scaling) — stores
      coef and intercept.
    - Generic callable or unknown: stores ``{'type': ..., 'repr': ...}`` for
      debugging; warn that it cannot be deserialized automatically.

    Parameters
    ----------
    calibrator:
        Fitted calibrator object.
    path:
        Destination JSON path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    params: dict[str, Any] = {
        "exported_at": datetime.now(tz=timezone.utc).isoformat(),
        "type": type(calibrator).__name__,
        "module": getattr(type(calibrator), "__module__", "unknown"),
    }

    try:
        # IsotonicRegression
        if hasattr(calibrator, "X_thresholds_") and hasattr(calibrator, "y_thresholds_"):
            params["method"] = "isotonic"
            params["X_thresholds"] = calibrator.X_thresholds_.tolist()
            params["y_thresholds"] = calibrator.y_thresholds_.tolist()

        # Logistic (Platt) calibration
        elif hasattr(calibrator, "coef_") and hasattr(calibrator, "intercept_"):
            params["method"] = "platt"
            params["coef"] = calibrator.coef_.tolist()
            params["intercept"] = calibrator.intercept_.tolist()
            if hasattr(calibrator, "classes_"):
                params["classes"] = calibrator.classes_.tolist()

        # CalibratedClassifierCV wraps another estimator
        elif hasattr(calibrator, "calibrated_classifiers_"):
            params["method"] = "sklearn_calibrated"
            params["n_calibrators"] = len(calibrator.calibrated_classifiers_)
            params["note"] = "Full state not serializable to JSON; use joblib/pickle."

        # Unknown / custom
        else:
            params["method"] = "unknown"
            params["repr"] = repr(calibrator)[:500]
            logger.warning(
                "export_calibration_params: calibrator type '%s' has no recognized "
                "attribute pattern; stored repr only.",
                type(calibrator).__name__,
            )

    except Exception as exc:  # noqa: BLE001
        logger.error("export_calibration_params: serialization error: %s", exc)
        params["error"] = str(exc)

    with path.open("w", encoding="utf-8") as fh:
        json.dump(params, fh, ensure_ascii=False, indent=2)

    logger.info("export_calibration_params: saved to %s", path)


def generate_daily_summary(picks_df: pd.DataFrame) -> dict:
    """
    Generate a summary of today's picks statistics.

    Parameters
    ----------
    picks_df:
        Picks DataFrame from ``validate_picks`` (may include all-time picks;
        only today's are summarised).  Requires columns: ``saved_at``,
        ``movimento``, ``score_sistema``, ``ev_pct`` (or computable),
        ``half_kelly`` (or computable).

    Returns
    -------
    dict with keys:
        date, n_picks, n_shortening, n_steam, n_sharp,
        avg_score, avg_prob, avg_odds, avg_ev, avg_kelly,
        n_settled, n_win, hit_rate, roi_pct
    """
    today = date.today().isoformat()

    # Filter to today's picks
    if "saved_at" in picks_df.columns and not picks_df["saved_at"].isna().all():
        dt = pd.to_datetime(picks_df["saved_at"], errors="coerce", utc=True)
        today_mask = dt.dt.date == date.today()
        df = picks_df[today_mask].copy()
    else:
        df = picks_df.copy()

    n_picks = len(df)

    if n_picks == 0:
        return {
            "date": today,
            "n_picks": 0,
            "n_shortening": 0,
            "n_steam": 0,
            "n_sharp": 0,
            "avg_score": float("nan"),
            "avg_prob": float("nan"),
            "avg_odds": float("nan"),
            "avg_ev": float("nan"),
            "avg_kelly": float("nan"),
            "n_settled": 0,
            "n_win": 0,
            "hit_rate": float("nan"),
            "roi_pct": float("nan"),
        }

    # Movement / sharp counts
    mov = df.get("movimento", pd.Series("", index=df.index)).fillna("")
    sharp = df.get("sharp_label", pd.Series("", index=df.index)).fillna("")

    n_shortening = int((mov == "SHORTENING").sum())
    n_steam = int((mov == "STEAM").sum()) + int((sharp == "STEAM").sum())
    n_sharp = int(sharp.isin(["STEAM", "SHARP"]).sum())

    # Numeric averages
    score = pd.to_numeric(df.get("score_sistema", pd.Series(dtype=float)), errors="coerce")
    prob = pd.to_numeric(df.get("prob_over25", pd.Series(dtype=float)), errors="coerce")
    odds = pd.to_numeric(df.get("odds_over", pd.Series(dtype=float)), errors="coerce")

    avg_score = float(score.mean()) if not score.isna().all() else float("nan")
    avg_prob = float(prob.mean()) if not prob.isna().all() else float("nan")
    avg_odds = float(odds.mean()) if not odds.isna().all() else float("nan")

    # EV% and Kelly (compute if not already enriched)
    if "ev_pct" in df.columns:
        ev = pd.to_numeric(df["ev_pct"], errors="coerce")
    else:
        p = prob / 100.0
        ev = (p * odds - 1.0) * 100.0
    avg_ev = float(ev.mean()) if not ev.isna().all() else float("nan")

    if "half_kelly" in df.columns:
        kelly = pd.to_numeric(df["half_kelly"], errors="coerce")
    else:
        p = prob / 100.0
        b = odds - 1.0
        q = 1.0 - p
        kelly_full = ((p * b - q) / b).clip(lower=0.0)
        kelly = kelly_full / 2.0
    avg_kelly = float(kelly.mean()) if not kelly.isna().all() else float("nan")

    # Settlement stats
    result = df.get("result_over25", pd.Series("", index=df.index)).fillna("")
    settled_mask = result.isin(["WIN", "LOSS"])
    n_settled = int(settled_mask.sum())
    n_win = int((result == "WIN").sum())

    hit_rate = float(n_win / n_settled) if n_settled > 0 else float("nan")

    if n_settled > 0:
        settled_odds = odds[settled_mask]
        settled_result = result[settled_mask]
        pl = np.where(settled_result == "WIN", settled_odds - 1.0, -1.0)
        roi_pct = float(pl.sum() / n_settled * 100.0)
    else:
        roi_pct = float("nan")

    return {
        "date": today,
        "n_picks": n_picks,
        "n_shortening": n_shortening,
        "n_steam": n_steam,
        "n_sharp": n_sharp,
        "avg_score": round(avg_score, 2),
        "avg_prob": round(avg_prob, 2),
        "avg_odds": round(avg_odds, 4),
        "avg_ev": round(avg_ev, 4),
        "avg_kelly": round(avg_kelly, 6),
        "n_settled": n_settled,
        "n_win": n_win,
        "hit_rate": round(hit_rate, 4) if not np.isnan(hit_rate) else float("nan"),
        "roi_pct": round(roi_pct, 4) if not np.isnan(roi_pct) else float("nan"),
    }


# ---------------------------------------------------------------------------
# League whitelist filter
# ---------------------------------------------------------------------------


def filter_by_league(
    picks: list[dict],
    leagues: list[str],
    rejected_path: Path,
) -> tuple[list[dict], int]:
    """
    Split picks into whitelisted and rejected based on the 'liga' field.

    Picks with an empty 'liga' or a liga not in the whitelist are written to
    ``rejected_path`` (merged with any existing content, deduplicated by id)
    with a ``reject_reason`` field for auditability.  They are NOT passed to
    the main pipeline — DRIFTING picks that slipped through with bad liga values
    (e.g. Angola-Mauritânia, Singapura-China) are excluded here.

    Parameters
    ----------
    picks:
        Raw pick dicts (from local file or GAS).
    leagues:
        Whitelist of accepted league names (must match picks['liga'] exactly).
    rejected_path:
        Path to write rejected picks JSON.  Created if absent; merged if present.

    Returns
    -------
    tuple[list[dict], int]
        (accepted_picks, n_rejected)
    """
    league_set = set(leagues)
    accepted: list[dict] = []
    rejected: list[dict] = []

    for pick in picks:
        liga = str(pick.get("liga", "")).strip()
        if not liga:
            rejected.append({**pick, "reject_reason": "liga_vazia"})
        elif liga not in league_set:
            rejected.append({**pick, "reject_reason": f"liga_fora_da_whitelist:{liga}"})
        else:
            accepted.append(pick)

    if rejected:
        rejected_path = Path(rejected_path)
        existing: list[dict] = []
        if rejected_path.exists():
            try:
                with rejected_path.open("r", encoding="utf-8") as fh:
                    existing = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("filter_by_league: cannot read %s: %s", rejected_path, exc)

        existing_ids = {str(r.get("id", "")) for r in existing if r.get("id")}
        new_rejected = [r for r in rejected if str(r.get("id", "")) not in existing_ids]
        merged = existing + new_rejected

        rejected_path.parent.mkdir(parents=True, exist_ok=True)
        with rejected_path.open("w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2, default=str)

        logger.info(
            "filter_by_league: %d accepted, %d rejected (%d new) → %s",
            len(accepted),
            len(rejected),
            len(new_rejected),
            rejected_path,
        )

    return accepted, len(rejected)


# ---------------------------------------------------------------------------
# Alert candidate filter
# ---------------------------------------------------------------------------


def filter_alert_candidates(picks: list[dict]) -> list[dict]:
    """
    Return only picks that should generate a Telegram alert (pre-transform gate).

    DRIFTING picks are saved to picks.json for study purposes but never
    generate alerts.  Backtesting confirms DRIFTING adds no edge over
    SHORTENING+SHARP alone (same ROI, higher variance).

    Parameters
    ----------
    picks:
        Raw pick dicts (all movements included).

    Returns
    -------
    list[dict]
        Subset of picks where movimento != 'DRIFTING'.
    """
    return [
        p for p in picks
        if str(p.get("movimento", "")).strip().upper() != "DRIFTING"
    ]


def filter_alert_by_ev(
    enriched_df: "pd.DataFrame",
    min_ev: float,
) -> "pd.DataFrame":
    """
    Post-transform EV gate: keep only rows where ev_final >= min_ev.

    This is the final alert decision filter, applied after
    ``compute_final_probability`` has been called inside ``enrich_picks``.
    Combined with ``filter_alert_candidates`` (DRIFTING gate) and
    ``filter_by_league`` (whitelist gate), it ensures Telegram alerts
    only fire when:
      1. liga is in the production whitelist
      2. movimento != DRIFTING
      3. blended EV >= MIN_EV_ALERT (default 3%)

    Telegram message fields to include:
        p_final, p_market, ev_final, clv (if available)

    Parameters
    ----------
    enriched_df:
        DataFrame produced by ``enrich_picks`` (has ``ev_final`` column).
    min_ev:
        Minimum blended EV threshold (e.g. 0.03 = 3%).

    Returns
    -------
    pd.DataFrame
        Rows where ev_final is present and >= min_ev.
    """
    import pandas as pd

    if "ev_final" not in enriched_df.columns:
        logger.warning("filter_alert_by_ev: 'ev_final' column missing; returning empty")
        return enriched_df.iloc[0:0]

    ev = pd.to_numeric(enriched_df["ev_final"], errors="coerce")
    mask = ev >= min_ev
    result = enriched_df[mask]
    logger.info(
        "filter_alert_by_ev: %d of %d picks pass ev_final >= %.3f",
        len(result),
        len(enriched_df),
        min_ev,
    )
    return result


# ---------------------------------------------------------------------------
# Main ETL pipeline
# ---------------------------------------------------------------------------


def run_etl(config: Any) -> dict:
    """
    Execute the full ETL pipeline.

    Steps:
    1. **Extract** — load picks from local file and (optionally) GAS.
    2. **Validate** — run schema validation; log errors.
    3. **Update local** — merge any new GAS picks into picks.json.
    4. **Transform** — enrich + build feature matrix.
    5. **Save** — write feature matrix to ``<DATA_DIR>/features.parquet``.
    6. **Summarise** — return summary stats dict.

    Parameters
    ----------
    config:
        ``Config`` instance from ``pipeline.config.load_config()``.

    Returns
    -------
    dict
        Summary with keys: n_raw, n_valid, n_invalid, n_added,
        n_enriched, features_path, daily_summary.
    """
    summary: dict[str, Any] = {}

    # --- 1. Extract ---------------------------------------------------------
    load_picks_local, load_picks_from_gas, fetch_bsd_events = _get_extract()
    validate_picks, validate_bsd_events = _get_schema()
    enrich_picks_fn, create_feature_matrix_fn = _get_transform()

    logger.info("run_etl: [1/5] extract")
    local_picks = load_picks_local(config.PICKS_FILE)

    gas_picks: list[dict] = []
    if config.GAS_SHEET_URL:
        gas_picks = load_picks_from_gas(config.GAS_SHEET_URL)
    else:
        logger.debug("run_etl: GAS_SHEET_URL not configured; skipping GAS fetch")

    # Merge sources
    all_raw = local_picks.copy()
    if gas_picks:
        local_ids = {str(r.get("id", "")) for r in local_picks}
        new_from_gas = [r for r in gas_picks if str(r.get("id", "")) not in local_ids]
        all_raw.extend(new_from_gas)

    summary["n_raw"] = len(all_raw)
    logger.info("run_etl: %d raw picks (local: %d, gas_new: %d)", len(all_raw), len(local_picks), len(gas_picks))

    # --- 1b. League whitelist filter ----------------------------------------
    rejected_path = config.DATA_DIR / "rejected_picks.json"
    all_raw, n_rejected = filter_by_league(all_raw, config.LEAGUES, rejected_path)
    summary["n_rejected_league"] = n_rejected
    logger.info(
        "run_etl: [1b] league filter — %d pass, %d rejected → %s",
        len(all_raw),
        n_rejected,
        rejected_path,
    )

    # --- 1c. Alert candidates (DRIFTING excluded) ---------------------------
    alert_candidates = filter_alert_candidates(all_raw)
    summary["n_alert_candidates"] = len(alert_candidates)
    logger.info(
        "run_etl: [1c] alert candidates — %d of %d picks are non-DRIFTING",
        len(alert_candidates),
        len(all_raw),
    )

    # --- 2. Validate --------------------------------------------------------
    logger.info("run_etl: [2/5] validate")
    clean_df = validate_picks(all_raw)
    n_valid = len(clean_df)
    n_invalid = summary["n_raw"] - n_valid
    summary["n_valid"] = n_valid
    summary["n_invalid"] = n_invalid
    logger.info("run_etl: %d valid, %d invalid records", n_valid, n_invalid)

    if clean_df.empty:
        logger.error("run_etl: no valid records; aborting pipeline")
        return summary

    # --- 3. Update local file -----------------------------------------------
    logger.info("run_etl: [3/5] update local picks.json")
    n_added = 0
    if gas_picks:
        n_added = update_picks_file(gas_picks, config.PICKS_FILE)
    summary["n_added"] = n_added

    # --- 4. Transform -------------------------------------------------------
    logger.info("run_etl: [4/5] transform (enrich + feature matrix)")
    enriched_df = enrich_picks_fn(clean_df)
    feature_df = create_feature_matrix_fn(enriched_df)
    summary["n_enriched"] = len(enriched_df)
    logger.info("run_etl: enriched %d records, feature matrix shape %s", len(enriched_df), feature_df.shape)

    # --- 5. Save ------------------------------------------------------------
    logger.info("run_etl: [5/5] save feature matrix")
    features_path = config.DATA_DIR / "features.parquet"
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        feature_df.to_parquet(features_path, index=True, engine="auto")
        logger.info("run_etl: features saved to %s", features_path)
        summary["features_path"] = str(features_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("run_etl: failed to save features parquet: %s", exc)
        summary["features_path"] = None

    # --- 5b. Post-transform EV gate -----------------------------------------
    ev_alerts_df = filter_alert_by_ev(enriched_df, config.MIN_EV_ALERT)
    summary["n_ev_alert_candidates"] = len(ev_alerts_df)
    logger.info(
        "run_etl: [5b] EV gate (ev_final >= %.3f) — %d alert candidates",
        config.MIN_EV_ALERT,
        len(ev_alerts_df),
    )

    # --- 6. Daily summary ---------------------------------------------------
    summary["daily_summary"] = generate_daily_summary(enriched_df)

    logger.info("run_etl: pipeline complete. summary=%s", summary)
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="etl",
        description="Over 2.5 scanner ETL pipeline",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--extract",
        action="store_true",
        help="Extract picks from all sources and validate. Print summary.",
    )
    mode.add_argument(
        "--validate",
        action="store_true",
        help="Validate existing local picks.json only.",
    )
    mode.add_argument(
        "--transform",
        action="store_true",
        help="Validate + enrich + build feature matrix (no GAS fetch).",
    )
    mode.add_argument(
        "--full",
        action="store_true",
        help="Full ETL pipeline: extract → validate → transform → save.",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        help="Path to a .env file (default: <project_root>/.env).",
    )
    parser.add_argument(
        "--output-summary",
        type=Path,
        default=None,
        help="Write pipeline summary JSON to this path.",
    )
    return parser


def _run_cli(args: argparse.Namespace) -> None:
    _, load_config = _get_config_class()
    config = load_config(args.dotenv)

    load_picks_local, load_picks_from_gas, _ = _get_extract()
    validate_picks_fn, _ = _get_schema()
    enrich_picks_fn, create_feature_matrix_fn = _get_transform()

    summary: dict[str, Any] = {}

    if args.validate:
        # Validate only — no GAS fetch, no save
        raw = load_picks_local(config.PICKS_FILE)
        df = validate_picks_fn(raw)
        summary = {
            "mode": "validate",
            "n_raw": len(raw),
            "n_valid": len(df),
            "n_invalid": len(raw) - len(df),
        }

    elif args.extract:
        # Extract + validate (no transform, no save)
        raw_local = load_picks_local(config.PICKS_FILE)
        raw_gas = load_picks_from_gas(config.GAS_SHEET_URL) if config.GAS_SHEET_URL else []
        all_raw = raw_local + [r for r in raw_gas if str(r.get("id")) not in {str(x.get("id")) for x in raw_local}]
        df = validate_picks_fn(all_raw)
        summary = {
            "mode": "extract",
            "n_local": len(raw_local),
            "n_gas": len(raw_gas),
            "n_raw_total": len(all_raw),
            "n_valid": len(df),
            "n_invalid": len(all_raw) - len(df),
        }

    elif args.transform:
        # Validate + transform (no GAS fetch)
        raw = load_picks_local(config.PICKS_FILE)
        df = validate_picks_fn(raw)
        if not df.empty:
            enriched = enrich_picks_fn(df)
            features = create_feature_matrix_fn(enriched)
            summary = {
                "mode": "transform",
                "n_valid": len(df),
                "n_enriched": len(enriched),
                "feature_shape": list(features.shape),
                "feature_cols": list(features.columns),
            }
        else:
            summary = {"mode": "transform", "n_valid": 0, "error": "no valid picks"}

    elif args.full:
        summary = run_etl(config)
        summary["mode"] = "full"

    # Output summary
    summary_json = json.dumps(summary, indent=2, default=str)
    print(summary_json)

    if args.output_summary:
        args.output_summary.parent.mkdir(parents=True, exist_ok=True)
        with args.output_summary.open("w", encoding="utf-8") as fh:
            fh.write(summary_json)
        logger.info("Summary written to %s", args.output_summary)


if __name__ == "__main__":
    parser = _build_parser()
    args = parser.parse_args()
    _run_cli(args)
