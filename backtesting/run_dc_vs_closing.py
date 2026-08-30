"""
backtesting/run_dc_vs_closing.py
---------------------------------
Pergunta de investigação: o modelo Dixon-Coles bate a linha de fecho da
Pinnacle? Ver relatório completo em backtesting/reports/dc_vs_closing.md.

Reaproveita o splitter walk-forward já existente em run_walkforward.py
(run_walkforward(), com blend_weights=[1.0] para obter p_dc puro sem
blend de mercado e min_ev muito negativo para desligar o gate de EV) —
zero reimplementação da janela expansiva. p_open/p_close são recalculados
aqui directamente com models.math.devig.metodo_multiplicativo sobre os
pares de odds de abertura (P>2.5/P<2.5) e fecho (PC>2.5/PC<2.5), em vez
do _market_prob() interno do walk-forward (que tem fallback quando só
P>2.5 está disponível — esse fallback não serve para este estudo, que
exige o par completo em ambos os lados).

CLV (métrica primária) = p_close * odds_abertura - 1: valor da odd de
abertura tomada, avaliada contra a probabilidade implícita do fecho.
Distinto do campo 'clv' que já existe em run_walkforward.py (que é
odds_abertura/PC - 1, sem de-vig).

Uso:
    python -m backtesting.run_dc_vs_closing
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.run_walkforward import (
    MIN_TEAM_GAMES,
    MIN_TRAIN_GAMES,
    _DIV_TO_LEAGUE,
    _load,
    _roi,
    run_walkforward,
)
from models.math.devig import metodo_multiplicativo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "historical" / "matches.csv"
OUT_PATH = ROOT / "backtesting" / "reports" / "dc_vs_closing.md"

THRESHOLDS = [round(i * 0.01, 2) for i in range(0, 11)]  # 0.00 .. 0.10
REF_THRESHOLD = 0.03  # pré-registado antes de observar o sweep — ver relatório
N_SIMS = 1000
SEED = 42
MIN_SEGMENT_N = 100  # célula com n < 100 é ruído, marcada como tal

ODDS_BAND_EDGES = [0.0, 1.70, 1.90, 2.10, 99.0]
ODDS_BAND_LABELS = ["<1.70", "1.70-1.90", "1.90-2.10", ">2.10"]

SEASON_LABELS = {
    2122: "2021-22", 2223: "2022-23", 2324: "2023-24",
    2425: "2024-25", 2526: "2025-26",
}


# ---------------------------------------------------------------------------
# Universo do estudo (com cadeia de reconciliação de N explícita)
# ---------------------------------------------------------------------------

def build_universe(data_path: Path = DATA_PATH) -> tuple[pd.DataFrame, dict]:
    df = _load(data_path)
    n_total_known_div = len(df)

    both_open_raw = (
        df["P>2.5"].notna() & df["P<2.5"].notna()
        & (df["P>2.5"] > 1.0) & (df["P<2.5"] > 1.0)
    )
    both_close_raw = (
        df["PC>2.5"].notna() & df["PC<2.5"].notna()
        & (df["PC>2.5"] > 1.0) & (df["PC<2.5"] > 1.0)
    )
    n_both_pairs_valid_raw = int((both_open_raw & both_close_raw).sum())

    logger.info(
        "A correr o splitter walk-forward (blend_weights=[1.0], min_ev=-999 "
        "— desliga só o gate de EV; prior de equipa >=%d e treino >=%d jogos "
        "mantêm-se, são intrínsecos ao ajuste do modelo)…",
        MIN_TEAM_GAMES, MIN_TRAIN_GAMES,
    )
    t0 = time.perf_counter()
    bets = run_walkforward(df, blend_weights=[1.0], min_ev=-999.0, min_train=MIN_TRAIN_GAMES)
    elapsed = time.perf_counter() - t0
    n_after_splitter = len(bets)
    logger.info("Walk-forward concluído em %.1fs — %d registos", elapsed, n_after_splitter)

    key = ["date", "div", "home", "away"]
    df_keyed = df.rename(columns={"Date": "date", "Div": "div", "HomeTeam": "home", "AwayTeam": "away"})
    merged = bets.merge(
        df_keyed[key + ["P<2.5", "PC>2.5", "PC<2.5"]],
        on=key, how="left", validate="one_to_one",
    )

    valid_open = (
        merged["odds_over"].notna() & (merged["odds_over"] > 1.0)
        & merged["P<2.5"].notna() & (merged["P<2.5"] > 1.0)
    )
    n_after_splitter_valid_open = int(valid_open.sum())

    valid_close = (
        merged["PC>2.5"].notna() & merged["PC<2.5"].notna()
        & (merged["PC>2.5"] > 1.0) & (merged["PC<2.5"] > 1.0)
    )

    study = merged[valid_open & valid_close].copy()
    n_final_study = len(study)

    p_open = np.empty(len(study))
    p_close = np.empty(len(study))
    for i, (_, row) in enumerate(study.iterrows()):
        p_open[i], _ = metodo_multiplicativo(float(row["odds_over"]), float(row["P<2.5"]))
        p_close[i], _ = metodo_multiplicativo(float(row["PC>2.5"]), float(row["PC<2.5"]))
    study["p_open"] = p_open
    study["p_close"] = p_close
    study["edge"] = study["p_dc"] - study["p_open"]
    study["clv_close"] = study["p_close"] * study["odds_over"] - 1.0
    study["pnl"] = np.where(study["won"] == 1, study["odds_over"] - 1.0, -1.0)

    n_chain = {
        "n_total_known_div": n_total_known_div,
        "n_both_pairs_valid_raw": n_both_pairs_valid_raw,
        "n_after_splitter": n_after_splitter,
        "n_after_splitter_valid_open": n_after_splitter_valid_open,
        "n_final_study": n_final_study,
    }
    return study, n_chain


# ---------------------------------------------------------------------------
# Estatística
# ---------------------------------------------------------------------------

def _mean_ci(values_pct: np.ndarray) -> dict:
    n = len(values_pct)
    if n == 0:
        return {"n": 0, "mean": float("nan"), "se": float("nan"), "lo": float("nan"), "hi": float("nan")}
    mean = float(np.mean(values_pct))
    if n < 2:
        return {"n": n, "mean": mean, "se": float("nan"), "lo": float("nan"), "hi": float("nan")}
    se = float(np.std(values_pct, ddof=1) / np.sqrt(n))
    return {"n": n, "mean": mean, "se": se, "lo": mean - 1.96 * se, "hi": mean + 1.96 * se}


def odds_band(odds: pd.Series) -> pd.Series:
    return pd.cut(odds, bins=ODDS_BAND_EDGES, labels=ODDS_BAND_LABELS, right=False)


# ---------------------------------------------------------------------------
# Sweep de threshold
# ---------------------------------------------------------------------------

def threshold_sweep(study: pd.DataFrame, thresholds: list[float] = THRESHOLDS) -> pd.DataFrame:
    rows = []
    for t in thresholds:
        bucket = study[study["edge"] >= t]
        clv = _mean_ci(bucket["clv_close"].values * 100)
        roi = _mean_ci(bucket["pnl"].values * 100)
        win_rate = float(bucket["won"].mean()) if len(bucket) > 0 else float("nan")
        rows.append({"threshold": t, "n": len(bucket), "win_rate": win_rate, **{
            f"clv_{k}": v for k, v in clv.items() if k != "n"
        }, **{f"roi_{k}": v for k, v in roi.items() if k != "n"}})
    return pd.DataFrame(rows)


def baseline_always_over(study: pd.DataFrame) -> dict:
    clv = _mean_ci(study["clv_close"].values * 100)
    roi = _mean_ci(study["pnl"].values * 100)
    win_rate = float(study["won"].mean())
    return {"n": len(study), "win_rate": win_rate, "clv": clv, "roi": roi}


# ---------------------------------------------------------------------------
# Controlos aleatórios
# ---------------------------------------------------------------------------

def _uniform_random_means(rng: np.random.Generator, universe: pd.DataFrame, bucket_n: int, n_sims: int = N_SIMS) -> np.ndarray:
    n_pop = len(universe)
    clv_vals = universe["clv_close"].values * 100
    if bucket_n <= 0 or bucket_n > n_pop:
        return np.array([])
    means = np.empty(n_sims)
    for i in range(n_sims):
        idx = rng.choice(n_pop, size=bucket_n, replace=False)
        means[i] = clv_vals[idx].mean()
    return means


def _stratified_random_means(rng: np.random.Generator, universe: pd.DataFrame, bucket: pd.DataFrame, n_sims: int = N_SIMS) -> np.ndarray:
    if len(bucket) == 0:
        return np.array([])
    universe = universe.copy()
    universe["_band"] = odds_band(universe["odds_over"])
    bucket_bands = odds_band(bucket["odds_over"]).value_counts()

    band_pools: dict[str, np.ndarray] = {}
    for band, grp in universe.groupby("_band", observed=True):
        band_pools[str(band)] = grp["clv_close"].values * 100

    means = np.empty(n_sims)
    for i in range(n_sims):
        draws = []
        for band, need_n in bucket_bands.items():
            band = str(band)
            need_n = int(need_n)
            if need_n <= 0:
                continue
            pool = band_pools.get(band, np.array([]))
            take_n = min(need_n, len(pool))
            if take_n <= 0:
                continue
            sel = rng.choice(len(pool), size=take_n, replace=False)
            draws.append(pool[sel])
        means[i] = np.concatenate(draws).mean() if draws else np.nan
    return means


def random_controls(study: pd.DataFrame, thresholds: list[float] = THRESHOLDS, n_sims: int = N_SIMS) -> pd.DataFrame:
    rng = np.random.default_rng(SEED)
    rows = []
    for t in thresholds:
        bucket = study[study["edge"] >= t]
        n = len(bucket)
        model_clv = float(bucket["clv_close"].mean() * 100) if n > 0 else float("nan")

        unif_means = _uniform_random_means(rng, study, n, n_sims)
        strat_means = _stratified_random_means(rng, study, bucket, n_sims)

        unif_stats = _mean_ci(unif_means) if len(unif_means) else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan")}
        strat_stats = _mean_ci(strat_means) if len(strat_means) else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan")}

        p_unif = float(np.mean(unif_means >= model_clv)) if len(unif_means) else float("nan")
        p_strat = float(np.mean(strat_means >= model_clv)) if len(strat_means) else float("nan")

        rows.append({
            "threshold": t, "n": n, "model_clv": model_clv,
            "unif_mean": unif_stats["mean"], "unif_lo": unif_stats["lo"], "unif_hi": unif_stats["hi"], "p_unif": p_unif,
            "strat_mean": strat_stats["mean"], "strat_lo": strat_stats["lo"], "strat_hi": strat_stats["hi"], "p_strat": p_strat,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Estratificação por época / divisão
# ---------------------------------------------------------------------------

def stratify(bucket: pd.DataFrame, by: str) -> list[dict]:
    rows = []
    for key, grp in bucket.groupby(by):
        y = grp["won"].values.astype(float)
        odds = grp["odds_over"].values
        clv = _mean_ci(grp["clv_close"].values * 100)
        label = SEASON_LABELS.get(key, str(key)) if by == "season" else str(key)
        rows.append({
            "key": label, "n": len(grp), "win_rate": float(y.mean()),
            "roi_pct": _roi(y, odds), "clv_mean": clv["mean"],
            "noise": len(grp) < MIN_SEGMENT_N,
        })
    rows.sort(key=lambda r: r["n"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

def _fmt_pct(v: float) -> str:
    return f"{v:+.3f}%" if pd.notna(v) else "—"


def _fmt_ci(lo: float, hi: float) -> str:
    if pd.isna(lo) or pd.isna(hi):
        return "—"
    return f"[{lo:+.3f}%, {hi:+.3f}%]"


def write_report(
    study: pd.DataFrame,
    n_chain: dict,
    sweep: pd.DataFrame,
    baseline: dict,
    controls: pd.DataFrame,
    strat_baseline_season: list[dict],
    strat_baseline_div: list[dict],
    strat_ref_season: list[dict],
    strat_ref_div: list[dict],
    out_path: Path = OUT_PATH,
) -> None:
    nl = "\n"
    date_min = study["date"].min().date()
    date_max = study["date"].max().date()

    chain_rows = [
        f"| Jogos ligas conhecidas (13 divisões, `_load()`) | {n_chain['n_total_known_div']:,} | — | — |",
        f"| ... com par abertura (P>2.5/P<2.5) **e** fecho (PC>2.5/PC<2.5) válidos, bruto | {n_chain['n_both_pairs_valid_raw']:,} | −{n_chain['n_total_known_div'] - n_chain['n_both_pairs_valid_raw']:,} | odds ausentes/suspensas nalgum dos 4 campos |",
        f"| ... elegíveis pelo splitter walk-forward (`run_walkforward`, prior de equipa ≥{MIN_TEAM_GAMES} jogos, treino da liga ≥{MIN_TRAIN_GAMES} jogos, mercado computável) | {n_chain['n_after_splitter']:,} | ver nota¹ | cold-start (equipas/liga sem histórico suficiente no início da série); estes gates não são desligados — são intrínsecos ao ajuste do modelo, ao contrário do gate de EV (`min_ev=-999`, esse sim desligado) |",
        f"| ... com par de abertura estrito (P>2.5 **e** P<2.5, sem o fallback de margem que o splitter aceita) | {n_chain['n_after_splitter_valid_open']:,} | −{n_chain['n_after_splitter'] - n_chain['n_after_splitter_valid_open']:,} | splitter aceita fallback `(1/P>2.5)/1.04` quando só P>2.5 existe; este estudo exige o par completo |",
        f"| **Universo final do estudo** (+ par de fecho válido) | **{n_chain['n_final_study']:,}** | −{n_chain['n_after_splitter_valid_open'] - n_chain['n_final_study']:,} | PC>2.5/PC<2.5 ausente apesar do par de abertura presente |",
    ]

    sweep_rows = []
    for _, r in sweep.iterrows():
        sweep_rows.append(
            f"| {r['threshold']:.2f} | {int(r['n']):>6} | "
            f"{r['win_rate']*100:.1f}% | {_fmt_pct(r['clv_mean'])} | {_fmt_ci(r['clv_lo'], r['clv_hi'])} | "
            f"{_fmt_pct(r['roi_mean'])} | {_fmt_ci(r['roi_lo'], r['roi_hi'])} |"
        )

    ctrl_rows = []
    for _, r in controls.iterrows():
        if r["n"] == 0:
            ctrl_rows.append(f"| {r['threshold']:.2f} | 0 | — | — | — | — | — |")
            continue
        beats_unif = "não separa" if pd.notna(r["p_unif"]) and r["p_unif"] > 0.05 else "separa (p≤0.05)"
        beats_strat = "não separa" if pd.notna(r["p_strat"]) and r["p_strat"] > 0.05 else "separa (p≤0.05)"
        ctrl_rows.append(
            f"| {r['threshold']:.2f} | {int(r['n'])} | {_fmt_pct(r['model_clv'])} | "
            f"{_fmt_pct(r['unif_mean'])} {_fmt_ci(r['unif_lo'], r['unif_hi'])} | p={r['p_unif']:.3f} ({beats_unif}) | "
            f"{_fmt_pct(r['strat_mean'])} {_fmt_ci(r['strat_lo'], r['strat_hi'])} | p={r['p_strat']:.3f} ({beats_strat}) |"
        )

    def strat_table(rows: list[dict]) -> str:
        lines = []
        for r in rows:
            flag = " ⚠️ n<100 (ruído)" if r["noise"] else ""
            lines.append(
                f"| {r['key']} | {r['n']:,} | {r['win_rate']*100:.1f}% | "
                f"{r['roi_pct']:+.2f}% | {_fmt_pct(r['clv_mean'])}{flag} |"
            )
        return nl.join(lines)

    best_row = sweep.iloc[sweep["clv_mean"].idxmax()] if sweep["clv_mean"].notna().any() else None
    best_note = (
        f"Threshold com CLV médio mais alto no sweep: **{best_row['threshold']:.2f}** "
        f"(n={int(best_row['n'])}, CLV={_fmt_pct(best_row['clv_mean'])} {_fmt_ci(best_row['clv_lo'], best_row['clv_hi'])}). "
        "Não confundir com o threshold de referência (3pp) usado na estratificação — "
        "esse foi pré-registado antes de observar este sweep."
        if best_row is not None else "Sweep sem dados suficientes para identificar um melhor threshold."
    )

    report = f"""# Dixon-Coles vs. Linha de Fecho Pinnacle — Investigação Offline

