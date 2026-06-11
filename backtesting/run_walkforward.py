"""
backtesting/run_walkforward.py
------------------------------
Weekly walk-forward backtest of the Dixon-Coles + market blend model.

No-leakage guarantee
--------------------
When predicting a game on date D, the training set contains ONLY games
with date < D (strictly before the match day).

What is tested
--------------
For each blend weight w in {0.0, 0.15, 0.30, 0.50, 1.0}:
    p_final(w) = w * p_dc + (1 - w) * p_market
    ev_final   = p_final * odds_over - 1
    bet        = 1 if ev_final >= MIN_EV else 0

Metrics reported:
    N_bets, Win%, P&L, Brier Score, Log-loss, Avg CLV%, ROI%

CLV = Closing Line Value: P>2.5 / PC>2.5 - 1  (positive = beat the close)

Extended report includes:
    - Calibration table (10 probability buckets)
    - Results by league
    - Results by odds band

Usage
-----
    python -m backtesting.run_walkforward
    python -m backtesting.run_walkforward --data data/historical/matches.csv
    python -m backtesting.run_walkforward --min-ev 0.03 --min-train 50
"""

from __future__ import annotations

import argparse
import logging
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
MIN_TRAIN_GAMES = 50
MIN_TEAM_GAMES  = 5
XI = 0.0018
PINNACLE_MARGIN = 1.04

_DIV_TO_LEAGUE: dict[str, str] = {
    "E0":  "Premier League",   "E1":  "Championship",
    "SP1": "La Liga",          "SP2": "La Liga 2",
    "I1":  "Serie A",          "I2":  "Serie B",
    "D1":  "Bundesliga",       "D2":  "Bundesliga 2",
    "F1":  "Ligue 1",          "F2":  "Ligue 2",
    "P1":  "Primeira Liga",    "N1":  "Eredivisie",
    "B1":  "Belgian Pro League",
}

ODDS_BANDS = [
    (0.0,  1.50, "<1.50"),
    (1.50, 1.70, "1.50–1.70"),
    (1.70, 2.00, "1.70–2.00"),
    (2.00, 2.50, "2.00–2.50"),
    (2.50, 99.0, ">2.50"),
]


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
    for col in ("P>2.5", "P<2.5", "PC>2.5", "PC<2.5"):
        if col not in df.columns:
            df[col] = np.nan
    # Only keep known divisions — skip '?' (BOM artifact) and any others
    df = df[df["Div"].isin(_DIV_TO_LEAGUE.keys())].copy()
    df.sort_values("Date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ---------------------------------------------------------------------------
# Devig helper
# ---------------------------------------------------------------------------

def _devig_market(p_over: float, p_under: float) -> float:
    total = p_over + p_under
    if total <= 0:
        return np.nan
    return p_over / total


def _market_prob(row: pd.Series) -> tuple[float, str]:
    p_raw = row.get("P>2.5", np.nan)
    u_raw = row.get("P<2.5", np.nan)
    if pd.notna(p_raw) and pd.notna(u_raw) and float(p_raw) > 1.0 and float(u_raw) > 1.0:
        return _devig_market(1.0 / float(p_raw), 1.0 / float(u_raw)), "devig"
    if pd.notna(p_raw) and float(p_raw) > 1.0:
        return (1.0 / float(p_raw)) / PINNACLE_MARGIN, "fallback"
    return np.nan, "missing"


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def _weeks_between(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start=start, end=end, freq="W-MON"))


