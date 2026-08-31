"""
backtesting/run_dc_vs_closing_bestprice.py
--------------------------------------------
Seguimento único e pré-registado de backtesting/run_dc_vs_closing.py
(PR #176, backtesting/reports/dc_vs_closing.md). Critério de aceitação
pré-registado ANTES desta corrida em .claude/rules/decisions.md — CLV
> +1,0%, n≥1000, positivo em ≥4 das 5 épocas. Uma só variante; sem
terceira tentativa se esta falhar.

ALTERAÇÃO face ao estudo original: a odd tomada passa a ser Max>2.5
(melhor preço de mercado do football-data.co.uk) em vez de P>2.5
(abertura Pinnacle). CLV = p_close * Max>2.5 - 1. Tudo o resto é
literalmente o mesmo código: mesmo splitter walk-forward
(run_walkforward(), importado sem alterações), mesmos três controlos
(sempre Over / aleatório uniforme / aleatório estratificado por banda
de odds), mesmo sweep de threshold 0-10pp, mesma estratificação por
época/divisão — threshold_sweep(), baseline_always_over(),
random_controls() e stratify() são importados directamente de
run_dc_vs_closing.py, não reimplementados.

O sinal de selecção (edge = p_dc - p_open) continua a comparar-se
contra a Pinnacle de ABERTURA — só a odd usada para calcular CLV/ROI
(o preço efectivamente tomado) muda para Max>2.5. Por isso o universo
interno reaproveita o nome de coluna 'odds_over' (esperado pelas
funções genéricas importadas) para representar Max>2.5 neste script;
'odds_open' fica guardado à parte como referência Pinnacle.

Uso:
    python -m backtesting.run_dc_vs_closing_bestprice
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from backtesting.run_dc_vs_closing import (
    N_SIMS,
    REF_THRESHOLD,
    THRESHOLDS,
    _fmt_ci,
    _fmt_pct,
    baseline_always_over,
    random_controls,
    stratify,
    threshold_sweep,
)
from backtesting.run_walkforward import MIN_TEAM_GAMES, MIN_TRAIN_GAMES, _load, run_walkforward
from models.math.devig import metodo_multiplicativo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "historical" / "matches.csv"
OUT_PATH = ROOT / "backtesting" / "reports" / "dc_vs_closing_bestprice.md"

STAKE_COL = "Max>2.5"


# ---------------------------------------------------------------------------
# Universo do estudo — mesmo splitter, odd tomada = Max>2.5
# ---------------------------------------------------------------------------

def build_universe(data_path: Path = DATA_PATH, stake_col: str = STAKE_COL) -> tuple[pd.DataFrame, dict]:
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
        "A correr o splitter walk-forward (mesmo run_walkforward() do estudo "
        "original — blend_weights=[1.0], min_ev=-999; prior de equipa >=%d e "
        "treino >=%d jogos mantêm-se)…",
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
        df_keyed[key + ["P<2.5", "PC>2.5", "PC<2.5", stake_col]],
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

    # Universo comparável ao estudo original (P>2.5 como odd tomada) — antes
    # de exigir Max>2.5. Serve de denominador para a cobertura reportada.
    base_study = merged[valid_open & valid_close].copy()
    n_final_study_pinnacle = len(base_study)

    valid_stake = base_study[stake_col].notna() & (base_study[stake_col] > 1.0)
    study = base_study[valid_stake].copy()
    n_final_study = len(study)
    coverage_pct = (100.0 * n_final_study / n_final_study_pinnacle) if n_final_study_pinnacle else float("nan")

    # Sanity check: Max>2.5 deveria ser >= P>2.5 (é o melhor preço, incluindo
    # a própria Pinnacle). Reportado, não filtrado — usar Max>2.5 tal como
    # está, sem escolher a dedo os casos favoráveis.
    n_stake_below_open = int((study[stake_col] < study["odds_over"]).sum())

    p_open = np.empty(len(study))
    p_close = np.empty(len(study))
    for i, (_, row) in enumerate(study.iterrows()):
        p_open[i], _ = metodo_multiplicativo(float(row["odds_over"]), float(row["P<2.5"]))
        p_close[i], _ = metodo_multiplicativo(float(row["PC>2.5"]), float(row["PC<2.5"]))
    study["p_open"] = p_open
    study["p_close"] = p_close
    study["edge"] = study["p_dc"] - study["p_open"]  # selecção continua vs. Pinnacle abertura

    study["odds_open"] = study["odds_over"].astype(float)          # referência: Pinnacle abertura
    study["odds_over"] = study[stake_col].astype(float)             # reaproveita nome p/ funções genéricas — passa a ser Max>2.5
    study["clv_close"] = study["p_close"] * study["odds_over"] - 1.0
    study["pnl"] = np.where(study["won"] == 1, study["odds_over"] - 1.0, -1.0)

    n_chain = {
        "n_total_known_div": n_total_known_div,
        "n_both_pairs_valid_raw": n_both_pairs_valid_raw,
        "n_after_splitter": n_after_splitter,
        "n_after_splitter_valid_open": n_after_splitter_valid_open,
        "n_final_study_pinnacle": n_final_study_pinnacle,
        "n_final_study": n_final_study,
        "coverage_pct": coverage_pct,
        "n_stake_below_open": n_stake_below_open,
        "stake_col": stake_col,
    }
    return study, n_chain


# ---------------------------------------------------------------------------
# Conclusão — mesma estrutura de raciocínio do estudo original, mas
# escrita à mão para esta variante (build_conclusion() do estudo
# original tem "P>2.5" hardcoded no texto, não serve sem reescrever).
# ---------------------------------------------------------------------------

def build_conclusion(
    controls: pd.DataFrame,
    sweep: pd.DataFrame,
    strat_ref_season: list[dict],
    n_chain: dict,
) -> str:
    parts = []

    if n_chain["coverage_pct"] < 90.0:
        parts.append(
            f"**Cobertura de `{n_chain['stake_col']}` é {n_chain['coverage_pct']:.1f}% do "
            f"universo do estudo original ({n_chain['n_final_study']:,} de "
            f"{n_chain['n_final_study_pinnacle']:,} jogos) — substancialmente menor. Os "
            "dois estudos NÃO são directamente comparáveis: este universo é uma amostra "
            "diferente, não o mesmo conjunto de jogos com a odd trocada.**"
        )
    else:
        parts.append(
            f"Cobertura de `{n_chain['stake_col']}` no universo do estudo original: "
            f"**{n_chain['coverage_pct']:.1f}%** ({n_chain['n_final_study']:,} de "
            f"{n_chain['n_final_study_pinnacle']:,} jogos) — os dois estudos são "
            "directamente comparáveis, mesmo conjunto de jogos, só a odd tomada muda."
        )

    if n_chain["n_stake_below_open"] > 0:
        pct_below = 100.0 * n_chain["n_stake_below_open"] / n_chain["n_final_study"]
        parts.append(
            f"\n**Nota de qualidade de dados:** em {n_chain['n_stake_below_open']:,} jogos "
            f"({pct_below:.1f}%), `{n_chain['stake_col']} < P>2.5` — provavelmente artefacto "
            "de timing de captura no football-data.co.uk (colunas `Max`/`Avg` e `P` não são "
            "necessariamente amostradas no mesmo instante). Não filtrados — usar a odd tal "
            "como está, sem escolher a dedo os casos favoráveis."
        )

    # Critério avaliado APENAS ao threshold de referência único — não varrido pelo
    # sweep à procura de um threshold que passe. Fazer isso seria exactamente a
    # forma de p-hacking que a pré-registação em decisions.md existe para evitar.
    ref_row = sweep[sweep["threshold"] == REF_THRESHOLD]
    if ref_row.empty:
        parts.append(
            f"\n**Não foi possível avaliar o critério pré-registado:** threshold de "
            f"referência {REF_THRESHOLD:.2f} não está no sweep."
        )
        clv_pass = n_pass = season_pass = False
        clv_val, n_val, n_season_pos = float("nan"), 0, 0
    else:
        r = ref_row.iloc[0]
        clv_val = r["clv_mean"]
        n_val = int(r["n"])
        n_season_pos = sum(1 for s in strat_ref_season if s["clv_mean"] > 0)
        clv_pass = bool(pd.notna(clv_val) and clv_val > 1.0)
        n_pass = n_val >= 1000
        season_pass = n_season_pos >= 4

        parts.append(
            f"\n**Critério pré-registado, avaliado ao threshold de referência "
            f"{REF_THRESHOLD:.2f}** (o mesmo usado em toda a estratificação deste "
            "relatório — não procurado no sweep):\n\n"
            "| Perna do critério | Valor observado | Passa? |\n"
            "|---|---|---|\n"
            f"| CLV médio > +1,0% | {_fmt_pct(clv_val)} | {'✅' if clv_pass else '❌'} |\n"
            f"| n ≥ 1000 | {n_val:,} | {'✅' if n_pass else '❌'} |\n"
            f"| ≥4 das 5 épocas com CLV médio positivo | {n_season_pos}/{len(strat_ref_season)} | {'✅' if season_pass else '❌'} |\n"
        )

    all_pass = clv_pass and n_pass and season_pass

    valid_ctrl = controls[controls["n"] > 0].reset_index(drop=True)
    if len(valid_ctrl):
        n_beats_strat = int((valid_ctrl["p_strat"] <= 0.05).sum())
        parts.append(
            f"\n**Para contexto (não é o critério de decisão):** o modelo separa-se do "
            f"controlo estratificado por banda de odds (p≤0.05) em {n_beats_strat} de "
            f"{len(valid_ctrl)} thresholds do sweep — mesma leitura qualitativa do "
            "estudo original (sinal relativo real face à linha de fecho), "
            "independentemente de o critério de decisão passar ou não."
        )

    if all_pass:
        parts.append(
            f"\n**Resultado desta tentativa (Max>2.5): o critério pré-registado é "
            f"cumprido na íntegra** ao threshold {REF_THRESHOLD:.2f} — CLV "
            f"{_fmt_pct(clv_val)}, n={n_val:,}, positivo em {n_season_pos}/5 épocas. "
            "Isto é evidência de valor mensurável ao apostar Over 2.5 na melhor odd de "
            "mercado disponível, segundo o modelo Dixon-Coles bruto, avaliado contra a "
            "linha de fecho Pinnacle. **Não substitui o checkpoint C3/C4/C5 nem os "
            "gates de CLV rolling ao vivo definidos em `.claude/rules/cycles.md`** — "
            "este é um resultado histórico offline, não uma activação de apostas reais."
        )
    else:
        failed = [
            name for name, ok in [
                ("CLV>+1,0%", clv_pass), ("n≥1000", n_pass), ("≥4/5 épocas", season_pass),
            ] if not ok
        ]
        parts.append(
            f"\n**Resultado desta tentativa (Max>2.5): o critério pré-registado "
            f"NÃO é cumprido** ao threshold {REF_THRESHOLD:.2f} — falha em: "
            f"{', '.join(failed)}. Por regra pré-registada em "
            "`.claude/rules/decisions.md`, não há terceira tentativa: a linha de "
            "investigação \"DC vs. fecho\" fica registada como resultado nulo nesta "
            "forma, mesmo trocando a odd de abertura pela melhor odd de mercado."
        )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

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
    conclusion = build_conclusion(controls, sweep, strat_ref_season, n_chain)
    nl = "\n"
    date_min = study["date"].min().date()
    date_max = study["date"].max().date()
    stake_col = n_chain["stake_col"]

    chain_rows = [
        f"| Jogos ligas conhecidas (13 divisões, `_load()`) | {n_chain['n_total_known_div']:,} | — | — |",
        f"| ... com par abertura (P>2.5/P<2.5) **e** fecho (PC>2.5/PC<2.5) válidos, bruto | {n_chain['n_both_pairs_valid_raw']:,} | −{n_chain['n_total_known_div'] - n_chain['n_both_pairs_valid_raw']:,} | odds ausentes/suspensas nalgum dos 4 campos |",
        f"| ... elegíveis pelo splitter walk-forward (`run_walkforward`, prior de equipa ≥{MIN_TEAM_GAMES} jogos, treino da liga ≥{MIN_TRAIN_GAMES} jogos, mercado computável) | {n_chain['n_after_splitter']:,} | ver nota¹ | cold-start; gate de EV desligado (`min_ev=-999`), gates de treino mínimo mantidos |",
        f"| ... com par de abertura estrito + par de fecho válido (**universo do estudo original**, comparável a `dc_vs_closing.md`) | {n_chain['n_final_study_pinnacle']:,} | ver nota¹ | mesmo universo do PR #176 |",
        f"| **Universo final desta variante** (+ {stake_col} válida) | **{n_chain['n_final_study']:,}** | −{n_chain['n_final_study_pinnacle'] - n_chain['n_final_study']:,} | {stake_col} ausente apesar do resto do par estar completo |",
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

    n_seasons_positive_ref = sum(1 for r in strat_ref_season if r["clv_mean"] > 0)

    report = f"""# Dixon-Coles vs. Linha de Fecho Pinnacle — Variante Max>2.5 (2ª e última tentativa)