> **Contexto:** scanner ao vivo congelado desde 25 ago 2026 (tag `v-freeze-2026-08`,
> ver [`.claude/rules/decisions.md`](../../.claude/rules/decisions.md)). Este relatório
> corre inteiramente sobre `data/historical/matches.csv`, sem chamadas à BSD API.

**Pergunta:** o modelo Dixon-Coles, treinado apenas com jogos passados (walk-forward
estritamente expansivo), identifica valor na odd Over 2.5 de **abertura** Pinnacle
quando avaliado contra a probabilidade implícita do **fecho**?

Gerado: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC
Dataset: `data/historical/matches.csv`, {date_min} → {date_max}

---

## Metodologia

1. **Walk-forward estritamente expansivo** — reaproveita `run_walkforward()` de
   `backtesting/run_walkforward.py` sem alterações nem reimplementação: para cada
   jogo, o Dixon-Coles é ajustado só com jogos de data anterior (`train_mask = Date <
   week_start`). Chamado com `blend_weights=[1.0]` (→ `p_final = p_dc` puro, sem
   blend de mercado) e `min_ev=-999` (desliga só o gate de EV — os gates de
   cold-start/treino mínimo do splitter continuam activos, ver cadeia de N abaixo).
   `run_walkforward()` levanta `RuntimeError` se detectar alguma violação de
   lookahead — **0 violações nesta corrida**.