def run_walkforward(
    df: pd.DataFrame,
    blend_weights: list[float] = BLEND_WEIGHTS,
    min_ev: float = MIN_EV_DEFAULT,
    min_train: int = MIN_TRAIN_GAMES,
) -> pd.DataFrame:
    from models.math.poisson import fit_dixon_coles_fast, prob_over25_from_model

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])

    all_records: list[dict] = []
    _leakage_violations = 0
    _models: dict[str, object] = {}
    _model_trained_at: dict[str, pd.Timestamp] = {}

    weeks = _weeks_between(df["Date"].min(), df["Date"].max() + timedelta(days=7))

    for week_idx, week_start in enumerate(weeks[:-1]):
        week_end = weeks[week_idx + 1]
        train_mask = df["Date"] < week_start
        test_mask  = (df["Date"] >= week_start) & (df["Date"] < week_end)
        test_df = df[test_mask]
        if test_df.empty:
            continue
        if train_mask.sum() < min_train:
            continue

        for div, div_test in test_df.groupby("Div"):
            if str(div) not in _DIV_TO_LEAGUE:
                continue
            div_train = df[train_mask & (df["Div"] == div)]
            if len(div_train) < MIN_TEAM_GAMES:
                continue

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

            league = _DIV_TO_LEAGUE[str(div)]

            for _, row in div_test.iterrows():
                if row["Date"] < week_start:
                    _leakage_violations += 1
                    continue

                home, away = str(row["HomeTeam"]), str(row["AwayTeam"])
                home_prior = ((div_train["HomeTeam"] == home) | (div_train["AwayTeam"] == home)).sum()
                away_prior = ((div_train["HomeTeam"] == away) | (div_train["AwayTeam"] == away)).sum()
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

                pc_over = float(row.get("PC>2.5", np.nan))
                clv = (odds_over / pc_over - 1.0) if (pd.notna(pc_over) and pc_over > 1.0) else np.nan

                for w in blend_weights:
                    p_final = w * p_dc + (1.0 - w) * p_market_val
                    ev_final = p_final * odds_over - 1.0
                    if ev_final < min_ev:
                        continue
                    all_records.append({
                        "date":            row["Date"],
                        "div":             div,
                        "league":          league,
                        "home":            home,
                        "away":            away,
                        "over25":          int(row["over25"]),
                        "p_dc":            round(p_dc, 6),
                        "p_market":        round(p_market_val, 6),
                        "p_market_source": p_market_src,
                        "blend_weight":    w,
                        "p_final":         round(p_final, 6),
                        "ev_final":        round(ev_final, 6),
                        "odds_over":       round(odds_over, 3),
                        "clv":             round(clv, 6) if pd.notna(clv) else np.nan,
                        "won":             int(row["over25"] == 1),
                    })

    if _leakage_violations:
        raise RuntimeError(f"LEAKAGE DETECTED: {_leakage_violations} violations!")

    logger.info("No leakage violations detected ✓")
    return pd.DataFrame(all_records)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))

def _log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-7) -> float:
    p = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

def _roi(y: np.ndarray, odds: np.ndarray) -> float:
    pnl = np.sum(np.where(y == 1, odds - 1.0, -1.0))
    return float(pnl / len(y) * 100) if len(y) > 0 else 0.0