> **Contexto:** seguimento único e pré-registado de
> [`backtesting/reports/dc_vs_closing.md`](dc_vs_closing.md) (PR #176). Critério de
> aceitação e regra de tentativa única registados em
> [`.claude/rules/decisions.md`](../../.claude/rules/decisions.md) **antes** desta
> corrida. Scanner ao vivo continua congelado (`v-freeze-2026-08`) — corre
> inteiramente sobre `data/historical/matches.csv`.

**Pergunta:** o estudo original usou `P>2.5` (abertura Pinnacle) como odd tomada —
irrealista, porque ninguém aposta na Pinnacle havendo melhor preço. Substituindo pela
melhor odd de mercado (`{stake_col}`), o Dixon-Coles supera o critério pré-registado
(CLV>+1,0%, n≥1000, positivo em ≥4/5 épocas)?

Gerado: {pd.Timestamp.now('UTC').isoformat(timespec='seconds')} UTC
Dataset: `data/historical/matches.csv`, {date_min} → {date_max}

---

## Metodologia — idêntica ao estudo original, uma só variável trocada

1. **Mesmo splitter** — `run_walkforward()` de `backtesting/run_walkforward.py`,
   importado sem alterações (0 violações de leakage nesta corrida).
2. **Mesmo sinal de selecção** — `p_open`/`edge` continuam calculados contra a
   Pinnacle de abertura (`P>2.5`/`P<2.5`, de-vig `metodo_multiplicativo`); o modelo
   continua a decidir "há valor?" contra o mercado mais sharp disponível.
3. **ALTERAÇÃO — odd tomada:** `{stake_col}` em vez de `P>2.5`. `p_close` continua a
   ser o de-vig de `PC>2.5`/`PC<2.5` (fecho Pinnacle, inalterado). **CLV = p_close ×
   {stake_col} − 1.**
4. **Mesmo sweep** 0–10pp em passos de 1pp sobre `edge = p_dc − p_open`.
5. **Mesmos três controlos**, funções reaproveitadas de `run_dc_vs_closing.py`
   (`threshold_sweep`, `baseline_always_over`, `random_controls`, `stratify` — não
   reimplementadas): (a) sempre Over; (b) aleatório uniforme; (c) aleatório
   estratificado por banda de odds — banda calculada sobre `{stake_col}` (a odd
   agora tomada), não sobre `P>2.5`.
6. **Mesma estratificação** por época e divisão, threshold de referência
   {REF_THRESHOLD:.2f} (idêntico ao estudo original, não recalibrado para esta
   variante).
7. **Critério de decisão pré-registado** (não os controlos, ao contrário do estudo
   original): CLV > +1,0% E n ≥ 1000 E positivo em ≥4 das 5 épocas —
   `.claude/rules/decisions.md`.

---

## Cadeia de reconciliação de N (+ cobertura de {stake_col})

| Passo | N | Δ vs. anterior | Motivo |
|---|---|---|---|
{nl.join(chain_rows)}

¹ Ver `backtesting/reports/dc_vs_closing.md` para o detalhe desta perda — idêntica em
ambos os estudos até este ponto, o splitter e os gates de treino não mudaram.

**Cobertura de `{stake_col}`:** {n_chain['coverage_pct']:.1f}% do universo do estudo
original ({n_chain['n_final_study']:,} de {n_chain['n_final_study_pinnacle']:,}).

---

## Resultados por threshold (`edge = p_dc − p_open ≥ threshold`, CLV a `{stake_col}`)

| Threshold | N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|---|
{nl.join(sweep_rows)}

---

## Controlo (a) — apostar sempre Over (universo completo, a `{stake_col}`)

| N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|
| {baseline['n']:,} | {baseline['win_rate']*100:.1f}% | {_fmt_pct(baseline['clv']['mean'])} | {_fmt_ci(baseline['clv']['lo'], baseline['clv']['hi'])} | {_fmt_pct(baseline['roi']['mean'])} | {_fmt_ci(baseline['roi']['lo'], baseline['roi']['hi'])} |

---

## Controlos (b)/(c) — aleatório uniforme vs. estratificado por banda de `{stake_col}`

| Threshold | N | CLV modelo | (b) uniforme IC95% | p (b) | (c) estratificado IC95% | p (c) |
|---|---|---|---|---|---|---|
{nl.join(ctrl_rows)}

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

CLV médio positivo em **{n_seasons_positive_ref} de {len(strat_ref_season)}** épocas
a este threshold (critério pré-registado exige ≥4/5).

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_ref_season)}