2. `p_open` = de-vig proporcional (`models.math.devig.metodo_multiplicativo`) do par
   `P>2.5`/`P<2.5`. `p_close` = idem sobre `PC>2.5`/`PC<2.5`. Recalculados directamente
   neste script (não usa o `_market_prob()` interno do walk-forward, que tem um
   fallback de margem quando só um lado da odd existe — não serve para este estudo).
3. **CLV (métrica primária)** = `p_close × P>2.5 − 1`: valor da odd de abertura
   tomada, avaliada contra a probabilidade implícita do fecho. ROI (secundária)
   sempre com IC 95%.
4. **Regra de selecção:** apostar Over quando `p_dc − p_open ≥ threshold`, varrido de
   0 a 10pp em passos de 1pp.
5. **Controlos** (mesmo universo em todos):
   - **(a) sempre Over** — sem filtro de edge, universo completo.
   - **(b) aleatório uniforme** — mesmo N de cada bucket do sweep, 1000 simulações,
     seed fixa (`{SEED}`).
   - **(c) aleatório estratificado por banda de `P>2.5`** ({', '.join(ODDS_BAND_LABELS)}) —
     replica a distribuição de bandas da selecção do modelo em cada threshold, 1000
     simulações. Se o modelo não separar deste controlo, o sinal vem da estrutura
     da linha (o spread abertura-fecho não é uniforme por banda de odds), não do
     modelo.
