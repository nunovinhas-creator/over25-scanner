"""
backtesting/run_btts_over25_backtest.py
---------------------------------------
Walk-forward backtest for the BTTS+Over 2.5 combined market.

Uses the same no-leakage methodology as run_walkforward.py:
  - Train: all games with date < week_start
  - Test:  games in [week_start, next_week_start)

For each game the DC bivariate grid is used to compute:
  p_dc_conjunta — P(BTTS AND Over 2.5) from the joint grid
  p_btts_dc     — P(BTTS) from the joint grid
  p_over25_dc   — P(Over 2.5) from the joint grid
  p_naive       — p_btts_dc × p_over25_dc (independence assumption)
  overlay       — p_dc_conjunta − p_naive

Bet simulated when overlay >= OVERLAY_MIN_BET (10%).

Output: backtesting/reports/btts_over25_backtest.md

Usage:
    python -m backtesting.run_btts_over25_backtest
    python -m backtesting.run_btts_over25_backtest --data data/historical/matches.csv
"""

from __future__ import annotations

import argparse
import logging
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / "backtesting" / "reports"

OVERLAY_MIN_BET = 0.10   # gate for "simulated bet" in backtest
MIN_TRAIN_GAMES = 50
MIN_TEAM_GAMES  = 5
XI              = 0.0018

DATA_DIR = ROOT / "data"

_DIV_TO_LEAGUE: dict[str, str] = {
    "E0":  "Premier League",   "E1":  "Championship",
    "SP1": "La Liga",          "SP2": "La Liga 2",
    "I1":  "Serie A",          "I2":  "Serie B",
    "D1":  "Bundesliga",       "D2":  "Bundesliga 2",
    "F1":  "Ligue 1",          "F2":  "Ligue 2",
    "P1":  "Primeira Liga",    "N1":  "Eredivisie",
    "B1":  "Belgian Pro League",
}

CAL_BUCKETS  = [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.01]

_PROD_LEAGUES = {
    "Premier League", "Championship", "La Liga", "La Liga 2",
    "Serie A", "Bundesliga", "Ligue 1", "Primeira Liga",
    "Eredivisie", "Belgian Pro League",
}