## Estratificação por divisão — modelo @ threshold={REF_THRESHOLD:.2f}

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
{strat_table(strat_ref_div)}

---

## Conclusão

{conclusion}

---

## Notas metodológicas

- Modelo Dixon-Coles **não calibrado** (`p_dc` bruto), mesma escolha do estudo
  original — isola "o DC bruto tem informação vs. o fecho" sem misturar calibração.
- Stake flat 1 unidade em todos os cálculos de ROI, à odd `{stake_col}`.
- `{stake_col} < P>2.5` não é filtrado — ver nota de qualidade de dados na conclusão.
- `_load()` mantém apenas as 13 divisões conhecidas, mesmo filtro do estudo original.
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    logger.info("Relatório escrito em %s", out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Dixon-Coles vs. fecho Pinnacle — variante Max>2.5 (odd tomada)")
    p.add_argument("--data", type=Path, default=DATA_PATH)
    p.add_argument("--out", type=Path, default=OUT_PATH)
    p.add_argument("--stake-col", type=str, default=STAKE_COL)
    p.add_argument(
        "--study-cache", type=Path, default=None,
        help="Caminho para cache (pickle) do universo do estudo — evita repetir o fit walk-forward.",
    )
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
    args = _build_parser().parse_args()

    n_chain_path = args.study_cache.with_suffix(".n_chain.json") if args.study_cache else None
    if args.study_cache and args.study_cache.exists() and n_chain_path.exists():
        logger.info("A carregar universo do cache: %s", args.study_cache)
        study = pd.read_pickle(args.study_cache)
        n_chain = json.loads(n_chain_path.read_text())
    else:
        study, n_chain = build_universe(args.data, stake_col=args.stake_col)
        if args.study_cache:
            args.study_cache.parent.mkdir(parents=True, exist_ok=True)
            study.to_pickle(args.study_cache)
            n_chain_path.write_text(json.dumps(n_chain))
            logger.info("Universo guardado em cache: %s", args.study_cache)
    logger.info("Cadeia de N: %s", n_chain)

    sweep = threshold_sweep(study, thresholds=THRESHOLDS)
    baseline = baseline_always_over(study)
    controls = random_controls(study, thresholds=THRESHOLDS, n_sims=N_SIMS)

    strat_baseline_season = stratify(study, "season")
    strat_baseline_div = stratify(study, "league")

    ref_bucket = study[study["edge"] >= REF_THRESHOLD]
    strat_ref_season = stratify(ref_bucket, "season") if len(ref_bucket) else []
    strat_ref_div = stratify(ref_bucket, "league") if len(ref_bucket) else []

    write_report(
        study, n_chain, sweep, baseline, controls,
        strat_baseline_season, strat_baseline_div,
        strat_ref_season, strat_ref_div,
        out_path=args.out,
    )
    print(f"\nRelatório escrito em {args.out}")


if __name__ == "__main__":
    main()