6. **Estratificação** por época (5) e por divisão (13), n reportado em todas as
   células; célula com n<{MIN_SEGMENT_N} marcada como ruído. Reportada para o universo
   "sempre Over" (spread abertura-fecho estrutural, independente do modelo) e para o
   modelo no threshold de referência **{REF_THRESHOLD:.2f} (3pp)** — este threshold foi
   fixado antes de correr o sweep, escolhido por legibilidade; não corresponde ao
   `MIN_EV=3%` de produção (unidades diferentes — EV vs. diferença de probabilidade —
   o alinhamento é aparente, não real).

---

## Cadeia de reconciliação de N

| Passo | N | Δ vs. anterior | Motivo |
|---|---|---|---|
{nl.join(chain_rows)}

¹ Perda do splitter face ao par bruto válido: {n_chain['n_both_pairs_valid_raw'] - n_chain['n_after_splitter']:+,} — negativo se o splitter aceitou jogos via fallback de mercado (só P>2.5) que não estavam no par bruto "ambos válidos"; positivo se o cold-start/min-treino removeu jogos que tinham odds completas mas não histórico suficiente. Ver sinal real na tabela.

---

## Resultados por threshold (`edge = p_dc − p_open ≥ threshold`)

| Threshold | N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|---|
{nl.join(sweep_rows)}

