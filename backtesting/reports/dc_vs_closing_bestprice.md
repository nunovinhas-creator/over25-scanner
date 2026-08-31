# Dixon-Coles vs. Linha de Fecho Pinnacle — Variante Max>2.5 (2ª e última tentativa)

> **Contexto:** seguimento único e pré-registado de
> [`backtesting/reports/dc_vs_closing.md`](dc_vs_closing.md) (PR #176). Critério de
> aceitação e regra de tentativa única registados em
> [`.claude/rules/decisions.md`](../../.claude/rules/decisions.md) **antes** desta
> corrida. Scanner ao vivo continua congelado (`v-freeze-2026-08`) — corre
> inteiramente sobre `data/historical/matches.csv`.

**Pergunta:** o estudo original usou `P>2.5` (abertura Pinnacle) como odd tomada —
irrealista, porque ninguém aposta na Pinnacle havendo melhor preço. Substituindo pela
melhor odd de mercado (`Max>2.5`), o Dixon-Coles supera o critério pré-registado
(CLV>+1,0%, n≥1000, positivo em ≥4/5 épocas)?

Gerado: 2026-08-31T06:32:37+00:00 UTC
Dataset: `data/historical/matches.csv`, 2021-08-27 → 2026-01-14

---

## Metodologia — idêntica ao estudo original, uma só variável trocada

1. **Mesmo splitter** — `run_walkforward()` de `backtesting/run_walkforward.py`,
   importado sem alterações (0 violações de leakage nesta corrida).
2. **Mesmo sinal de selecção** — `p_open`/`edge` continuam calculados contra a
   Pinnacle de abertura (`P>2.5`/`P<2.5`, de-vig `metodo_multiplicativo`); o modelo
   continua a decidir "há valor?" contra o mercado mais sharp disponível.
3. **ALTERAÇÃO — odd tomada:** `Max>2.5` em vez de `P>2.5`. `p_close` continua a
   ser o de-vig de `PC>2.5`/`PC<2.5` (fecho Pinnacle, inalterado). **CLV = p_close ×
   Max>2.5 − 1.**
4. **Mesmo sweep** 0–10pp em passos de 1pp sobre `edge = p_dc − p_open`.
5. **Mesmos três controlos**, funções reaproveitadas de `run_dc_vs_closing.py`
   (`threshold_sweep`, `baseline_always_over`, `random_controls`, `stratify` — não
   reimplementadas): (a) sempre Over; (b) aleatório uniforme; (c) aleatório
   estratificado por banda de odds — banda calculada sobre `Max>2.5` (a odd
   agora tomada), não sobre `P>2.5`.
6. **Mesma estratificação** por época e divisão, threshold de referência
   0.03 (idêntico ao estudo original, não recalibrado para esta
   variante).
7. **Critério de decisão pré-registado** (não os controlos, ao contrário do estudo
   original): CLV > +1,0% E n ≥ 1000 E positivo em ≥4 das 5 épocas —
   `.claude/rules/decisions.md`.

---

## Cadeia de reconciliação de N (+ cobertura de Max>2.5)

| Passo | N | Δ vs. anterior | Motivo |
|---|---|---|---|
| Jogos ligas conhecidas (13 divisões, `_load()`) | 23,765 | — | — |
| ... com par abertura (P>2.5/P<2.5) **e** fecho (PC>2.5/PC<2.5) válidos, bruto | 21,012 | −2,753 | odds ausentes/suspensas nalgum dos 4 campos |
| ... elegíveis pelo splitter walk-forward (`run_walkforward`, prior de equipa ≥5 jogos, treino da liga ≥50 jogos, mercado computável) | 19,687 | ver nota¹ | cold-start; gate de EV desligado (`min_ev=-999`), gates de treino mínimo mantidos |
| ... com par de abertura estrito + par de fecho válido (**universo do estudo original**, comparável a `dc_vs_closing.md`) | 19,684 | ver nota¹ | mesmo universo do PR #176 |
| **Universo final desta variante** (+ Max>2.5 válida) | **19,684** | −0 | Max>2.5 ausente apesar do resto do par estar completo |

¹ Ver `backtesting/reports/dc_vs_closing.md` para o detalhe desta perda — idêntica em
ambos os estudos até este ponto, o splitter e os gates de treino não mudaram.

**Cobertura de `Max>2.5`:** 100.0% do universo do estudo
original (19,684 de 19,684).

---

## Resultados por threshold (`edge = p_dc − p_open ≥ threshold`, CLV a `Max>2.5`)

| Threshold | N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|---|
| 0.00 |   8635 | 51.5% | -2.435% | [-2.557%, -2.314%] | -1.937% | [-4.009%, +0.135%] |
| 0.01 |   7658 | 51.5% | -2.413% | [-2.542%, -2.284%] | -1.961% | [-4.160%, +0.237%] |
| 0.02 |   6767 | 51.8% | -2.395% | [-2.532%, -2.257%] | -1.474% | [-3.809%, +0.861%] |
| 0.03 |   5935 | 51.5% | -2.326% | [-2.474%, -2.179%] | -1.952% | [-4.446%, +0.543%] |
| 0.04 |   5134 | 50.7% | -2.285% | [-2.445%, -2.125%] | -3.235% | [-5.922%, -0.548%] |
| 0.05 |   4445 | 50.7% | -2.250% | [-2.423%, -2.077%] | -3.348% | [-6.232%, -0.464%] |
| 0.06 |   3784 | 51.3% | -2.139% | [-2.329%, -1.950%] | -2.317% | [-5.440%, +0.806%] |
| 0.07 |   3157 | 51.5% | -2.076% | [-2.283%, -1.869%] | -2.347% | [-5.748%, +1.054%] |
| 0.08 |   2646 | 51.9% | -2.027% | [-2.254%, -1.800%] | -1.888% | [-5.594%, +1.818%] |
| 0.09 |   2166 | 51.5% | -1.795% | [-2.046%, -1.544%] | -2.589% | [-6.684%, +1.506%] |
| 0.10 |   1785 | 51.5% | -1.642% | [-1.925%, -1.359%] | -2.474% | [-6.987%, +2.040%] |

---

## Controlo (a) — apostar sempre Over (universo completo, a `Max>2.5`)

| N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|
| 19,684 | 50.5% | -2.514% | [-2.597%, -2.432%] | -1.843% | [-3.244%, -0.441%] |

---

## Controlos (b)/(c) — aleatório uniforme vs. estratificado por banda de `Max>2.5`

| Threshold | N | CLV modelo | (b) uniforme IC95% | p (b) | (c) estratificado IC95% | p (c) |
|---|---|---|---|---|---|---|
| 0.00 | 8635 | -2.435% | -2.515% [-2.518%, -2.512%] | p=0.049 (separa (p≤0.05)) | -2.478% [-2.481%, -2.475%] | p=0.185 (não separa) |
| 0.01 | 7658 | -2.413% | -2.513% [-2.516%, -2.510%] | p=0.032 (separa (p≤0.05)) | -2.475% [-2.478%, -2.472%] | p=0.109 (não separa) |
| 0.02 | 6767 | -2.395% | -2.515% [-2.519%, -2.512%] | p=0.024 (separa (p≤0.05)) | -2.468% [-2.471%, -2.464%] | p=0.090 (não separa) |
| 0.03 | 5935 | -2.326% | -2.511% [-2.515%, -2.507%] | p=0.002 (separa (p≤0.05)) | -2.472% [-2.476%, -2.468%] | p=0.011 (separa (p≤0.05)) |
| 0.04 | 5134 | -2.285% | -2.514% [-2.518%, -2.509%] | p=0.000 (separa (p≤0.05)) | -2.476% [-2.481%, -2.472%] | p=0.004 (separa (p≤0.05)) |
| 0.05 | 4445 | -2.250% | -2.514% [-2.519%, -2.509%] | p=0.000 (separa (p≤0.05)) | -2.474% [-2.478%, -2.469%] | p=0.004 (separa (p≤0.05)) |
| 0.06 | 3784 | -2.139% | -2.513% [-2.518%, -2.507%] | p=0.000 (separa (p≤0.05)) | -2.465% [-2.470%, -2.460%] | p=0.000 (separa (p≤0.05)) |
| 0.07 | 3157 | -2.076% | -2.516% [-2.522%, -2.510%] | p=0.000 (separa (p≤0.05)) | -2.456% [-2.462%, -2.450%] | p=0.000 (separa (p≤0.05)) |
| 0.08 | 2646 | -2.027% | -2.512% [-2.519%, -2.506%] | p=0.000 (separa (p≤0.05)) | -2.457% [-2.463%, -2.450%] | p=0.000 (separa (p≤0.05)) |
| 0.09 | 2166 | -1.795% | -2.521% [-2.528%, -2.513%] | p=0.000 (separa (p≤0.05)) | -2.460% [-2.467%, -2.452%] | p=0.000 (separa (p≤0.05)) |
| 0.10 | 1785 | -1.642% | -2.511% [-2.519%, -2.503%] | p=0.000 (separa (p≤0.05)) | -2.451% [-2.458%, -2.443%] | p=0.000 (separa (p≤0.05)) |

---

## Estratificação por época — universo "sempre Over"

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| 2023-24 | 4,568 | 52.5% | -0.17% | -2.612% |
| 2022-23 | 4,549 | 48.7% | -3.01% | -2.560% |
| 2024-25 | 4,525 | 50.8% | -2.01% | -2.631% |
| 2021-22 | 4,133 | 49.5% | -2.30% | -1.363% |
| 2025-26 | 1,909 | 51.5% | -1.70% | -4.388% |

## Estratificação por divisão — universo "sempre Over"

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| Championship | 2,333 | 46.9% | -3.05% | -2.266% |
| La Liga 2 | 1,906 | 41.9% | -0.95% | -2.803% |
| Premier League | 1,631 | 56.8% | +0.26% | -2.536% |
| Serie A | 1,620 | 48.0% | -5.25% | -2.757% |
| La Liga | 1,618 | 47.2% | -2.24% | -2.683% |
| Serie B | 1,442 | 43.8% | -3.04% | -2.723% |
| Ligue 1 | 1,438 | 53.3% | -1.09% | -2.115% |
| Ligue 2 | 1,411 | 45.1% | -0.03% | -2.001% |
| Primeira Liga | 1,289 | 50.7% | -0.02% | -2.112% |
| Eredivisie | 1,285 | 58.7% | -1.99% | -2.562% |
| Belgian Pro League | 1,262 | 53.9% | -6.41% | -2.722% |
| Bundesliga | 1,243 | 60.2% | +0.65% | -2.957% |
| Bundesliga 2 | 1,206 | 58.8% | +0.17% | -2.489% |

## Estratificação por época — modelo @ threshold=0.03

CLV médio positivo em **0 de 5** épocas
a este threshold (critério pré-registado exige ≥4/5).

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| 2021-22 | 1,483 | 51.4% | -2.43% | -1.027% |
| 2022-23 | 1,391 | 51.6% | -0.87% | -2.416% |
| 2024-25 | 1,235 | 50.5% | -3.86% | -2.291% |
| 2023-24 | 1,170 | 53.1% | +0.70% | -2.771% |
| 2025-26 | 656 | 50.6% | -4.31% | -4.346% |

## Estratificação por divisão — modelo @ threshold=0.03

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| La Liga 2 | 625 | 42.4% | -1.91% | -2.522% |
| Championship | 602 | 49.2% | -0.76% | -2.217% |
| Serie A | 562 | 50.2% | -3.69% | -2.746% |
| Serie B | 523 | 43.0% | -6.22% | -2.081% |
| Bundesliga | 494 | 58.7% | -4.50% | -3.402% |
| Premier League | 470 | 58.9% | +6.04% | -2.598% |
| Bundesliga 2 | 463 | 57.0% | -4.02% | -2.532% |
| La Liga | 410 | 51.5% | +4.43% | -2.319% |
| Ligue 1 | 388 | 52.3% | -2.65% | -2.311% |
| Ligue 2 | 363 | 45.2% | -1.77% | -0.952% |
| Belgian Pro League | 360 | 57.5% | -2.58% | -1.699% |
| Eredivisie | 354 | 60.2% | -4.71% | -1.869% |
| Primeira Liga | 321 | 50.2% | -2.80% | -2.254% |

---

## Conclusão

Cobertura de `Max>2.5` no universo do estudo original: **100.0%** (19,684 de 19,684 jogos) — os dois estudos são directamente comparáveis, mesmo conjunto de jogos, só a odd tomada muda.

**Nota de qualidade de dados:** em 1,057 jogos (5.4%), `Max>2.5 < P>2.5` — provavelmente artefacto de timing de captura no football-data.co.uk (colunas `Max`/`Avg` e `P` não são necessariamente amostradas no mesmo instante). Não filtrados — usar a odd tal como está, sem escolher a dedo os casos favoráveis.

**Critério pré-registado, avaliado ao threshold de referência 0.03** (o mesmo usado em toda a estratificação deste relatório — não procurado no sweep):

| Perna do critério | Valor observado | Passa? |
|---|---|---|
| CLV médio > +1,0% | -2.326% | ❌ |
| n ≥ 1000 | 5,935 | ✅ |
| ≥4 das 5 épocas com CLV médio positivo | 0/5 | ❌ |


**Para contexto (não é o critério de decisão):** o modelo separa-se do controlo estratificado por banda de odds (p≤0.05) em 8 de 11 thresholds do sweep — mesma leitura qualitativa do estudo original (sinal relativo real face à linha de fecho), independentemente de o critério de decisão passar ou não.

**Resultado desta tentativa (Max>2.5): o critério pré-registado NÃO é cumprido** ao threshold 0.03 — falha em: CLV>+1,0%, ≥4/5 épocas. Por regra pré-registada em `.claude/rules/decisions.md`, não há terceira tentativa: a linha de investigação "DC vs. fecho" fica registada como resultado nulo nesta forma, mesmo trocando a odd de abertura pela melhor odd de mercado.

---

## Notas metodológicas

- Modelo Dixon-Coles **não calibrado** (`p_dc` bruto), mesma escolha do estudo
  original — isola "o DC bruto tem informação vs. o fecho" sem misturar calibração.
- Stake flat 1 unidade em todos os cálculos de ROI, à odd `Max>2.5`.
- `Max>2.5 < P>2.5` não é filtrado — ver nota de qualidade de dados na conclusão.
- `_load()` mantém apenas as 13 divisões conhecidas, mesmo filtro do estudo original.
