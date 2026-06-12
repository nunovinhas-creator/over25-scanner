"""
backtesting/run_sharp1x2_signal.py
------------------------------------
Análise de sinais sharp money em 1X2 a partir de dados históricos.

Sinais testados:
  Q1: pin_drop direto — apostar no outcome com maior queda de odds (PSx/PSCx - 1)
  Q2: pin_drop inverso — apostar no outcome com MENOR queda (maior drift)
  Q3: sinal de empate (pin_drop_d > 5%)
  Q4: divergência B365/Pinnacle — apostar onde Bet365 > Pinnacle em X%

Requer: data/historical/matches.csv com colunas 1X2 (PSH, PSCH, B365H, etc.)
Se as colunas 1X2 não existirem → reporta "indisponíveis" e sai com código 0.

Escreve: backtesting/reports/sharp1x2_signal.md

Correr com:
    python -m backtesting.run_sharp1x2_signal
    python -m backtesting.run_sharp1x2_signal --csv data/historical/matches.csv
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CSV = _ROOT / "data" / "historical" / "matches.csv"
_REPORT_DIR = _ROOT / "backtesting" / "reports"
_REPORT_PATH = _REPORT_DIR / "sharp1x2_signal.md"

_MIN_1X2_ROWS = 500       # min rows to consider 1X2 data usable
_MIN_CELL = 30            # min games per quartile cell
_DRAW_DROP_THRESH = 0.05  # 5% shortening for Q3

_DIV_THRESHOLDS = [0.02, 0.03, 0.05, 0.08, 0.10]
_MIN_DIV_GLOBAL = 100     # min bets globally per threshold row
_MIN_DIV_LEAGUE = 300     # min bets per (league, threshold) cell

# ROI verdict thresholds
_ROI_PROMISING = -2.0     # above this → "promissor"
_N_MEANINGFUL = 3000      # below this → "amostra insuficiente"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, low_memory=False)
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    if "Season" in df.columns:
        df["Season"] = df["Season"].astype(str)
    return df


def _has_1x2(df: pd.DataFrame) -> bool:
    required = ["FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"]
    if not all(c in df.columns for c in required):
        return False
    return int(df[required].notna().all(axis=1).sum()) >= _MIN_1X2_ROWS


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows with full 1X2 data; compute pin_drop and div columns."""
    want = ["Div", "Season", "Date", "HomeTeam", "AwayTeam",
            "FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA",
            "B365H", "B365D", "B365A"]
    out = df[[c for c in want if c in df.columns]].copy()

    for col in ("PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA", "B365H", "B365D", "B365A"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    # pin_drop: positive = odds fell = sharp money in
    for open_col, close_col, drop_col in [
        ("PSH", "PSCH", "pin_drop_h"),
        ("PSD", "PSCD", "pin_drop_d"),
        ("PSA", "PSCA", "pin_drop_a"),
    ]:
        out[drop_col] = out[open_col] / out[close_col] - 1

    out = out.dropna(subset=["FTR", "PSH", "PSCH", "pin_drop_h", "pin_drop_d", "pin_drop_a"])
    return out[out["FTR"].isin(["H", "D", "A"])].copy()


def _close_odds_col(df: pd.DataFrame, outcome_col: str = "picked") -> pd.Series:
    return np.where(
        df[outcome_col] == "H", df["PSCH"],
        np.where(df[outcome_col] == "D", df["PSCD"], df["PSCA"])
    )


def _profits(df: pd.DataFrame) -> np.ndarray:
    """Return +1 × close_odds - 1 if correct, else -1."""
    close = _close_odds_col(df)
    return np.where(df["FTR"] == df["picked"], close - 1, -1.0)


def _roi_stats(df: pd.DataFrame) -> dict:
    profits = _profits(df)
    return {
        "n": len(df),
        "roi_pct": round(float(profits.mean() * 100), 2),
        "wr_pct": round(float((df["FTR"] == df["picked"]).mean() * 100), 1),
        "avg_close_odds": round(float(_close_odds_col(df).mean()), 3),
    }


# ---------------------------------------------------------------------------
# Q1: pin_drop direto
# ---------------------------------------------------------------------------

def _q1_forward(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Back outcome with MAX pin_drop. Returns (by_div, overall)."""
    d = df.copy()
    drops = d[["pin_drop_h", "pin_drop_d", "pin_drop_a"]]
    pick_map = {0: "H", 1: "D", 2: "A"}
    d["picked"] = drops.values.argmax(axis=1)
    d["picked"] = d["picked"].map(pick_map)
    d["max_drop"] = drops.max(axis=1)

    def _stats(grp: pd.DataFrame) -> pd.Series:
        s = _roi_stats(grp)
        s["avg_drop_pct"] = round(float(grp["max_drop"].mean() * 100), 2)
        return pd.Series(s)

    by_div = (
        d.groupby("Div", group_keys=False).apply(_stats).reset_index()
        .rename(columns={"index": "Div"})
    )
    by_div = by_div[by_div["n"] >= _MIN_CELL].sort_values("roi_pct", ascending=False)
    overall = _stats(d).to_frame("overall").T
    return by_div, overall


# ---------------------------------------------------------------------------
# Q2: pin_drop inverso
# ---------------------------------------------------------------------------

def _q2_reverse(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Back outcome with MIN pin_drop (most drift). Returns (by_quartile, overall)."""
    d = df.copy()
    drops = d[["pin_drop_h", "pin_drop_d", "pin_drop_a"]]
    pick_map = {0: "H", 1: "D", 2: "A"}
    d["picked"] = drops.values.argmin(axis=1)   # MIN, not MAX
    d["picked"] = d["picked"].map(pick_map)
    d["min_drop"] = drops.min(axis=1)

    try:
        d["quartile"] = pd.qcut(
            d["min_drop"], 4,
            labels=["Q1 (maior drift+)", "Q2", "Q3", "Q4 (menor drift)"],
        )
    except ValueError:
        d["quartile"] = "único quartil"

    def _stats(grp: pd.DataFrame) -> pd.Series:
        profits = _profits(grp)
        return pd.Series({
            "n": len(grp),
            "min_drop_range": f"{grp['min_drop'].min()*100:.1f}%–{grp['min_drop'].max()*100:.1f}%",
            "roi_pct": round(float(profits.mean() * 100), 2),
            "wr_pct": round(float((grp["FTR"] == grp["picked"]).mean() * 100), 1),
        })

    by_quartile = (
        d.groupby("quartile", observed=True, group_keys=False).apply(_stats)
        .reset_index().rename(columns={"index": "quartile"})
    )
    overall = _roi_stats(d)
    return by_quartile, overall


# ---------------------------------------------------------------------------
# Q3: sinal de empate
# ---------------------------------------------------------------------------

def _q3_draw(df: pd.DataFrame) -> dict:
    signal = df[df["pin_drop_d"] > _DRAW_DROP_THRESH].copy()
    n = len(signal)

    if n < _MIN_CELL:
        return {"usable": False, "n_signal": n,
                "msg": f"apenas {n} jogos com drop > {_DRAW_DROP_THRESH*100:.0f}% — insuficiente"}

    profits = np.where(signal["FTR"] == "D", signal["PSCD"] - 1, -1.0)
    wr = float((signal["FTR"] == "D").mean())
    avg_close = float(signal["PSCD"].mean())
    return {
        "usable": True,
        "n_signal": n,
        "wr_pct": round(wr * 100, 1),
        "breakeven_pct": round(100.0 / avg_close, 1),
        "avg_close_odds": round(avg_close, 3),
        "roi_pct": round(float(profits.mean() * 100), 2),
        "edge_pct": round((wr - 1.0 / avg_close) * 100, 2),
    }


# ---------------------------------------------------------------------------
# Q4: divergência B365 / Pinnacle
# ---------------------------------------------------------------------------

def _q4_divergence(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Divergence signal: bet on outcome where B365x / PSx - 1 > threshold.
    Uses Pinnacle closing odds for fair ROI.
    Returns (by_threshold, by_league).
    """
    needed = ["B365H", "B365D", "B365A", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA", "FTR"]
    if not all(c in df.columns for c in needed):
        return pd.DataFrame(), pd.DataFrame()

    d = df[[c for c in needed + ["Div"] if c in df.columns]].copy()
    for col in needed:
        if col != "FTR":
            d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna().copy()

    if d.empty:
        return pd.DataFrame(), pd.DataFrame()

    # Compute divergence (positive = Bet365 more generous than Pinnacle = potential value)
    d["div_h"] = d["B365H"] / d["PSH"] - 1
    d["div_d"] = d["B365D"] / d["PSD"] - 1
    d["div_a"] = d["B365A"] / d["PSA"] - 1
    d["max_div"] = d[["div_h", "div_d", "div_a"]].max(axis=1)

    pick_map = {0: "H", 1: "D", 2: "A"}
    d["picked"] = d[["div_h", "div_d", "div_a"]].values.argmax(axis=1)
    d["picked"] = d["picked"].map(pick_map)

    n_total = len(d)

    # Global table by threshold
    global_rows = []
    for thresh in _DIV_THRESHOLDS:
        bets = d[d["max_div"] > thresh]
        if len(bets) < _MIN_DIV_GLOBAL:
            continue
        profits = _profits(bets)
        global_rows.append({
            "threshold": f">{thresh*100:.0f}%",
            "n_bets": len(bets),
            "pct_jogos": f"{len(bets)/n_total*100:.1f}%",
            "wr_pct": round(float((bets["FTR"] == bets["picked"]).mean() * 100), 1),
            "avg_close_odds": round(float(_close_odds_col(bets).mean()), 3),
            "roi_pct": round(float(profits.mean() * 100), 2),
        })
    by_threshold = pd.DataFrame(global_rows)

    # League breakdown: all (league, threshold) cells with n >= _MIN_DIV_LEAGUE
    league_rows = []
    if "Div" in d.columns:
        for thresh in _DIV_THRESHOLDS:
            bets = d[d["max_div"] > thresh]
            for div, grp in bets.groupby("Div"):
                if len(grp) < _MIN_DIV_LEAGUE:
                    continue
                profits = _profits(grp)
                league_rows.append({
                    "liga": div,
                    "threshold": f">{thresh*100:.0f}%",
                    "n": len(grp),
                    "wr_pct": round(float((grp["FTR"] == grp["picked"]).mean() * 100), 1),
                    "roi_pct": round(float(profits.mean() * 100), 2),
                })
    by_league = pd.DataFrame(league_rows)
    if not by_league.empty:
        by_league = by_league.sort_values(["threshold", "roi_pct"], ascending=[True, False])

    return by_threshold, by_league


# ---------------------------------------------------------------------------
# Q5: Walk-forward temporal validation of div>3% signal
# ---------------------------------------------------------------------------

_FIXED_THRESH = 0.03  # threshold chosen from training analysis 12 jun 2026

def _q5_walkforward(df: pd.DataFrame) -> list[dict]:
    """
    Walk-forward validation to eliminate selection bias.
    Round 1: train=2122+2223+2324  → validate=2425
    Round 2: train=2122+2223+2324+2425 → validate=2526

    Fixed threshold=0.03 applied to both validation epochs.
    CLV simulado = B365_at_signal / Pin_closing - 1 (measures if B365 was above efficient closing)
    """
    needed = ["B365H","B365D","B365A","PSH","PSD","PSA","PSCH","PSCD","PSCA","FTR","Season"]
    if not all(c in df.columns for c in needed):
        return [{"error": "Colunas B365 ou Season em falta para walk-forward"}]

    d = df[[c for c in needed + (["Div"] if "Div" in df.columns else []) if c in df.columns]].copy()
    for col in [c for c in needed if c not in ("FTR","Season")]:
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d = d.dropna(subset=["B365H","PSH","PSCH","FTR","Season"]).copy()

    d["div_h"] = d["B365H"] / d["PSH"] - 1
    d["div_d"] = d["B365D"] / d["PSD"] - 1
    d["div_a"] = d["B365A"] / d["PSA"] - 1
    d["max_div"] = d[["div_h","div_d","div_a"]].max(axis=1)
    pick_map = {0:"H", 1:"D", 2:"A"}
    d["picked"] = d[["div_h","div_d","div_a"]].values.argmax(axis=1)
    d["picked"] = d["picked"].map(pick_map)

    # CLV simulado: did B365 beat the Pinnacle closing line?
    d["b365_picked"] = np.where(d["picked"]=="H", d["B365H"],
                        np.where(d["picked"]=="D", d["B365D"], d["B365A"]))
    d["pin_close"] = _close_odds_col(d)
    d["clv_sim"] = d["b365_picked"] / d["pin_close"] - 1

    def _eval_thresh(sub: pd.DataFrame, thresh: float) -> Optional[dict]:
        bets = sub[sub["max_div"] > thresh]
        if len(bets) < 30:
            return None
        profits = np.where(bets["FTR"]==bets["picked"], _close_odds_col(bets)-1, -1.0)
        return {
            "n": len(bets),
            "roi_pct": round(float(profits.mean()*100), 2),
            "wr_pct": round(float((bets["FTR"]==bets["picked"]).mean()*100), 1),
            "clv_sim_pct": round(float(bets["clv_sim"].mean()*100), 2),
            "avg_close_odds": round(float(_close_odds_col(bets).mean()), 3),
        }

    def _best_thresh(sub: pd.DataFrame) -> tuple:
        best_t, best_roi = None, -999.0
        for t in _DIV_THRESHOLDS:
            r = _eval_thresh(sub, t)
            if r and r["roi_pct"] > best_roi:
                best_roi, best_t = r["roi_pct"], t
        return best_t, best_roi

    ROUNDS = [
        (["2122","2223","2324"], "2425"),
        (["2122","2223","2324","2425"], "2526"),
    ]

    results = []
    for train_epocs, val_epoc in ROUNDS:
        train = d[d["Season"].isin(train_epocs)]
        val   = d[d["Season"] == val_epoc]

        if len(val) < 30:
            results.append({
                "treino": "+".join(train_epocs), "validação": val_epoc,
                "n_treino": len(train), "n_val": len(val),
                "nota": "época sem dados suficientes",
            })
            continue

        opt_t, opt_roi = _best_thresh(train)
        r_train = _eval_thresh(train, _FIXED_THRESH)
        r_val   = _eval_thresh(val,   _FIXED_THRESH)

        results.append({
            "treino": "+".join(train_epocs),
            "validação": val_epoc,
            "n_treino": len(train),
            "n_val": len(val),
            "threshold_ótimo_treino": f">{opt_t*100:.0f}%" if opt_t else "N/A",
            "roi_ótimo_treino": f"{opt_roi:+.2f}%" if opt_t else "N/A",
            "roi_treino_@3pct": f"{r_train['roi_pct']:+.2f}%" if r_train else "N/A",
            "n_apostas_val": r_val["n"] if r_val else 0,
            "roi_val_@3pct": f"{r_val['roi_pct']:+.2f}%" if r_val else "insuf.",
            "wr_val": f"{r_val['wr_pct']}%" if r_val else "—",
            "clv_sim_val": f"{r_val['clv_sim_pct']:+.2f}%" if r_val else "—",
        })

    return results


# ---------------------------------------------------------------------------
# Q6: Investigate divergence signal anomaly (per-league / per-outcome breakdown)
# ---------------------------------------------------------------------------

def _q6_investigate_div(df: pd.DataFrame, div_filter: Optional[str] = "N1") -> dict:
    """
    Investigate a suspiciously high ROI in one division.
    Breakdowns by season and outcome to detect data artifacts.
    """
    needed = ["B365H","B365D","B365A","PSH","PSD","PSA","PSCH","PSCD","PSCA","FTR","Season","Div"]
    if not all(c in df.columns for c in needed):
        return {"error": "Div ou colunas B365 em falta"}

    sub = df[df["Div"]==div_filter].copy() if div_filter else df.copy()
    if sub.empty:
        return {"error": f"Div '{div_filter}' não encontrada"}

    for col in ["B365H","B365D","B365A","PSH","PSD","PSA","PSCH","PSCD","PSCA"]:
        sub[col] = pd.to_numeric(sub[col], errors="coerce")
    sub = sub.dropna(subset=["B365H","PSH","PSCH","FTR","Season"]).copy()

    sub["div_h"] = sub["B365H"] / sub["PSH"] - 1
    sub["div_d"] = sub["B365D"] / sub["PSD"] - 1
    sub["div_a"] = sub["B365A"] / sub["PSA"] - 1
    sub["max_div"] = sub[["div_h","div_d","div_a"]].max(axis=1)
    pick_map = {0:"H",1:"D",2:"A"}
    sub["picked"] = sub[["div_h","div_d","div_a"]].values.argmax(axis=1)
    sub["picked"] = sub["picked"].map(pick_map)

    bets = sub[sub["max_div"] > _FIXED_THRESH].copy()
    if len(bets) < 10:
        return {"error": f"{div_filter}: apenas {len(bets)} apostas ao threshold {_FIXED_THRESH:.0%}"}

    def _grp_stats(grp: pd.DataFrame) -> pd.Series:
        profits = np.where(grp["FTR"]==grp["picked"], _close_odds_col(grp)-1, -1.0)
        return pd.Series({
            "n": len(grp),
            "wr_pct": round(float((grp["FTR"]==grp["picked"]).mean()*100), 1),
            "roi_pct": round(float(profits.mean()*100), 2),
            "avg_div_pct": round(float(grp["max_div"].mean()*100), 1),
            "avg_close_odds": round(float(_close_odds_col(grp).mean()), 3),
        })

    by_season = (
        bets.groupby("Season", group_keys=False).apply(_grp_stats)
        .reset_index().rename(columns={"index":"Season"})
    )
    by_season = by_season[by_season["n"] >= 5]

    by_outcome = (
        bets.groupby("picked", group_keys=False).apply(_grp_stats)
        .reset_index().rename(columns={"index":"picked"})
    )
    by_outcome = by_outcome[by_outcome["n"] >= 5]

    profits_all = np.where(bets["FTR"]==bets["picked"], _close_odds_col(bets)-1, -1.0)

    # Concentration: any single season drives > 60% of the net profit
    season_profits = []
    for _, grp in bets.groupby("Season"):
        p = np.where(grp["FTR"]==grp["picked"], _close_odds_col(grp)-1, -1.0)
        season_profits.append(p.sum())
    total_profit = sum(season_profits)
    concentrated = (
        len(season_profits) > 1 and total_profit > 0
        and max(season_profits) / total_profit > 0.60
    )

    return {
        "div": div_filter,
        "n_total": len(bets),
        "roi_overall": round(float(profits_all.mean()*100), 2),
        "wr_overall": round(float((bets["FTR"]==bets["picked"]).mean()*100), 1),
        "by_season": by_season,
        "by_outcome": by_outcome,
        "concentrated": concentrated,
        "concentration_note": (
            "⚠ Edge concentrado numa única época — possível anomalia de dados."
            if concentrated else
            "✓ ROI distribuído por múltiplas épocas — padrão consistente."
        ),
    }


# ---------------------------------------------------------------------------
# Conclusion
# ---------------------------------------------------------------------------

def _build_conclusion(
    q1_overall: pd.DataFrame,
    q2_overall: dict,
    q4_by_thresh: pd.DataFrame,
    n_games: int,
) -> list[str]:
    def _roi(val: object) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    q1_roi = _roi(q1_overall["roi_pct"].iloc[0]) if not q1_overall.empty else None
    q2_roi = _roi(q2_overall.get("roi_pct"))
    q2_n   = int(q2_overall.get("n", 0))

    best_div: dict = {}
    if not q4_by_thresh.empty:
        idx = q4_by_thresh["roi_pct"].idxmax()
        best_div = {
            "thresh": q4_by_thresh.loc[idx, "threshold"],
            "roi": float(q4_by_thresh.loc[idx, "roi_pct"]),
            "n": int(q4_by_thresh.loc[idx, "n_bets"]),
        }

    def _verdict(roi: Optional[float], n: int) -> str:
        if roi is None:
            return "dados insuficientes"
        if n < _N_MEANINGFUL:
            return f"amostra pequena (n={n:,})"
        if roi > _ROI_PROMISING:
            return f"**promissor** ({roi:+.2f}%)"
        return f"negativo ({roi:+.2f}%)"

    lines = [
        "---",
        "",
        "## Conclusão — Qual o sinal mais promissor?",
        "",
        f"Análise sobre {n_games:,} jogos.",
        "",
        "| Sinal | ROI global | n | Veredicto |",
        "| --- | --- | --- | --- |",
        f"| pin_drop direto (max drop) | {q1_roi:+.2f}% | {n_games:,} | {_verdict(q1_roi, n_games)} |"
        if q1_roi is not None else "| pin_drop direto | N/A | — | — |",
        f"| pin_drop inverso (min drop) | {q2_roi:+.2f}% | {q2_n:,} | {_verdict(q2_roi, q2_n)} |"
        if q2_roi is not None else "| pin_drop inverso | N/A | — | — |",
    ]

    if best_div:
        lines.append(
            f"| divergência B365/Pin ({best_div['thresh']}) | {best_div['roi']:+.2f}% "
            f"| {best_div['n']:,} | {_verdict(best_div['roi'], best_div['n'])} |"
        )
    else:
        lines.append("| divergência B365/Pin | N/A | — | dados insuficientes |")

    # Final verdict
    candidates = [
        ("pin_drop direto", q1_roi, n_games),
        ("pin_drop inverso", q2_roi, q2_n),
    ]
    if best_div:
        candidates.append(("divergência", best_div["roi"], best_div["n"]))

    usable = [(name, roi, n) for name, roi, n in candidates
              if roi is not None and roi > _ROI_PROMISING and n >= _N_MEANINGFUL]

    lines += [""]
    if usable:
        best_name, best_roi, best_n = max(usable, key=lambda x: x[1])
        lines += [
            f"> **Sinal mais promissor**: {best_name} (ROI {best_roi:+.2f}%, n={best_n:,})",
            "> Validar com backtesting temporal estrito antes de qualquer uso em produção.",
        ]
    else:
        lines += [
            "> **Nenhum sinal com ROI > −2% e n ≥ 3 000.**",
            ">",
            "> Os três sinais testados (pin_drop direto, pin_drop inverso, divergência B365/Pinnacle)",
            "> não demonstram valor preditivo nos dados de abertura/fecho disponíveis.",
            "> **Causa provável**: sem timestamps intraday, o pin_drop agrega toda a pressão da semana",
            "> e perde o sinal de timing que torna o sharp money valioso. A divergência B365/Pinnacle",
            "> pode mascarar diferenças estruturais de linha (B365 tem margens diferentes, não reflete",
            "> necessariamente ineficiência no mesmo mercado).",
            ">",
            "> **Módulo Sharp 1X2 em pausa.** Retomar quando disponíveis dados intraday (ex: Betfair",
            "> Exchange tick-by-tick ou timestamps de aposta da plataforma própria).",
        ]

    return lines


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _fmt_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_Sem dados suficientes._\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep    = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows   = ["| " + " | ".join(str(v) for v in row) + " |" for _, row in df.iterrows()]
    return "\n".join([header, sep] + rows) + "\n"


def _write_report(
    df: pd.DataFrame,
    q1_by_div: pd.DataFrame, q1_overall: pd.DataFrame,
    q2_by_q: pd.DataFrame,   q2_overall: dict,
    q3: dict,
    q4_by_thresh: pd.DataFrame, q4_by_league: pd.DataFrame,
    q5_walkforward: list[dict],
    q6_n1: dict,
    csv_path: Path,
) -> None:
    n = len(df)
    seasons = sorted(df["Season"].unique()) if "Season" in df.columns else []
    divs    = sorted(df["Div"].unique())    if "Div"    in df.columns else []

    lines: list[str] = [
        "# Sharp 1X2 — Análise de Sinais (pin_drop + divergência)",
        "",
        f"Gerado em {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        f"Fonte: `{csv_path.name}` · {n:,} jogos · épocas {seasons} · divisões {divs}",
        "",
        "> **Limitação dos dados**: football-data.co.uk tem apenas odds de abertura e fecho,",
        "> sem timestamps intraday. `pin_drop` = pressão acumulada, não timing. Interpretado com cautela.",
        "",
        "---",
        "",
        "## Q1 — pin_drop direto: apostar no outcome com maior queda de odds",
        "",
        "Estratégia: por cada jogo, apostar no outcome com maior `pin_drop` (PSx/PSCx − 1).",
        "Odds de liquidação: Pinnacle closing (PSCH/PSCD/PSCA).",
        "",
        "### Por divisão",
        "",
        _fmt_table(q1_by_div),
        "",
        "### Global",
        "",
        _fmt_table(q1_overall),
        "",
        "---",
        "",
        "## Q2 — pin_drop inverso: apostar no outcome com MENOR queda (maior drift)",
        "",
        "Estratégia: por cada jogo, apostar no outcome cujas odds mais SUBIRAM ou menos caíram.",
        "Racional: Q2 do Q1 sugere correlação inversa — drift pode indicar mercado a corrigir excesso.",
        "",
        "### Por quartil de min_drop",
        "",
        _fmt_table(q2_by_q),
        "",
        f"**Global**: ROI {q2_overall.get('roi_pct', 'N/A'):+.2f}% "
        f"(n={q2_overall.get('n', 0):,}, WR {q2_overall.get('wr_pct', 'N/A')}%)",
        "",
        "---",
        "",
        "## Q3 — Sinal de empate (pin_drop_d > 5%)",
        "",
        f"Filtro: pin_drop_d > {_DRAW_DROP_THRESH*100:.0f}% (odds de empate encurtaram ≥ 5%)",
        "",
    ]

    if q3["usable"]:
        lines += [
            "| Métrica | Valor |", "| --- | --- |",
            f"| Jogos no sinal | {q3['n_signal']:,} |",
            f"| WR observado (DRAW) | {q3['wr_pct']}% |",
            f"| Breakeven (closing odds) | {q3['breakeven_pct']}% |",
            f"| Edge bruto | {q3['edge_pct']:+.2f}% |",
            f"| Avg closing odds DRAW | {q3['avg_close_odds']} |",
            f"| ROI flat | {q3['roi_pct']:+.2f}% |",
            "",
            "> Resultado reportado apenas — não activar sem backtesting temporal.",
        ]
    else:
        lines += [f"> {q3['msg']}"]

    lines += [
        "",
        "---",
        "",
        "## Q4 — Divergência B365 / Pinnacle: apostar onde Bet365 é mais generosa",
        "",
        "Fórmula: `div_x = B365x / PSx − 1` (positivo = Bet365 acima da Pinnacle abertura).",
        "Estratégia: para cada threshold, apostar no outcome com maior divergência se > threshold.",
        "Odds de liquidação: Pinnacle closing (evita viés de usar as odds do sinal).",
        "",
        "### Global por threshold",
        "",
        _fmt_table(q4_by_thresh) if not (q4_by_thresh is None or q4_by_thresh.empty)
        else "_Colunas B365H/B365D/B365A não disponíveis ou insuficientes._\n",
        "",
        "### Por liga (n ≥ 300 por célula)",
        "",
        _fmt_table(q4_by_league) if not (q4_by_league is None or q4_by_league.empty)
        else "_Sem células com n ≥ 300._\n",
        "",
        "---",
        "",
        "## Q5 — Validação temporal walk-forward (div > 3%)",
        "",
        f"Threshold fixo: **{_FIXED_THRESH*100:.0f}%** (escolhido na análise de treino, 12 jun 2026).",
        "CLV simulado = B365_na_signal / Pin_closing − 1",
        "(positivo → B365 estava acima do fecho eficiente da Pinnacle → potencial value).",
        "",
    ]

    if not q5_walkforward or (len(q5_walkforward) == 1 and "error" in q5_walkforward[0]):
        err = q5_walkforward[0].get("error", "erro desconhecido") if q5_walkforward else "sem dados"
        lines.append(f"_Walk-forward indisponível: {err}_")
    else:
        wf_cols = [
            "treino", "validação", "n_treino", "n_val",
            "threshold_ótimo_treino", "roi_ótimo_treino", "roi_treino_@3pct",
            "n_apostas_val", "roi_val_@3pct", "wr_val", "clv_sim_val",
        ]
        # Build markdown table from results
        present_cols = [c for c in wf_cols if any(c in r for r in q5_walkforward)]
        header = "| " + " | ".join(present_cols) + " |"
        sep    = "| " + " | ".join("---" for _ in present_cols) + " |"
        rows   = []
        for r in q5_walkforward:
            cell = " | ".join(str(r.get(c, "—")) for c in present_cols)
            rows.append(f"| {cell} |")
        lines += [header, sep] + rows
        lines += [
            "",
            "> **Interpretação**: CLV sim > 0% → B365 sistematicamente acima do fecho Pinnacle → edge real.",
            "> ROI val > −2% com n ≥ 300 → sinal sobrevive walk-forward.",
            "> Se ambas as épocas de validação tiverem CLV sim positivo → implementar Gate 4 em produção.",
        ]

    # Q6 — Anomalia N1
    lines += [
        "",
        "---",
        "",
        "## Q6 — Investigação da anomalia N1 (Eredivisie, div > 3%)",
        "",
    ]

    if "error" in q6_n1:
        lines.append(f"_Q6 indisponível: {q6_n1['error']}_")
    else:
        div_label = q6_n1.get("div", "N1")
        lines += [
            f"**{div_label}**: {q6_n1['n_total']:,} apostas · ROI {q6_n1['roi_overall']:+.2f}% · "
            f"WR {q6_n1['wr_overall']}%",
            "",
            f"**Concentração**: {q6_n1['concentration_note']}",
            "",
            "### Por época",
            "",
        ]
        bs = q6_n1.get("by_season")
        lines.append(_fmt_table(bs) if bs is not None and not bs.empty else "_Sem dados por época._\n")
        lines += [
            "",
            "### Por outcome (H / D / A)",
            "",
        ]
        bo = q6_n1.get("by_outcome")
        lines.append(_fmt_table(bo) if bo is not None and not bo.empty else "_Sem dados por outcome._\n")
        if q6_n1.get("concentrated"):
            lines += [
                "",
                "> ⚠ **Anomalia confirmada**: edge concentrado numa única época.",
                "> Excluir N1 das ligas elegíveis até nova validação com dados adicionais.",
            ]
        else:
            lines += [
                "",
                "> ✓ **ROI consistente por múltiplas épocas** — padrão robusto para N1.",
            ]

    lines += _build_conclusion(q1_overall, q2_overall, q4_by_thresh, n)
    lines += ["", "---", "", "_Análise automática — ver `backtesting/run_sharp1x2_signal.py`_"]

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Relatório escrito em {_REPORT_PATH}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Análise de sinais sharp 1X2")
    parser.add_argument("--csv", type=Path, default=_DEFAULT_CSV,
                        help=f"Path ao matches.csv (default: {_DEFAULT_CSV})")
    args = parser.parse_args(argv)
    csv_path: Path = args.csv

    if not csv_path.exists():
        print(f"AVISO: {csv_path} não encontrado — aguarda download.", file=sys.stderr)
        return 0

    print(f"A carregar {csv_path}…")
    df = _load_csv(csv_path)
    print(f"  {len(df):,} linhas, {len(df.columns)} colunas")

    if not _has_1x2(df):
        print(
            "colunas 1X2 indisponíveis, aguarda re-download\n"
            "  → Corre 'Update historical data' com mode=full-1x2 no GitHub Actions."
        )
        return 0

    print("1X2 detectado. A preparar dados…")
    clean = _prepare(df)
    print(f"  {len(clean):,} jogos com 1X2 completo")

    if len(clean) < _MIN_1X2_ROWS:
        print(f"Dados insuficientes ({len(clean)} < {_MIN_1X2_ROWS}).")
        return 0

    print("Q1 (pin_drop direto)…")
    q1_by_div, q1_overall = _q1_forward(clean)

    print("Q2 (pin_drop inverso)…")
    q2_by_q, q2_overall = _q2_reverse(clean)

    print("Q3 (draw signal)…")
    q3 = _q3_draw(clean)

    print("Q4 (divergência B365/Pin)…")
    q4_by_thresh, q4_by_league = _q4_divergence(clean)

    print("Q5 (walk-forward temporal)…")
    q5_walkforward = _q5_walkforward(clean)

    print("Q6 (investigação anomalia N1)…")
    q6_n1 = _q6_investigate_div(clean, div_filter="N1")

    _write_report(
        clean,
        q1_by_div, q1_overall,
        q2_by_q,   q2_overall,
        q3,
        q4_by_thresh, q4_by_league,
        q5_walkforward,
        q6_n1,
        csv_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