_DIV_TO_PROD_LEAGUE = {
    k: v for k, v in _DIV_TO_LEAGUE.items() if v in _PROD_LEAGUES
}


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path, parse_dates=["Date"])
    required = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"matches.csv em falta colunas: {missing}")
    df = df.dropna(subset=list(required))
    df["FTHG"] = df["FTHG"].astype(int)
    df["FTAG"] = df["FTAG"].astype(int)
    df["btts"]         = ((df["FTHG"] >= 1) & (df["FTAG"] >= 1)).astype(int)
    df["over25"]       = ((df["FTHG"] + df["FTAG"]) >= 3).astype(int)
    df["btts_over25"]  = (df["btts"] & df["over25"]).astype(int)
    df = df[df["Div"].isin(_DIV_TO_LEAGUE)].copy()
    df.sort_values("Date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ---------------------------------------------------------------------------
# Fast mode: use pre-fitted dc_ratings.json (in-sample, no leakage guarantee)
# ---------------------------------------------------------------------------

def run_fast(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute BTTS+O2.5 probabilities using the saved dc_ratings.json.
    In-sample: models were fitted on overlapping data. For calibration analysis
    only — not for EV estimation. Runs in seconds vs minutes for walk-forward.
    """
    import json as _json
    from models.math.poisson import build_dc_grid, extract_btts_over25_prob, prob_over25_poisson

    dc_path = DATA_DIR / "dc_ratings.json"
    if not dc_path.exists():
        raise FileNotFoundError(f"dc_ratings.json não encontrado: {dc_path}")
    dc_ratings = _json.loads(dc_path.read_text())

    records: list[dict] = []
    for _, row in df.iterrows():
        div = str(row["Div"])
        if div not in _DIV_TO_LEAGUE:
            continue
        league = _DIV_TO_LEAGUE[div]
        ld = dc_ratings.get(league)
        if not ld:
            continue
        teams = ld.get("teams", {})
        # dc_ratings stores raw CSV team names (no normalisation)
        home_n = str(row["HomeTeam"])
        away_n = str(row["AwayTeam"])
        hd = teams.get(home_n)
        ad = teams.get(away_n)
        if not hd or not ad:
            continue
        try:
            lh = float(np.exp(hd["attack"] + ad["defence"] + ld["home_adv"]))
            la = float(np.exp(ad["attack"] + hd["defence"]))
            rho = float(ld.get("rho", 0.0))
            grid = build_dc_grid(lh, la, rho=rho)
            p_dc = extract_btts_over25_prob(grid)
            p_btts = float(grid[1:, 1:].sum())
            p_o25  = prob_over25_poisson(lh, la, rho=rho)
            p_naive = p_btts * p_o25
            overlay = p_dc - p_naive
        except Exception:
            continue
        records.append({
            "date":          row["Date"],
            "div":           div,
            "league":        league,
            "home":          str(row["HomeTeam"]),
            "away":          str(row["AwayTeam"]),
            "p_dc_conjunta": round(p_dc, 6),
            "p_btts_dc":     round(p_btts, 6),
            "p_over25_dc":   round(p_o25, 6),
            "p_naive":       round(p_naive, 6),
            "overlay":       round(overlay, 6),
            "btts":          int(row["btts"]),
            "over25":        int(row["over25"]),
            "btts_over25":   int(row["btts_over25"]),
            "fthg":          int(row["FTHG"]),
            "ftag":          int(row["FTAG"]),
        })
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def _weeks(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    return list(pd.date_range(start=start, end=end, freq="W-MON"))


def run_backtest(df: pd.DataFrame) -> pd.DataFrame:
    from models.math.poisson import (
        fit_dixon_coles_fast,
        build_dc_grid,
        extract_btts_over25_prob,
        prob_over25_poisson,
        _lambda_from_model,
    )

    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])

    records: list[dict] = []
    _models: dict[str, object] = {}
    _model_trained_at: dict[str, pd.Timestamp] = {}
    leakage = 0

    weeks = _weeks(df["Date"].min(), df["Date"].max() + timedelta(days=7))
    for wi, week_start in enumerate(weeks[:-1]):
        week_end = weeks[wi + 1]
        train_mask = df["Date"] < week_start
        test_mask  = (df["Date"] >= week_start) & (df["Date"] < week_end)
        test_df    = df[test_mask]
        if test_df.empty or train_mask.sum() < MIN_TRAIN_GAMES:
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
                    logger.debug("Fit failed %s week %s: %s", div, week_start.date(), exc)
                    continue

            model = _models.get(div)
            if model is None:
                continue

            league = _DIV_TO_LEAGUE[str(div)]
            for _, row in div_test.iterrows():
                if row["Date"] < week_start:
                    leakage += 1
                    continue

                home, away = str(row["HomeTeam"]), str(row["AwayTeam"])
                home_prior = ((div_train["HomeTeam"] == home) | (div_train["AwayTeam"] == home)).sum()
                away_prior = ((div_train["HomeTeam"] == away) | (div_train["AwayTeam"] == away)).sum()
                if home_prior < MIN_TEAM_GAMES or away_prior < MIN_TEAM_GAMES:
                    continue

                try:
                    lh, la = _lambda_from_model(model, home, away)
                    rho = float(model.get("rho", 0.0))
                    grid = build_dc_grid(lh, la, rho=rho)
                    p_dc_conjunta = extract_btts_over25_prob(grid)
                    p_btts_dc     = float(grid[1:, 1:].sum())
                    p_over25_dc   = prob_over25_poisson(lh, la, rho=rho)
                    p_naive       = p_btts_dc * p_over25_dc
                    overlay       = p_dc_conjunta - p_naive
                except Exception:
                    continue

                records.append({
                    "date":          row["Date"],
                    "div":           div,
                    "league":        league,
                    "home":          home,
                    "away":          away,
                    "p_dc_conjunta": round(p_dc_conjunta, 6),
                    "p_btts_dc":     round(p_btts_dc, 6),
                    "p_over25_dc":   round(p_over25_dc, 6),
                    "p_naive":       round(p_naive, 6),
                    "overlay":       round(overlay, 6),
                    "btts":          int(row["btts"]),
                    "over25":        int(row["over25"]),
                    "btts_over25":   int(row["btts_over25"]),
                    "fthg":          int(row["FTHG"]),
                    "ftag":          int(row["FTAG"]),
                })

    if leakage:
        raise RuntimeError(f"LEAKAGE DETECTADO: {leakage} violações!")

    logger.info("Sem violações de leakage ✓")
    return pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _calibration_table(df: pd.DataFrame, pred_col: str) -> list[dict]:
    df = df.copy()
    df["bucket"] = pd.cut(df[pred_col], bins=CAL_BUCKETS, include_lowest=True)
    rows = []
    for bucket, grp in df.groupby("bucket", observed=True):
        if grp.empty:
            continue
        rows.append({
            "bucket":    str(bucket),
            "n":         len(grp),
            "pred_avg":  round(grp[pred_col].mean(), 3),
            "actual_wr": round(grp["btts_over25"].mean(), 3),
            "diff":      round(grp["btts_over25"].mean() - grp[pred_col].mean(), 3),
        })
    return rows


def _by_league(df: pd.DataFrame) -> list[dict]:
    rows = []
    for league, grp in df.groupby("league"):
        rows.append({
            "league":  league,
            "n":       len(grp),
            "wr_pct":  round(grp["btts_over25"].mean() * 100, 1),
            "pred_avg": round(grp["p_dc_conjunta"].mean() * 100, 1),
        })
    return sorted(rows, key=lambda r: r["n"], reverse=True)


def write_report(df: pd.DataFrame, bets: pd.DataFrame, out_path: Path, mode: str = "walk-forward") -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    n_all  = len(df)
    n_bets = len(bets)
    wr_all  = round(df["btts_over25"].mean() * 100, 1)  if n_all  else float("nan")
    wr_bets = round(bets["btts_over25"].mean() * 100, 1) if n_bets else float("nan")
    date_min = df["date"].min().date() if n_all else "—"
    date_max = df["date"].max().date() if n_all else "—"

    avg_overlay_all  = round(df["overlay"].mean() * 100, 2)  if n_all  else float("nan")
    avg_overlay_bets = round(bets["overlay"].mean() * 100, 2) if n_bets else float("nan")

    # Calibration tables
    cal_full_rows = _calibration_table(df, "p_dc_conjunta")
    cal_full_md = "\n".join(
        f"| {r['bucket']:<22} | {r['n']:>6} | {r['pred_avg']:.3f} | {r['actual_wr']:.3f} | {r['diff']:+.3f} |"
        for r in cal_full_rows
    )
    cal_naive_rows = _calibration_table(df, "p_naive")
    cal_naive_md = "\n".join(
        f"| {r['bucket']:<22} | {r['n']:>6} | {r['pred_avg']:.3f} | {r['actual_wr']:.3f} | {r['diff']:+.3f} |"
        for r in cal_naive_rows
    )

    # By league
    lg_all_rows = _by_league(df)
    lg_all_md = "\n".join(
        f"| {r['league']:<25} | {r['n']:>6} | {r['wr_pct']:.1f}% | {r['pred_avg']:.1f}% |"
        for r in lg_all_rows
    )
    if not bets.empty:
        lg_bets_rows = _by_league(bets)
        lg_bets_md = "\n".join(
            f"| {r['league']:<25} | {r['n']:>6} | {r['wr_pct']:.1f}% | {r['pred_avg']:.1f}% |"
            for r in lg_bets_rows
        )
    else:
        lg_bets_md = "| — | — | — | — |"

    mode_note = (
        "> ⚠️ **Modo rápido (in-sample)**: probabilidades calculadas com dc_ratings.json actual.\n"
        "> Modelo treinado em dados sobrepostos — calibração indicativa, não EV real.\n"
        "> Para análise walk-forward sem leakage, correr sem flag `--fast`.\n"
    ) if mode == "fast" else (
        "> Walk-forward sem lookahead: modelo DC re-treinado semanalmente.\n"
    )

    report = f"""\
# BTTS+Over 2.5 — Backtest {mode.title()}

> **Dados reais — football-data.co.uk — {n_all:,} jogos com previsão DC**
> Threshold overlay (backtest): ≥ {OVERLAY_MIN_BET*100:.0f}% (p\\_dc\\_conjunta − p\\_naive)
> Período: {date_min} → {date_max}
{mode_note}
Sem odds BTTS+Over 2.5 disponíveis no dataset — ROI não calculado.

## Definições

| Termo | Descrição |
|---|---|
| `p_dc_conjunta` | P(BTTS AND Over 2.5) extraída da grelha bivariada DC |
| `p_btts_dc` | P(BTTS) = P(home≥1 AND away≥1) da grelha |
| `p_over25_dc` | P(Over 2.5) = P(total≥3) da grelha |
| `p_naive` | `p_btts_dc × p_over25_dc` (assumindo independência) |
| `overlay` | `p_dc_conjunta − p_naive` (excesso de probabilidade conjunta) |
| Resultado real | BTTS real AND Over 2.5 real (FTHG≥1, FTAG≥1, total≥3) |

## Resultados globais

| | Todos os jogos DC | Overlay ≥ {OVERLAY_MIN_BET*100:.0f}% |
|---|---|---|
| N jogos | {n_all:,} | {n_bets:,} |
| WR real (BTTS+O2.5) | {wr_all}% | {wr_bets}% |
| Overlay médio | {avg_overlay_all:+.2f}% | {avg_overlay_bets:+.2f}% |

## Calibração — p_dc_conjunta vs frequência real

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
{cal_full_md}

## Calibração — p_naive vs frequência real (referência)

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
{cal_naive_md}

## Por liga — todos os jogos DC

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
{lg_all_md}

## Por liga — overlay ≥ {OVERLAY_MIN_BET*100:.0f}%

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
{lg_bets_md}

## Interpretação

- **overlay > 0** é o padrão esperado: P(BTTS AND O2.5) é sempre maior que o produto
  das probabilidades marginais porque os eventos são positivamente correlacionados.
- Jogos com overlay elevado têm lambdas altos em ambas as equipas — são os jogos
  onde o modelo DC vê maior expectativa de golo partilhado.
- Sem odds de mercado específicas para BTTS+Over 2.5, não é possível calcular CLV
  nem ROI real. A validação é feita apenas por calibração e frequência relativa.
- **Gate live scan**: overlay ≥ 8% AND ev\\_final\\_over25 ≥ 3% AND liga whitelisted.
- **Activação alertas TG**: n ≥ 100 settled com CLV proxy > +5% no período.

Generated: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC
"""
    out_path.write_text(report, encoding="utf-8")
    print(f"Relatório escrito: {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

    parser = argparse.ArgumentParser(description="BTTS+Over 2.5 backtest")
    parser.add_argument("--data", default="data/historical/matches.csv")
    parser.add_argument("--fast", action="store_true",
                        help="Usar dc_ratings.json actual (in-sample, rápido)")
    args = parser.parse_args()

    csv_path = ROOT / args.data
    if not csv_path.exists():
        print(f"Ficheiro não encontrado: {csv_path}")
        return

    print(f"A carregar dados: {csv_path}")
    df = _load(csv_path)
    print(f"Jogos carregados: {len(df):,} ({df['Div'].nunique()} divisões, {df['Date'].min().date()} → {df['Date'].max().date()})")
    print(f"WR base BTTS+O2.5: {df['btts_over25'].mean()*100:.1f}%  BTTS: {df['btts'].mean()*100:.1f}%  Over 2.5: {df['over25'].mean()*100:.1f}%")

    mode = "fast" if args.fast else "walk-forward"
    if args.fast:
        print("Modo rápido: a usar dc_ratings.json (in-sample)...")
        results = run_fast(df)
    else:
        print("A correr walk-forward (pode demorar vários minutos)...")
        results = run_backtest(df)

    print(f"Previsões geradas: {len(results):,}")

    if results.empty:
        print("Sem resultados.")
        return

    bets = results[results["overlay"] >= OVERLAY_MIN_BET].copy()
    print(f"Jogos com overlay ≥ {OVERLAY_MIN_BET*100:.0f}%: {len(bets):,}")

    out_path = REPORTS_DIR / "btts_over25_backtest.md"
    write_report(results, bets, out_path, mode=mode)


if __name__ == "__main__":
    main()