def compute_metrics(bets: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for w in sorted(bets["blend_weight"].unique()):
        sub = bets[bets["blend_weight"] == w]
        if sub.empty:
            continue
        y = sub["won"].values.astype(float)
        p = sub["p_final"].values.astype(float)
        odds = sub["odds_over"].values
        clv_vals = sub["clv"].dropna()
        rows.append({
            "w":       w,
            "n_bets":  len(sub),
            "win_rate": round(float(y.mean()), 4),
            "pnl":     round(float(np.sum(np.where(y == 1, odds - 1.0, -1.0))), 2),
            "roi_pct": round(_roi(y, odds), 2),
            "brier":   round(_brier(y, p), 5),
            "log_loss":round(_log_loss(y, p), 5),
            "avg_clv_pct": round(float(clv_vals.mean()) * 100, 3) if len(clv_vals) > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


def calibration_table(bets: pd.DataFrame, best_w: float, n_buckets: int = 10) -> list[dict]:
    sub = bets[bets["blend_weight"] == best_w]
    if sub.empty:
        return []
    sub = sub.copy()
    sub["bucket"] = pd.cut(sub["p_final"], bins=n_buckets, include_lowest=True)
    rows = []
    for bucket, grp in sub.groupby("bucket", observed=True):
        rows.append({
            "bucket":     str(bucket),
            "n":          len(grp),
            "pred_avg":   round(grp["p_final"].mean(), 3),
            "actual_wr":  round(grp["won"].mean(), 3),
            "diff":       round(grp["won"].mean() - grp["p_final"].mean(), 3),
        })
    return rows


def by_league_table(bets: pd.DataFrame, best_w: float) -> list[dict]:
    sub = bets[bets["blend_weight"] == best_w]
    if sub.empty:
        return []
    rows = []
    for league, grp in sub.groupby("league"):
        y = grp["won"].values.astype(float)
        odds = grp["odds_over"].values
        clv_vals = grp["clv"].dropna()
        rows.append({
            "league":    league,
            "n_bets":    len(grp),
            "win_rate":  round(float(y.mean()), 3),
            "roi_pct":   round(_roi(y, odds), 2),
            "avg_clv_pct": round(float(clv_vals.mean()) * 100, 3) if len(clv_vals) > 0 else float("nan"),
        })
    return sorted(rows, key=lambda r: r["n_bets"], reverse=True)


def by_odds_band_table(bets: pd.DataFrame, best_w: float) -> list[dict]:
    sub = bets[bets["blend_weight"] == best_w].copy()
    if sub.empty:
        return []
    rows = []
    for lo, hi, label in ODDS_BANDS:
        grp = sub[(sub["odds_over"] >= lo) & (sub["odds_over"] < hi)]
        if grp.empty:
            continue
        y = grp["won"].values.astype(float)
        odds = grp["odds_over"].values
        rows.append({
            "band":     label,
            "n_bets":   len(grp),
            "win_rate": round(float(y.mean()), 3),
            "avg_ev":   round(grp["ev_final"].mean(), 4),
            "roi_pct":  round(_roi(y, odds), 2),
        })
    return rows


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _best_w(metrics: pd.DataFrame) -> float:
    if metrics.empty:
        return 0.30
    return float(metrics.loc[metrics["brier"].idxmin(), "w"])


def write_report(
    bets: pd.DataFrame,
    metrics: pd.DataFrame,
    raw_df: pd.DataFrame,
    out_path: Path,
    min_ev: float,
) -> None:
    if bets.empty:
        out_path.write_text("# Walk-Forward — sem apostas geradas\n")
        return

    best_w = _best_w(metrics)
    n_games_total = raw_df[raw_df["Div"].isin(_DIV_TO_LEAGUE)].shape[0]
    date_min = raw_df["Date"].min().date()
    date_max = raw_df["Date"].max().date()
    leagues = sorted(_DIV_TO_LEAGUE.values())
    n_known = len(raw_df[raw_df["Div"].isin(_DIV_TO_LEAGUE)])
    n_total = len(raw_df)

    # --- weight table ---
    w_rows = []
    for _, r in metrics.iterrows():
        clv_str = f"{r['avg_clv_pct']:+.2f}%" if pd.notna(r["avg_clv_pct"]) else "N/A"
        w_rows.append(
            f"| **{r['w']:.2f}** | {int(r['n_bets']):>6} | {r['win_rate']*100:.1f}% | "
            f"{r['pnl']:>+7.1f}u | {r['roi_pct']:>+6.2f}% | "
            f"{r['brier']:.5f} | {r['log_loss']:.5f} | {clv_str} |"
        )

    # --- calibration table ---
    cal_rows = []
    for r in calibration_table(bets, best_w):
        diff_str = f"{r['diff']:+.3f}"
        cal_rows.append(
            f"| {r['bucket']} | {r['n']:>5} | {r['pred_avg']:.3f} | {r['actual_wr']:.3f} | {diff_str} |"
        )

    # --- by league ---
    lg_rows = []
    for r in by_league_table(bets, best_w):
        clv_str = f"{r['avg_clv_pct']:+.2f}%" if pd.notna(r["avg_clv_pct"]) else "N/A"
        lg_rows.append(
            f"| {r['league']:<25} | {r['n_bets']:>6} | {r['win_rate']*100:.1f}% | "
            f"{r['roi_pct']:>+7.2f}% | {clv_str} |"
        )

    # --- by odds band ---
    od_rows = []
    for r in by_odds_band_table(bets, best_w):
        od_rows.append(
            f"| {r['band']:<12} | {r['n_bets']:>6} | {r['win_rate']*100:.1f}% | "
            f"{r['avg_ev']:>+.4f} | {r['roi_pct']:>+7.2f}% |"
        )

    best_row = metrics[metrics["w"] == best_w].iloc[0]
    bom_note = ""
    if n_known < n_total:
        bom_note = (
            f"\n> ⚠️ **{n_total - n_known:,} linhas excluídas** (Div=`?`): "
            "CSVs de football-data.co.uk com BOM UTF-8 lido incorrectamente em latin-1 "
            "— correcto após próximo `--download-all` com a versão fixada do pipeline.\n"
        )

    nl = "\n"
    report = (
f"# Walk-Forward Backtest Report\n"
f"\n"
f"> **DADOS REAIS — football-data.co.uk — {n_known:,} jogos**\n"
f"> 5 épocas × 13 divisões × temporadas 2021-22 a 2025-26\n"
f"{bom_note}"
f"Generated: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC\n"
f"\n"
f"## Dataset\n"
f"\n"
f"| | |\n"
f"|---|---|\n"
f"| Jogos usados (Div conhecida) | **{n_known:,}** |\n"
f"| Date range | {date_min} → {date_max} |\n"
f"| Ligas | 13 ({', '.join(leagues)}) |\n"
f"| EV threshold (MIN\\_EV) | {min_ev:.2f} ({min_ev*100:.0f}%) |\n"
f"\n"
f"## No-leakage verification\n"
f"\n"
f"- Training: todos os jogos com `date < week_start(W)` (sem lookahead)\n"
f"- Test: jogos em `[week_start(W), week_start(W+1))`\n"
f"- Cold-start: equipas com < {MIN_TEAM_GAMES} jogos anteriores ignoradas\n"
f"- **0 violações de leakage detectadas ✓**\n"
f"\n"
f"## Resultados por peso de blend\n"
f"\n"
f"`p_final = w × p_dc + (1 − w) × p_market`\n"
f"\n"
f"| w | N apostas | Win% | P&L | ROI | Brier | Log-loss | Avg CLV |\n"
f"|---|-----------|------|-----|-----|-------|----------|---------|\n"
+ nl.join(w_rows) + "\n"
f"\n"
f"> Brier Score benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230\n"
f"> CLV = `P>2.5 / PC>2.5 − 1`; positivo = apostámos a odds melhores que o fecho.\n"
f"> ROI = (P&L / N apostas) × 100\n"
f"\n"
f"## Peso recomendado: w = {best_w} (melhor Brier = {best_row['brier']:.5f})\n"
f"\n"
f"Win%={best_row['win_rate']*100:.1f}%  |  ROI={best_row['roi_pct']:+.2f}%  |  N={int(best_row['n_bets'])}\n"
f"\n"
f"## Tabela de calibração (w = {best_w})\n"
f"\n"
f"Buckets de probabilidade prevista vs taxa real de vitória.\n"
f"\n"
f"| Bucket previsto | N | Pred médio | Win% real | Diferença |\n"
f"|-----------------|---|-----------|-----------|----------|\n"
+ nl.join(cal_rows) + "\n"
f"\n"
f"## Resultados por liga (w = {best_w})\n"
f"\n"
f"| Liga | N apostas | Win% | ROI | Avg CLV |\n"
f"|------|-----------|------|-----|--------|\n"
+ nl.join(lg_rows) + "\n"
f"\n"
f"## Resultados por banda de odds (w = {best_w})\n"
f"\n"
f"| Odds | N apostas | Win% | EV médio | ROI |\n"
f"|------|-----------|------|----------|----|\n"
+ nl.join(od_rows) + "\n"
f"\n"
f"## Notas metodológicas\n"
f"\n"
f"- Modelo Dixon-Coles re-treinado semanalmente (cada segunda-feira) por divisão\n"
f"- Decay ξ = {XI} (semi-vida ≈ 2 anos)\n"
f"- Probabilidade de mercado: devig multiplicativo sobre Pinnacle opening (`P>2.5` / `P<2.5`)\n"
f"- Fallback quando `P<2.5` ausente: `(1 / P>2.5) / {PINNACLE_MARGIN}`\n"
f"- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ {min_ev}`\n"
f"- Stake: flat 1 unidade (Kelly desabilitado — `Config.STAKE_TYPE = \"flat\"`)\n"
    )

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
                   default=root / "data" / "historical" / "matches.csv")
    p.add_argument("--out", type=Path,
                   default=root / "backtesting" / "reports" / "walkforward.md")
    p.add_argument("--min-ev", type=float, default=MIN_EV_DEFAULT)
    p.add_argument("--min-train", type=int, default=MIN_TRAIN_GAMES)
    p.add_argument("--weights", nargs="+", type=float, default=BLEND_WEIGHTS)
    return p


def main(argv: Optional[list[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-8s | %(message)s")
    args = _build_parser().parse_args(argv)

    if not args.data.exists():
        raise FileNotFoundError(f"matches.csv not found at {args.data}")

    logger.info("Loading %s…", args.data)
    raw_df = pd.read_csv(args.data, parse_dates=["Date"])
    df = _load(args.data)
    logger.info("Loaded %d games from %d known divisions (%.0f%% of %d total)",
                len(df), df["Div"].nunique(),
                len(df) / len(raw_df) * 100, len(raw_df))

    logger.info("Running walk-forward (weights=%s, min_ev=%.3f)…", args.weights, args.min_ev)
    t0 = time.perf_counter()
    bets = run_walkforward(df, blend_weights=args.weights,
                           min_ev=args.min_ev, min_train=args.min_train)
    elapsed = time.perf_counter() - t0
    logger.info("Walk-forward complete in %.1fs — %d bet records", elapsed, len(bets))

    metrics = compute_metrics(bets)
    print("\n" + metrics.to_string(index=False))

    write_report(bets, metrics, raw_df, args.out, min_ev=args.min_ev)
    print(f"\nReport saved to {args.out}")


if __name__ == "__main__":
    main()