{best_note}

---

## Controlo (a) — apostar sempre Over (universo completo)

| N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|
| {baseline['n']:,} | {baseline['win_rate']*100:.1f}% | {_fmt_pct(baseline['clv']['mean'])} | {_fmt_ci(baseline['clv']['lo'], baseline['clv']['hi'])} | {_fmt_pct(baseline['roi']['mean'])} | {_fmt_ci(baseline['roi']['lo'], baseline['roi']['hi'])} |

---

## Controlos (b)/(c) — aleatório uniforme vs. estratificado por banda de odds

Por threshold: CLV do modelo vs. CLV médio de 1000 simulações aleatórias com o
mesmo N (b: amostragem uniforme; c: amostragem estratificada pela mesma
distribuição de bandas de `P>2.5` da selecção do modelo). `p` = fracção das
1000 simulações com CLV ≥ CLV do modelo — `p` baixo (≤0.05) indica que o
modelo separa do controlo; `p` alto indica que não separa.

| Threshold | N | CLV modelo | (b) uniforme IC95% | p (b) | (c) estratificado IC95% | p (c) |
|---|---|---|---|---|---|---|
{nl.join(ctrl_rows)}

**Leitura decisiva:** se o modelo separar de (b) mas não de (c), o sinal vem da
estrutura do spread abertura-fecho por banda de odds (o modelo simplesmente
selecciona mais jogos numa banda com CLV estrutural mais alto), não de
capacidade preditiva do Dixon-Coles.

