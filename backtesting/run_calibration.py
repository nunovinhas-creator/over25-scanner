"""
backtesting/run_calibration.py
------------------------------
FASE 4: Calibração com validação temporal estrita.

REGRA DE OURO: split temporal — a época de validação (2526) nunca é
tocada durante o ajuste do calibrador.

Épocas de treino/calibração : 2122, 2223, 2324, 2425
Época de validação (intocada): 2526

Pipeline
--------
1. Corre walk-forward completo (blend=0.0) para recolher previsões OOS
   de p_dc com labels de época — um único passe, sem lookahead.
2. LOEO-CV (4 folds) sobre épocas de treino: compara Platt scaling vs
   Isotónico por Brier médio.
3. Ajusta calibrador final no conjunto completo de treino (épocas 2122–2425).
4. Serializa em data/calibrator.json (parâmetros legíveis, não pickle).
5. Valida na época 2526: calibrado vs não-calibrado vs baseline de mercado.
   — Tabela de calibração, resultados por banda de odds.
   — Decisão sobre cap de odds.
6. Gera backtesting/reports/calibration_validation.md.

Usage
-----
    python -m backtesting.run_calibration
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRAIN_EPOCHS = [2122, 2223, 2324, 2425]
VAL_EPOCH = 2526
CAL_WEIGHTS = [0.10, 0.15, 0.20, 0.30]
MIN_EV = 0.03
MIN_TRAIN = 50

ROOT = Path(__file__).resolve().parent.parent
DATA_CSV = ROOT / "data" / "historical" / "matches.csv"
CALIBRATOR_PATH = ROOT / "data" / "calibrator.json"
REPORT_PATH = ROOT / "backtesting" / "reports" / "calibration_validation.md"

_MIN_SEGMENT_N = 30  # min bets per segment for own row in tables

ODDS_BANDS = [
    (0.0,  1.50, "<1.50"),
    (1.50, 1.70, "1.50–1.70"),
    (1.70, 2.00, "1.70–2.00"),
    (2.00, 2.50, "2.00–2.50"),
    (2.50, 99.0, ">2.50"),
]


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _brier(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-7, 1.0 - 1e-7)
    return float(np.mean((p - y) ** 2))


def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-7, 1.0 - 1e-7)
    return float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))


def _roi(y: np.ndarray, odds: np.ndarray) -> float:
    if len(y) == 0:
        return float("nan")
    pnl = float(np.sum(np.where(y == 1, odds - 1.0, -1.0)))
    return pnl / len(y) * 100.0


def _clv_ci(clv_pct: np.ndarray) -> dict:
    clv_pct = clv_pct[~np.isnan(clv_pct)]
    n = len(clv_pct)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "se": float("nan"),
                "ci95_lo": float("nan"), "ci95_hi": float("nan"), "ci_includes_zero": True}
    mean = float(np.mean(clv_pct))
    se = float(np.std(clv_pct, ddof=1) / np.sqrt(n))
    ci_lo, ci_hi = mean - 1.96 * se, mean + 1.96 * se
    return {
        "n": n,
        "mean": round(mean, 3),
        "se": round(se, 3),
        "ci95_lo": round(ci_lo, 3),
        "ci95_hi": round(ci_hi, 3),
        "ci_includes_zero": ci_lo <= 0.0 <= ci_hi,
    }


# ---------------------------------------------------------------------------
# Calibrator serialization / deserialization
# ---------------------------------------------------------------------------

def _calibrator_fn_from_data(cal_data: dict):
    """Reconstruct a callable np.ndarray -> np.ndarray from JSON calibrator data."""
    method = cal_data["method"]
    if method == "platt":
        A, B = float(cal_data["A"]), float(cal_data["B"])
        def _predict(p_arr: np.ndarray) -> np.ndarray:
            from scipy.special import expit
            p = np.clip(np.asarray(p_arr, dtype=np.float64), 1e-6, 1.0 - 1e-6)
            return np.clip(expit(A * np.log(p / (1.0 - p)) + B), 1e-6, 1.0 - 1e-6)
        return _predict
    elif method == "isotonic":
        x = np.array(cal_data["x_thresholds"], dtype=np.float64)
        y = np.array(cal_data["y_thresholds"], dtype=np.float64)
        def _predict(p_arr: np.ndarray) -> np.ndarray:
            return np.clip(np.interp(np.asarray(p_arr, dtype=np.float64), x, y), 1e-6, 1.0 - 1e-6)
        return _predict
    else:
        raise ValueError(f"Método de calibração desconhecido: {method!r}")


def _save_calibrator(cal_data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cal_data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Calibrador guardado em %s", path)


# ---------------------------------------------------------------------------
# LOEO Cross-Validation
# ---------------------------------------------------------------------------

def _loeo_cv(train_recs: pd.DataFrame) -> dict:
    """
    Leave-one-epoch-out CV (4 folds).
    Returns {method: {"avg_brier": float, "fold_briers": list[float]}}
    """
    from models.math.calibration import PlattScaler, IsotonicCalibrator

    epochs = sorted(train_recs["season"].unique())
    results: dict = {
        "platt":    {"fold_briers": [], "avg_brier": float("nan")},
        "isotonic": {"fold_briers": [], "avg_brier": float("nan")},
    }

    for held_out in epochs:
        cal_mask = train_recs["season"] != held_out
        val_mask = train_recs["season"] == held_out

        cal_p  = train_recs.loc[cal_mask, "p_dc"].values
        cal_y  = train_recs.loc[cal_mask, "won"].values.astype(float)
        val_p  = train_recs.loc[val_mask, "p_dc"].values
        val_y  = train_recs.loc[val_mask, "won"].values.astype(float)

        if len(cal_p) < 30 or len(val_p) == 0:
            logger.warning("LOEO fold %s: dados insuficientes (%d cal, %d val)",
                           held_out, len(cal_p), len(val_p))
            continue

        for method_name, CalibratorClass in [("platt", PlattScaler), ("isotonic", IsotonicCalibrator)]:
            try:
                cal_obj = CalibratorClass().fit(cal_p, cal_y)
                val_pred = cal_obj.predict(val_p)
                fold_brier = _brier(val_y, val_pred)
                results[method_name]["fold_briers"].append(fold_brier)
                logger.info("  LOEO fold held_out=%s | %s | Brier=%.5f", held_out, method_name, fold_brier)
            except Exception as exc:
                logger.warning("  LOEO fold %s / %s falhou: %s", held_out, method_name, exc)

    for method in results:
        folds = results[method]["fold_briers"]
        results[method]["avg_brier"] = round(float(np.mean(folds)), 6) if folds else float("nan")

    return results


# ---------------------------------------------------------------------------
# Final calibrator fitting
# ---------------------------------------------------------------------------

def _fit_final_calibrator(train_recs: pd.DataFrame, method: str, cv_brier: float) -> dict:
    """Fit calibrator on all training records. Returns JSON-serializable dict."""
    from models.math.calibration import PlattScaler, IsotonicCalibrator

    p = train_recs["p_dc"].values
    y = train_recs["won"].values.astype(float)
    logger.info("A ajustar calibrador final (%s) em %d amostras de treino…", method, len(p))

    if method == "platt":
        scaler = PlattScaler().fit(p, y)
        A, B = scaler.coef_
        return {
            "method": "platt",
            "A": round(float(A), 8),
            "B": round(float(B), 8),
            "cv_brier_loeo": round(cv_brier, 6),
            "n_train": int(len(p)),
            "train_epochs": TRAIN_EPOCHS,
        }

    # isotonic
    from models.math.calibration import IsotonicCalibrator
    iso_cal = IsotonicCalibrator().fit(p, y)
    if iso_cal._used_fallback:
        # Fell back to Platt internally
        A, B = iso_cal._fallback.coef_
        return {
            "method": "platt",
            "A": round(float(A), 8),
            "B": round(float(B), 8),
            "cv_brier_loeo": round(cv_brier, 6),
            "n_train": int(len(p)),
            "train_epochs": TRAIN_EPOCHS,
            "note": "isotonic fell back to platt (n<30)",
        }
    x_thresh = iso_cal._iso.X_thresholds_.tolist()
    y_thresh = iso_cal._iso.y_thresholds_.tolist()
    return {
        "method": "isotonic",
        "x_thresholds": [round(v, 8) for v in x_thresh],
        "y_thresholds": [round(v, 8) for v in y_thresh],
        "cv_brier_loeo": round(cv_brier, 6),
        "n_train": int(len(p)),
        "train_epochs": TRAIN_EPOCHS,
    }


# ---------------------------------------------------------------------------
# Validation metrics (all post-hoc from w=0.0 OOS records)
# ---------------------------------------------------------------------------

def _segment_metrics(y_b: np.ndarray, odds_b: np.ndarray,
                     p_f_b: np.ndarray, clv_b: np.ndarray) -> dict:
    n = len(y_b)
    if n == 0:
        return {"n_bets": 0, "win_rate": float("nan"), "pnl": 0.0,
                "roi_pct": float("nan"), "brier": float("nan"),
                "log_loss": float("nan"), "clv_ci": _clv_ci(np.array([]))}
    clv_pct = clv_b[~np.isnan(clv_b)] * 100.0
    return {
        "n_bets":   n,
        "win_rate": round(float(y_b.mean()), 4),
        "pnl":      round(float(np.sum(np.where(y_b == 1, odds_b - 1.0, -1.0))), 2),
        "roi_pct":  round(_roi(y_b, odds_b), 2),
        "brier":    round(_brier(y_b, p_f_b), 5),
        "log_loss": round(_log_loss(y_b, p_f_b), 5),
        "clv_ci":   _clv_ci(clv_pct),
    }


def _odds_band_table(y: np.ndarray, odds: np.ndarray, p_f: np.ndarray,
                     ev: np.ndarray, bet_mask: np.ndarray) -> list[dict]:
    if not bet_mask.any():
        return []
    rows: list[dict] = []
    others_y: list[np.ndarray] = []
    others_odds: list[np.ndarray] = []
    for lo, hi, label in ODDS_BANDS:
        mask = bet_mask & (odds >= lo) & (odds < hi)
        if not mask.any():
            continue
        y_b = y[mask]; o_b = odds[mask]; ev_b = ev[mask]
        entry = {
            "band":    label,
            "n_bets":  int(mask.sum()),
            "win_rate": round(float(y_b.mean()), 3),
            "avg_ev":  round(float(ev_b.mean()), 4),
            "roi_pct": round(_roi(y_b, o_b), 2),
        }
        if entry["n_bets"] >= _MIN_SEGMENT_N:
            rows.append(entry)
        else:
            others_y.append(y_b)
            others_odds.append(o_b)
    if others_y:
        yo = np.concatenate(others_y); oo = np.concatenate(others_odds)
        rows.append({
            "band":    f"outros (n<{_MIN_SEGMENT_N})",
            "n_bets":  len(yo),
            "win_rate": round(float(yo.mean()), 3),
            "avg_ev":  float("nan"),
            "roi_pct": round(_roi(yo, oo), 2),
        })
    return rows


def _calibration_tbl(y: np.ndarray, p_f: np.ndarray,
                     bet_mask: np.ndarray, n_buckets: int = 10) -> list[dict]:
    if not bet_mask.any():
        return []
    p_bet = p_f[bet_mask]; y_bet = y[bet_mask]
    try:
        bins_s = pd.cut(pd.Series(p_bet), bins=n_buckets, include_lowest=True)
    except Exception:
        return []
    rows = []
    for bucket in bins_s.cat.categories:
        m = (bins_s == bucket).values
        if not m.any():
            continue
        rows.append({
            "bucket":    str(bucket),
            "n":         int(m.sum()),
            "pred_avg":  round(float(p_bet[m].mean()), 3),
            "actual_wr": round(float(y_bet[m].mean()), 3),
            "diff":      round(float(y_bet[m].mean() - p_bet[m].mean()), 3),
        })
    return rows


def _compute_val_metrics(val_recs: pd.DataFrame, cal_fn, weights: list[float], min_ev: float) -> dict:
    p_dc   = val_recs["p_dc"].values
    p_mkt  = val_recs["p_market"].values
    odds   = val_recs["odds_over"].values
    y      = val_recs["won"].values.astype(float)
    clv    = val_recs["clv"].values

    p_cal = cal_fn(p_dc)

    results: dict = {
        "market_brier":   round(_brier(y, p_mkt), 5),
        "market_logloss": round(_log_loss(y, p_mkt), 5),
        "market_n":       len(val_recs),
        "by_weight":      {},
        "best_w":         weights[0],
    }
    best_brier = float("inf")

    for w in weights:
        pf_cal = w * p_cal + (1.0 - w) * p_mkt
        ev_cal = pf_cal * odds - 1.0
        bm_cal = ev_cal >= min_ev

        pf_unc = w * p_dc + (1.0 - w) * p_mkt
        ev_unc = pf_unc * odds - 1.0
        bm_unc = ev_unc >= min_ev

        cal_m = _segment_metrics(y[bm_cal], odds[bm_cal], pf_cal[bm_cal], clv[bm_cal])
        unc_m = _segment_metrics(y[bm_unc], odds[bm_unc], pf_unc[bm_unc], clv[bm_unc])
        results["by_weight"][w] = {"cal": cal_m, "uncal": unc_m}

        if (cal_m["n_bets"] >= 10
                and not np.isnan(cal_m["brier"])
                and cal_m["brier"] < best_brier):
            best_brier = cal_m["brier"]
            results["best_w"] = w

    bw = results["best_w"]
    pf_cal_bw = bw * p_cal + (1.0 - bw) * p_mkt
    ev_cal_bw  = pf_cal_bw * odds - 1.0
    bm_cal_bw  = ev_cal_bw >= min_ev

    pf_unc_bw = bw * p_dc + (1.0 - bw) * p_mkt
    ev_unc_bw  = pf_unc_bw * odds - 1.0
    bm_unc_bw  = ev_unc_bw >= min_ev

    results["cal_table"]    = _calibration_tbl(y, pf_cal_bw, bm_cal_bw)
    results["uncal_table"]  = _calibration_tbl(y, pf_unc_bw, bm_unc_bw)
    results["band_cal"]     = _odds_band_table(y, odds, pf_cal_bw, ev_cal_bw, bm_cal_bw)
    results["band_unc"]     = _odds_band_table(y, odds, pf_unc_bw, ev_unc_bw, bm_unc_bw)
    return results


# ---------------------------------------------------------------------------
# Report writer
# ---------------------------------------------------------------------------

def _fmt_metric(v, fmt="+.2f", suffix="%"):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:{fmt}}{suffix}"


def _write_report(cv_results: dict, cal_data: dict, val_metrics: dict, path: Path) -> None:
    method_name = cal_data["method"].capitalize()
    n_train     = cal_data["n_train"]
    cv_brier    = cal_data["cv_brier_loeo"]

    if cal_data["method"] == "platt":
        params_str = f"A={cal_data['A']:.4f}, B={cal_data['B']:.4f}"
    else:
        params_str = f"{len(cal_data['x_thresholds'])} pares de threshold"

    platt_brier = cv_results.get("platt", {}).get("avg_brier", float("nan"))
    iso_brier   = cv_results.get("isotonic", {}).get("avg_brier", float("nan"))
    platt_folds = [round(b, 5) for b in cv_results.get("platt", {}).get("fold_briers", [])]
    iso_folds   = [round(b, 5) for b in cv_results.get("isotonic", {}).get("fold_briers", [])]

    # Select winner label
    platt_sel = " ← **seleccionado**" if cal_data["method"] == "platt" else ""
    iso_sel   = " ← **seleccionado**" if cal_data["method"] == "isotonic" else ""

    bw = val_metrics["best_w"]
    best_cal  = val_metrics["by_weight"][bw]["cal"]
    best_unc  = val_metrics["by_weight"][bw]["uncal"]
    ci = best_cal["clv_ci"]
    ci_note = " ✓ IC não inclui zero" if not ci["ci_includes_zero"] else " ⚠️ IC inclui zero — CLV não significativamente positivo"

    # Weight table rows
    w_rows = []
    for w in sorted(val_metrics["by_weight"]):
        cm = val_metrics["by_weight"][w]["cal"]
        um = val_metrics["by_weight"][w]["uncal"]
        cal_clv = (f"{cm['clv_ci']['mean']:+.3f}%"
                   if cm["n_bets"] > 0 and not np.isnan(cm["clv_ci"]["mean"]) else "—")
        cal_br  = f"{cm['brier']:.5f}" if cm["n_bets"] > 0 and not np.isnan(cm.get("brier", float("nan"))) else "—"
        unc_br  = f"{um['brier']:.5f}" if um["n_bets"] > 0 and not np.isnan(um.get("brier", float("nan"))) else "—"
        mark = " ◄" if w == bw else ""
        w_rows.append(
            f"| **{w:.2f}** | {cm['n_bets']} | {_fmt_metric(cm['roi_pct'])} | {cal_clv} | {cal_br} "
            f"|| {um['n_bets']} | {_fmt_metric(um['roi_pct'])} | {unc_br} |{mark}"
        )

    # Calibration table rows
    def _tbl_rows(rows):
        return [
            f"| {r['bucket']} | {r['n']:>4} | {r['pred_avg']:.3f} | {r['actual_wr']:.3f} | {r['diff']:+.3f} |"
            for r in rows
        ] or ["| — | — | — | — | — |"]

    # Odds band rows
    def _band_rows(rows):
        out = []
        for r in rows:
            ev_s = f"{r['avg_ev']:+.4f}" if not np.isnan(r.get("avg_ev", float("nan"))) else "—"
            out.append(
                f"| {r['band']:<12} | {r['n_bets']:>5} | {r['win_rate']*100:.1f}% | "
                f"{ev_s} | {r['roi_pct']:>+7.2f}% |"
            )
        return out or ["| — | — | — | — | — |"]

    cal_band_rows = _band_rows(val_metrics.get("band_cal", []))
    unc_band_rows = _band_rows(val_metrics.get("band_unc", []))

    # Decision logic — based on 2.00–2.50 band after calibration
    cal_bands = {r["band"]: r for r in val_metrics.get("band_cal", [])}
    unc_bands = {r["band"]: r for r in val_metrics.get("band_unc", [])}
    band_25_cal = cal_bands.get("2.00–2.50", {})
    band_25_unc = unc_bands.get("2.00–2.50", {})

    if not band_25_cal or band_25_cal.get("n_bets", 0) < 10:
        decision = (
            "**INCONCLUSIVO** — Banda 2.00–2.50 sem apostas suficientes (< 10) "
            "após calibração na época de validação. Sem cap aplicado.\n\n"
            "Reavaliar com dados de mais uma época."
        )
    else:
        roi_cal_25 = band_25_cal.get("roi_pct", float("nan"))
        roi_unc_25 = band_25_unc.get("roi_pct", float("nan")) if band_25_unc else float("nan")
        n_cal_25   = band_25_cal.get("n_bets", 0)
        if roi_cal_25 < -15.0:
            decision = (
                f"**CAP RECOMENDADO ≤ 2.00** — A assimetria persiste na banda 2.00–2.50 "
                f"após calibração (ROI={roi_cal_25:+.1f}%, N={n_cal_25}). "
                f"Não-calibrado: ROI={roi_unc_25:+.1f}%. "
                "A calibração não resolveu o overconfidence nesta banda.\n\n"
                "**Implementação sugerida**: adicionar `MAX_ODDS_OVER = 2.00` em `config.py`.\n\n"
                "**Justificação**: Dixon-Coles sobrestima probabilidades em jogos de baixo "
                "expected score (odds altas Over). A calibração suaviza mas não elimina o viés."
            )
        elif roi_cal_25 >= -10.0:
            decision = (
                f"**SEM CAP** — A calibração resolveu a assimetria na banda 2.00–2.50 "
                f"(ROI={roi_cal_25:+.1f}% calibrado vs {roi_unc_25:+.1f}% não-calibrado). "
                "Toda a gama de odds é válida."
            )
        else:
            decision = (
                f"**ATENÇÃO / MONITORIZAR** — A banda 2.00–2.50 continua negativa após "
                f"calibração (ROI={roi_cal_25:+.1f}%, N={n_cal_25}), mas melhora face ao "
                f"não-calibrado ({roi_unc_25:+.1f}%). "
                "Sem cap agora — reavaliar após mais uma época de validação."
            )

    # Best weight section — defensive formatting
    def _pct(v):
        return f"{v*100:.1f}%" if not np.isnan(v) else "—"
    def _pnl(v):
        return f"{v:+.1f}u"

    nl = "\n"
    report = (
f"# Calibration Validation Report — Época {VAL_EPOCH}\n"
f"\n"
f"> **Split temporal estrito**\n"
f"> Treino/Calibração: épocas {', '.join(str(e) for e in TRAIN_EPOCHS)}\n"
f"> Validação (intocada): época **{VAL_EPOCH}**\n"
f"\n"
f"Generated: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC\n"
f"\n"
f"## Calibrador seleccionado\n"
f"\n"
f"| Métrica | Valor |\n"
f"|---------|-------|\n"
f"| Método seleccionado | **{method_name}** |\n"
f"| CV Brier (LOEO 4-fold) | {cv_brier:.5f} |\n"
f"| Platt avg Brier | {platt_brier:.5f}{platt_sel} |\n"
f"| Isotónico avg Brier | {iso_brier:.5f}{iso_sel} |\n"
f"| Parâmetros | {params_str} |\n"
f"| N amostras de treino | {n_train:,} |\n"
f"| Épocas de treino | {', '.join(str(e) for e in TRAIN_EPOCHS)} |\n"
f"\n"
f"Brier por fold — Platt: `{platt_folds}`\n"
f"Brier por fold — Isotónico: `{iso_folds}`\n"
f"\n"
f"## Baseline de mercado (época {VAL_EPOCH}, todos os jogos com odds Pinnacle)\n"
f"\n"
f"| Métrica | Valor |\n"
f"|---------|-------|\n"
f"| N jogos | {val_metrics['market_n']:,} |\n"
f"| Brier (p_market) | {val_metrics['market_brier']:.5f} |\n"
f"| Log-loss (p_market) | {val_metrics['market_logloss']:.5f} |\n"
f"\n"
f"> Brier benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230\n"
f"\n"
f"## Resultados por peso — calibrado vs não-calibrado (época {VAL_EPOCH})\n"
f"\n"
f"`p_final = w × p_cal + (1 − w) × p_market`\n"
f"\n"
f"| w | N cal | ROI cal | CLV cal | Brier cal || N unc | ROI unc | Brier unc |\n"
f"|---|-------|---------|---------|----------||-------|---------|----------|\n"
+ nl.join(w_rows) + "\n"
f"\n"
f"> **Cal** = `p_dc` calibrado pelo {method_name} antes do blend\n"
f"> **Unc** = `p_dc` directo do Dixon-Coles (sem calibração)\n"
f"> ◄ = peso seleccionado (melhor Brier calibrado)\n"
f"\n"
f"## Peso seleccionado: w = {bw}\n"
f"\n"
f"| | Calibrado | Não-calibrado |\n"
f"|---|---|---|\n"
f"| N apostas | {best_cal['n_bets']} | {best_unc['n_bets']} |\n"
f"| Win% | {_pct(best_cal['win_rate'])} | {_pct(best_unc['win_rate'])} |\n"
f"| P&L | {_pnl(best_cal['pnl'])} | {_pnl(best_unc['pnl'])} |\n"
f"| ROI | {_fmt_metric(best_cal['roi_pct'])} | {_fmt_metric(best_unc['roi_pct'])} |\n"
f"| Brier | {best_cal['brier']:.5f} | {best_unc['brier']:.5f} |\n"
f"\n"
f"### CLV com IC 95% (calibrado, w = {bw})\n"
f"\n"
f"| Métrica | Valor |\n"
f"|---------|-------|\n"
f"| N apostas com CLV | {ci['n']} |\n"
f"| CLV médio | {ci['mean']:+.3f}% |\n"
f"| SE | ±{ci['se']:.3f}% |\n"
f"| IC 95% | [{ci['ci95_lo']:+.3f}%, {ci['ci95_hi']:+.3f}%] |\n"
f"\n"
f"{ci_note}\n"
f"\n"
f"## Tabela de calibração — w = {bw}\n"
f"\n"
f"### Calibrado (buckets devem alinhar melhor)\n"
f"\n"
f"| Bucket previsto | N | Pred médio | Win% real | Diferença |\n"
f"|-----------------|---|-----------|-----------|----------|\n"
+ nl.join(_tbl_rows(val_metrics.get("cal_table", []))) + "\n"
f"\n"
f"### Não-calibrado (para comparação)\n"
f"\n"
f"| Bucket previsto | N | Pred médio | Win% real | Diferença |\n"
f"|-----------------|---|-----------|-----------|----------|\n"
+ nl.join(_tbl_rows(val_metrics.get("uncal_table", []))) + "\n"
f"\n"
f"## Resultados por banda de odds — w = {bw}\n"
f"\n"
f"### Calibrado\n"
f"\n"
f"| Odds | N apostas | Win% | EV médio | ROI |\n"
f"|------|-----------|------|----------|----|\n"
+ nl.join(cal_band_rows) + "\n"
f"\n"
f"### Não-calibrado\n"
f"\n"
f"| Odds | N apostas | Win% | EV médio | ROI |\n"
f"|------|-----------|------|----------|----|\n"
+ nl.join(unc_band_rows) + "\n"
f"\n"
f"## Decisão: Cap de odds\n"
f"\n"
f"{decision}\n"
f"\n"
f"## Notas metodológicas\n"
f"\n"
f"- Split temporal estrito: calibrador ajustado apenas em épocas {', '.join(str(e) for e in TRAIN_EPOCHS)}\n"
f"- Época {VAL_EPOCH} nunca tocada durante ajuste do calibrador (gold rule)\n"
f"- LOEO-CV: 4 folds, leave-one-epoch-out\n"
f"- Calibrador {method_name}: `p_model = calibrate(p_dc)` → `p_final = w·p_model + (1-w)·p_market`\n"
f"- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ {MIN_EV}`\n"
f"- Serialização em `data/calibrator.json` — sem pickle, parâmetros legíveis\n"
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    logger.info("Relatório guardado em %s", path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_OOS_CACHE = Path("/tmp/over25_oos_cache.parquet")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    if not DATA_CSV.exists():
        raise FileNotFoundError(f"matches.csv não encontrado: {DATA_CSV}")

    from backtesting.run_walkforward import _load, run_walkforward

    # ── Passo 1: recolher previsões OOS (um único passe, blend=0.0) ─────────
    if _OOS_CACHE.exists():
        logger.info("Passo 1/6 — a carregar OOS cache de %s…", _OOS_CACHE)
        all_oos = pd.read_parquet(_OOS_CACHE)
        logger.info("Cache carregada: %d registos OOS", len(all_oos))
    else:
        logger.info("A carregar %s…", DATA_CSV)
        full_df = _load(DATA_CSV)
        logger.info("Carregado: %d jogos | %d divisões | épocas %s",
                    len(full_df), full_df["Div"].nunique(),
                    sorted(full_df["Season"].dropna().unique().astype(int).tolist())
                    if "Season" in full_df.columns else "N/A")
        logger.info("Passo 1/6 — walk-forward completo (blend=[0.0]) para recolher OOS p_dc…")
        t0 = time.perf_counter()
        all_oos = run_walkforward(full_df, blend_weights=[0.0], min_ev=MIN_EV, min_train=MIN_TRAIN)
        elapsed = time.perf_counter() - t0
        logger.info("Walk-forward completo em %.1fs — %d registos OOS", elapsed, len(all_oos))
        all_oos.to_parquet(_OOS_CACHE, index=False)
        logger.info("Cache guardada em %s", _OOS_CACHE)

    if "season" not in all_oos.columns:
        raise RuntimeError("Coluna 'season' em falta — verifica run_walkforward.py")

    train_recs = all_oos[all_oos["season"].isin(TRAIN_EPOCHS)].copy()
    val_recs   = all_oos[all_oos["season"] == VAL_EPOCH].copy()

    logger.info("Treino: %d registos | Validação (%s): %d registos",
                len(train_recs), VAL_EPOCH, len(val_recs))

    if len(train_recs) < 100:
        raise RuntimeError(f"Registos de treino insuficientes: {len(train_recs)}")
    if len(val_recs) == 0:
        raise RuntimeError(f"Nenhum registo encontrado para época {VAL_EPOCH}!")

    # ── Passo 2: LOEO cross-validation ──────────────────────────────────────
    logger.info("Passo 2/6 — LOEO cross-validation (4 folds)…")
    cv_results = _loeo_cv(train_recs)
    for m, r in cv_results.items():
        logger.info("  %s → avg_brier=%.5f | folds=%s",
                    m, r["avg_brier"],
                    [round(b, 5) for b in r["fold_briers"]])

    # ── Passo 3: seleccionar melhor método ──────────────────────────────────
    valid = {m: r for m, r in cv_results.items() if not np.isnan(r["avg_brier"])}
    if not valid:
        raise RuntimeError("Todos os métodos de calibração falharam no LOEO-CV")
    best_method = min(valid, key=lambda m: valid[m]["avg_brier"])
    best_cv_brier = valid[best_method]["avg_brier"]
    logger.info("Passo 3/6 — Melhor método: %s (avg Brier=%.5f)", best_method, best_cv_brier)

    # ── Passo 4: ajustar calibrador final ────────────────────────────────────
    logger.info("Passo 4/6 — A ajustar calibrador final…")
    cal_data = _fit_final_calibrator(train_recs, best_method, best_cv_brier)

    # ── Passo 5: guardar calibrador ──────────────────────────────────────────
    logger.info("Passo 5/6 — A guardar calibrador em %s…", CALIBRATOR_PATH)
    _save_calibrator(cal_data, CALIBRATOR_PATH)
    print(f"\nCalibrador guardado: {CALIBRATOR_PATH}")
    print(json.dumps({k: v for k, v in cal_data.items() if k != "x_thresholds" and k != "y_thresholds"}, indent=2))

    # ── Passo 6: calcular métricas de validação ──────────────────────────────
    logger.info("Passo 6/6 — A calcular métricas de validação para época %s…", VAL_EPOCH)
    cal_fn = _calibrator_fn_from_data(cal_data)
    val_metrics = _compute_val_metrics(val_recs, cal_fn, CAL_WEIGHTS, MIN_EV)

    bw = val_metrics["best_w"]
    bcal = val_metrics["by_weight"][bw]["cal"]
    bunc = val_metrics["by_weight"][bw]["uncal"]
    logger.info("Peso seleccionado: w=%.2f | Cal: N=%d ROI=%+.2f%% | Unc: N=%d ROI=%+.2f%%",
                bw, bcal["n_bets"], bcal["roi_pct"] or 0.0,
                bunc["n_bets"], bunc["roi_pct"] or 0.0)

    # ── Gerar relatório ──────────────────────────────────────────────────────
    _write_report(cv_results, cal_data, val_metrics, REPORT_PATH)
    print(f"Relatório guardado: {REPORT_PATH}")

    # ── Recomendação final ───────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"FASE 4 completa!")
    print(f"  Calibrador: {cal_data['method']} (CV Brier={best_cv_brier:.5f})")
    print(f"  Peso recomendado (validação época {VAL_EPOCH}): w={bw}")
    print(f"  Calibrado — N={bcal['n_bets']}, ROI={bcal['roi_pct']:+.2f}%")
    print(f"  MODEL_WEIGHT sugerido para config.py: {bw}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
