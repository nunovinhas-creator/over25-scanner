"""
backtesting/run_sharp1x2_signal.py
------------------------------------
Análise do sinal de sharp money em 1X2 a partir de dados históricos.

Responde a 3 perguntas usando os dados de football-data.co.uk:
  Q1: O outcome com maior pin_drop (odds que mais encurtaram) tem ROI positivo?
  Q2: Magnitude do drop como proxy de timing — ROI por quartil de drop?
  Q3: Quando draw odds encurtam >5%, o DRAW tem WR acima do breakeven?

Requer: data/historical/matches.csv com colunas 1X2 (PSH, PSCH, etc.)
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
# Paths
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CSV = _ROOT / "data" / "historical" / "matches.csv"
_REPORT_DIR = _ROOT / "backtesting" / "reports"
_REPORT_PATH = _REPORT_DIR / "sharp1x2_signal.md"

# Minimum non-null rows to consider 1X2 data usable
_MIN_1X2_ROWS = 500

# Minimum games per cell (league/quartile) to report
_MIN_CELL = 30

# Drop threshold for Q3 (draw sharp signal)
_DRAW_DROP_THRESH = 0.05  # 5% shortening


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["Date"], low_memory=False)
    if "Season" in df.columns:
        df["Season"] = df["Season"].astype(str)
    return df


def _has_1x2(df: pd.DataFrame) -> bool:
    """Return True if the dataset has usable 1X2 columns."""
    required = ["FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"]
    if not all(c in df.columns for c in required):
        return False
    non_null = df[required].notna().all(axis=1).sum()
    return int(non_null) >= _MIN_1X2_ROWS


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to rows with full 1X2 data and compute pin_drop if missing."""
    cols = ["Div", "Season", "Date", "HomeTeam", "AwayTeam",
            "FTR", "PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"]
    existing = [c for c in cols if c in df.columns]
    out = df[existing].copy()

    for col in ("PSH", "PSD", "PSA", "PSCH", "PSCD", "PSCA"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    # Compute pin_drop if not already present (positive = odds fell = money in)
    for open_col, close_col, drop_col in [
        ("PSH", "PSCH", "pin_drop_h"),
        ("PSD", "PSCD", "pin_drop_d"),
        ("PSA", "PSCA", "pin_drop_a"),
    ]:
        if drop_col not in out.columns:
            out[drop_col] = out[open_col] / out[close_col] - 1

    out = out.dropna(subset=["FTR", "PSH", "PSCH", "pin_drop_h", "pin_drop_d", "pin_drop_a"])
    out = out[out["FTR"].isin(["H", "D", "A"])].copy()
    return out


def _closing_odds(row: pd.Series) -> float:
    return float(row["PSCH"] if row["picked"] == "H" else
                 (row["PSCD"] if row["picked"] == "D" else row["PSCA"]))


# ---------------------------------------------------------------------------
# Q1: ROI of backing outcome with biggest pin_drop, per division
# ---------------------------------------------------------------------------

def _q1_analysis(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    For each game, back the outcome with the biggest pin_drop.
    Returns (by_div, overall) DataFrames.
    """
    drops = df[["pin_drop_h", "pin_drop_d", "pin_drop_a"]].copy()
    pick_map = {0: "H", 1: "D", 2: "A"}
    df = df.copy()
    df["picked"] = drops.values.argmax(axis=1)
    df["picked"] = df["picked"].map(pick_map)
    df["close_odds"] = df.apply(_closing_odds, axis=1)
    df["profit"] = np.where(df["FTR"] == df["picked"], df["close_odds"] - 1, -1.0)
    df["max_drop"] = drops.max(axis=1)

    def _stats(grp: pd.DataFrame) -> pd.Series:
        n = len(grp)
        roi = grp["profit"].mean() * 100
        wr = (grp["FTR"] == grp["picked"]).mean() * 100
        avg_drop = grp["max_drop"].mean() * 100
        avg_close = grp["close_odds"].mean()
        return pd.Series({
            "n": n, "roi_pct": round(roi, 2),
            "wr_pct": round(wr, 1), "avg_drop_pct": round(avg_drop, 2),
            "avg_close_odds": round(avg_close, 3),
        })

    by_div = (
        df.groupby("Div", group_keys=False)
        .apply(_stats)
        .reset_index()
        .rename(columns={"index": "Div"})
    )
    by_div = by_div[by_div["n"] >= _MIN_CELL].sort_values("roi_pct", ascending=False)

    overall = _stats(df).to_frame("overall").T
    return by_div, overall


# ---------------------------------------------------------------------------
# Q2: ROI by quartile of max pin_drop
# ---------------------------------------------------------------------------

def _q2_analysis(df: pd.DataFrame) -> pd.DataFrame:
    drops = df[["pin_drop_h", "pin_drop_d", "pin_drop_a"]].copy()
    pick_map = {0: "H", 1: "D", 2: "A"}
    df = df.copy()
    df["picked"] = drops.values.argmax(axis=1)
    df["picked"] = df["picked"].map(pick_map)
    df["close_odds"] = df.apply(_closing_odds, axis=1)
    df["profit"] = np.where(df["FTR"] == df["picked"], df["close_odds"] - 1, -1.0)
    df["max_drop"] = drops.max(axis=1)

    try:
        df["quartile"] = pd.qcut(
            df["max_drop"], 4,
            labels=["Q1 (menor drop)", "Q2", "Q3", "Q4 (maior drop)"],
        )
    except ValueError:
        # Not enough distinct values for 4 quartiles
        df["quartile"] = "único quartil"

    def _stats(grp: pd.DataFrame) -> pd.Series:
        n = len(grp)
        roi = grp["profit"].mean() * 100
        wr = (grp["FTR"] == grp["picked"]).mean() * 100
        drop_min = grp["max_drop"].min() * 100
        drop_max = grp["max_drop"].max() * 100
        return pd.Series({
            "n": n,
            "drop_range_pct": f"{drop_min:.1f}%–{drop_max:.1f}%",
            "roi_pct": round(roi, 2),
            "wr_pct": round(wr, 1),
        })

    result = (
        df.groupby("quartile", observed=True, group_keys=False)
        .apply(_stats)
        .reset_index()
        .rename(columns={"index": "quartile"})
    )
    return result


# ---------------------------------------------------------------------------
# Q3: Draw signal — DRAW when pin_drop_d > threshold
# ---------------------------------------------------------------------------

def _q3_analysis(df: pd.DataFrame) -> dict:
    signal = df[df["pin_drop_d"] > _DRAW_DROP_THRESH].copy()
    n_signal = len(signal)

    if n_signal < _MIN_CELL:
        return {
            "n_signal": n_signal,
            "usable": False,
            "msg": f"apenas {n_signal} jogos com drop > {_DRAW_DROP_THRESH*100:.0f}% — insuficiente (mín {_MIN_CELL})",
        }

    wr = (signal["FTR"] == "D").mean()
    avg_close = signal["PSCD"].mean()
    breakeven = 1.0 / avg_close
    roi = (signal["PSCD"].where(signal["FTR"] == "D", 1.0) - 1.0).mean() * 100
    # Correct ROI: win → PSCD - 1, lose → -1
    profits = np.where(signal["FTR"] == "D", signal["PSCD"] - 1, -1.0)
    roi = float(profits.mean() * 100)

    return {
        "n_signal": n_signal,
        "usable": True,
        "wr_pct": round(wr * 100, 1),
        "breakeven_pct": round(breakeven * 100, 1),
        "avg_close_odds": round(avg_close, 3),
        "roi_pct": round(roi, 2),
        "edge_pct": round((wr - breakeven) * 100, 2),
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _fmt_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_Sem dados suficientes._\n"
    header = "| " + " | ".join(str(c) for c in df.columns) + " |"
    sep = "| " + " | ".join("---" for _ in df.columns) + " |"
    rows = []
    for _, row in df.iterrows():
        rows.append("| " + " | ".join(str(v) for v in row.values) + " |")
    return "\n".join([header, sep] + rows) + "\n"


def _write_report(df: pd.DataFrame, q1_by_div: pd.DataFrame, q1_overall: pd.DataFrame,
                  q2: pd.DataFrame, q3: dict, csv_path: Path) -> None:
    n_games = len(df)
    seasons = sorted(df["Season"].unique()) if "Season" in df.columns else []
    divs = sorted(df["Div"].unique()) if "Div" in df.columns else []

    lines = [
        "# Sharp 1X2 — Análise do Sinal pin_drop",
        "",
        f"Gerado em {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC",
        f"Fonte: `{csv_path.name}` · {n_games:,} jogos · épocas {seasons} · divisões {divs}",
        "",
        "> **Aviso de limitação**: os dados football-data.co.uk têm apenas odds de abertura e fecho,",
        "> sem timestamps intraday. O `pin_drop` é um proxy da pressão total sobre o mercado,",
        "> não da proximidade temporal ao KO. Resultados interpretados com esta limitação.",
        "",
        "---",
        "",
        "## Q1 — Outcome com maior pin_drop tem ROI positivo?",
        "",
        "Estratégia: por cada jogo, apostar no outcome cujas odds mais encurtaram (pin_drop máximo).",
        "Odds usadas: Pinnacle closing (PSCH/PSCD/PSCA).",
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
        "## Q2 — Magnitude do drop como proxy de timing (ROI por quartil)",
        "",
        "Jogos ordenados por max(pin_drop_h, pin_drop_d, pin_drop_a).",
        "Q4 = jogos com maior drop total — maior concentração de sharp money.",
        "",
        _fmt_table(q2),
        "",
        "> **Limitação**: drop maior não implica necessariamente aposta mais tardia.",
        "> Sem dados intraday, esta tabela é a melhor aproximação disponível.",
        "",
        "---",
        "",
        "## Q3 — Sinal de empate (DRAW quando pin_drop_d > 5%)",
        "",
        f"Filtro: pin_drop_d > {_DRAW_DROP_THRESH*100:.0f}% (odds de empate encurtaram ≥5%)",
        "",
    ]

    if q3["usable"]:
        lines += [
            f"| Métrica | Valor |",
            f"| --- | --- |",
            f"| Jogos no sinal | {q3['n_signal']:,} |",
            f"| WR observado (DRAW) | {q3['wr_pct']}% |",
            f"| Breakeven a closing odds | {q3['breakeven_pct']}% |",
            f"| Edge bruto | {q3['edge_pct']:+.2f}% |",
            f"| Avg. closing odds DRAW | {q3['avg_close_odds']} |",
            f"| ROI flat (closing odds) | {q3['roi_pct']:+.2f}% |",
            "",
            (
                "> **Estado**: resultado reportado apenas — não activar automaticamente."
                " Validar com backtesting temporal antes de qualquer uso em produção."
            ),
        ]
    else:
        lines += [
            f"> {q3['msg']}",
            "",
            "> Dados insuficientes para análise Q3 — aguarda re-download com colunas 1X2.",
        ]

    lines += ["", "---", "", "_Análise automática — ver `backtesting/run_sharp1x2_signal.py`_"]

    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    _REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Relatório escrito em {_REPORT_PATH}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Análise do sinal sharp 1X2")
    parser.add_argument(
        "--csv", type=Path, default=_DEFAULT_CSV,
        help=f"Path ao matches.csv (default: {_DEFAULT_CSV})",
    )
    args = parser.parse_args(argv)

    csv_path: Path = args.csv

    if not csv_path.exists():
        print(f"AVISO: {csv_path} não encontrado — aguarda download do histórico.", file=sys.stderr)
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

    print("Colunas 1X2 detectadas. A preparar dados…")
    clean = _prepare(df)
    print(f"  {len(clean):,} jogos com 1X2 completo")

    if len(clean) < _MIN_1X2_ROWS:
        print(f"Dados insuficientes ({len(clean)} < {_MIN_1X2_ROWS}) — aguarda re-download.")
        return 0

    print("Q1…")
    q1_by_div, q1_overall = _q1_analysis(clean)

    print("Q2…")
    q2 = _q2_analysis(clean)

    print("Q3…")
    q3 = _q3_analysis(clean)

    _write_report(clean, q1_by_div, q1_overall, q2, q3, csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