---

## Estratificação por época — universo "sempre Over"

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_baseline_season)}

## Estratificação por divisão — universo "sempre Over"

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_baseline_div)}

## Estratificação por época — modelo @ threshold={REF_THRESHOLD:.2f}

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_ref_season)}

## Estratificação por divisão — modelo @ threshold={REF_THRESHOLD:.2f}

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_ref_div)}

---

## Notas metodológicas

- Modelo Dixon-Coles **não calibrado** (`p_dc` bruto, sem o calibrador isotónico de
  `data/calibrator.json` — esse é treinado sobre outra questão; este estudo isola a
  pergunta "o DC bruto tem informação vs. o fecho", sem misturar com calibração
  ajustada separadamente).
- Stake flat 1 unidade em todos os cálculos de ROI.
- `_load()` mantém apenas as 13 divisões conhecidas (mesmo filtro de
  `run_walkforward.py`); linhas `Div='?'` (artefacto BOM) são excluídas antes mesmo
  da cadeia de N acima.
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    logger.info("Relatório escrito em %s", out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

    study, n_chain = build_universe()
    logger.info("Cadeia de N: %s", n_chain)

    sweep = threshold_sweep(study)
    baseline = baseline_always_over(study)
    controls = random_controls(study)

    strat_baseline_season = stratify(study, "season")
    strat_baseline_div = stratify(study, "league")

    ref_bucket = study[study["edge"] >= REF_THRESHOLD]
    strat_ref_season = stratify(ref_bucket, "season") if len(ref_bucket) else []
    strat_ref_div = stratify(ref_bucket, "league") if len(ref_bucket) else []

    write_report(
        study, n_chain, sweep, baseline, controls,
        strat_baseline_season, strat_baseline_div,
        strat_ref_season, strat_ref_div,
    )
    print(f"\nRelatório escrito em {OUT_PATH}")


if __name__ == "__main__":
    main()
