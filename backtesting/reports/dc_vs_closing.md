# Dixon-Coles vs. Linha de Fecho Pinnacle — Investigação Offline

> **Contexto:** scanner ao vivo congelado desde 25 ago 2026 (tag `v-freeze-2026-08`,
> ver [`.claude/rules/decisions.md`](../../.claude/rules/decisions.md)). Este relatório
> corre inteiramente sobre `data/historical/matches.csv`, sem chamadas à BSD API.

**Pergunta:** o modelo Dixon-Coles, treinado apenas com jogos passados (walk-forward
estritamente expansivo), identifica valor na odd Over 2.5 de **abertura** Pinnacle
quando avaliado contra a probabilidade implícita do **fecho**?

Gerado: 2026-08-31T00:15:00+00:00 UTC
Dataset: `data/historical/matches.csv`, 2021-08-27 → 2026-01-14

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
     seed fixa (`42`).
   - **(c) aleatório estratificado por banda de `P>2.5`** (<1.70, 1.70-1.90, 1.90-2.10, >2.10) —
     replica a distribuição de bandas da selecção do modelo em cada threshold, 1000
     simulações. Se o modelo não separar deste controlo, o sinal vem da estrutura
     da linha (o spread abertura-fecho não é uniforme por banda de odds), não do
     modelo.
6. **Estratificação** por época (5) e por divisão (13), n reportado em todas as
   células; célula com n<100 marcada como ruído. Reportada para o universo
   "sempre Over" (spread abertura-fecho estrutural, independente do modelo) e para o
   modelo no threshold de referência **0.03 (3pp)** — este threshold foi
   fixado antes de correr o sweep, escolhido por legibilidade; não corresponde ao
   `MIN_EV=3%` de produção (unidades diferentes — EV vs. diferença de probabilidade —
   o alinhamento é aparente, não real).

---

## Cadeia de reconciliação de N

| Passo | N | Δ vs. anterior | Motivo |
|---|---|---|---|
| Jogos ligas conhecidas (13 divisões, `_load()`) | 23,765 | — | — |
| ... com par abertura (P>2.5/P<2.5) **e** fecho (PC>2.5/PC<2.5) válidos, bruto | 21,012 | −2,753 | odds ausentes/suspensas nalgum dos 4 campos |
| ... elegíveis pelo splitter walk-forward (`run_walkforward`, prior de equipa ≥5 jogos, treino da liga ≥50 jogos, mercado computável) | 19,687 | ver nota¹ | cold-start (equipas/liga sem histórico suficiente no início da série); estes gates não são desligados — são intrínsecos ao ajuste do modelo, ao contrário do gate de EV (`min_ev=-999`, esse sim desligado) |
| ... com par de abertura estrito (P>2.5 **e** P<2.5, sem o fallback de margem que o splitter aceita) | 19,687 | −0 | splitter aceita fallback `(1/P>2.5)/1.04` quando só P>2.5 existe; este estudo exige o par completo |
| **Universo final do estudo** (+ par de fecho válido) | **19,684** | −3 | PC>2.5/PC<2.5 ausente apesar do par de abertura presente |

¹ Perda do splitter face ao par bruto válido: +1,325 — negativo se o splitter aceitou jogos via fallback de mercado (só P>2.5) que não estavam no par bruto "ambos válidos"; positivo se o cold-start/min-treino removeu jogos que tinham odds completas mas não histórico suficiente. Ver sinal real na tabela.

---

## Resultados por threshold (`edge = p_dc − p_open ≥ threshold`)

| Threshold | N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|---|
| 0.00 |   8635 | 51.5% | -3.817% | [-3.932%, -3.702%] | -3.328% | [-5.371%, -1.285%] |
| 0.01 |   7658 | 51.5% | -3.786% | [-3.908%, -3.664%] | -3.355% | [-5.522%, -1.188%] |
| 0.02 |   6767 | 51.8% | -3.773% | [-3.903%, -3.642%] | -2.892% | [-5.193%, -0.590%] |
| 0.03 |   5935 | 51.5% | -3.699% | [-3.839%, -3.559%] | -3.347% | [-5.807%, -0.888%] |
| 0.04 |   5134 | 50.7% | -3.675% | [-3.827%, -3.524%] | -4.623% | [-7.272%, -1.973%] |
| 0.05 |   4445 | 50.7% | -3.653% | [-3.817%, -3.489%] | -4.750% | [-7.593%, -1.908%] |
| 0.06 |   3784 | 51.3% | -3.563% | [-3.742%, -3.384%] | -3.741% | [-6.818%, -0.663%] |
| 0.07 |   3157 | 51.5% | -3.504% | [-3.699%, -3.308%] | -3.766% | [-7.118%, -0.414%] |
| 0.08 |   2646 | 51.9% | -3.464% | [-3.678%, -3.251%] | -3.316% | [-6.968%, +0.336%] |
| 0.09 |   2166 | 51.5% | -3.296% | [-3.532%, -3.060%] | -4.062% | [-8.094%, -0.030%] |
| 0.10 |   1785 | 51.5% | -3.156% | [-3.420%, -2.891%] | -3.988% | [-8.430%, +0.455%] |

Threshold com CLV médio mais alto no sweep: **0.10** (n=1785, CLV=-3.156% [-3.420%, -2.891%]). Não confundir com o threshold de referência (3pp) usado na estratificação — esse foi pré-registado antes de observar este sweep.

---

## Controlo (a) — apostar sempre Over (universo completo)

| N | Win% | CLV médio | IC95% CLV | ROI médio | IC95% ROI |
|---|---|---|---|---|---|
| 19,684 | 50.5% | -3.985% | [-4.063%, -3.907%] | -3.325% | [-4.706%, -1.945%] |

---

## Controlos (b)/(c) — aleatório uniforme vs. estratificado por banda de odds

Por threshold: CLV do modelo vs. CLV médio de 1000 simulações aleatórias com o
mesmo N (b: amostragem uniforme; c: amostragem estratificada pela mesma
distribuição de bandas de `P>2.5` da selecção do modelo). `p` = fracção das
1000 simulações com CLV ≥ CLV do modelo — `p` baixo (≤0.05) indica que o
modelo separa do controlo; `p` alto indica que não separa.

| Threshold | N | CLV modelo | (b) uniforme IC95% | p (b) | (c) estratificado IC95% | p (c) |
|---|---|---|---|---|---|---|
| 0.00 | 8635 | -3.817% | -3.986% [-3.989%, -3.983%] | p=0.000 (separa (p≤0.05)) | -3.936% [-3.938%, -3.933%] | p=0.005 (separa (p≤0.05)) |
| 0.01 | 7658 | -3.786% | -3.985% [-3.988%, -3.982%] | p=0.000 (separa (p≤0.05)) | -3.935% [-3.938%, -3.932%] | p=0.000 (separa (p≤0.05)) |
| 0.02 | 6767 | -3.773% | -3.984% [-3.987%, -3.980%] | p=0.001 (separa (p≤0.05)) | -3.929% [-3.932%, -3.926%] | p=0.000 (separa (p≤0.05)) |
| 0.03 | 5935 | -3.699% | -3.987% [-3.991%, -3.984%] | p=0.000 (separa (p≤0.05)) | -3.926% [-3.930%, -3.923%] | p=0.000 (separa (p≤0.05)) |
| 0.04 | 5134 | -3.675% | -3.983% [-3.988%, -3.979%] | p=0.000 (separa (p≤0.05)) | -3.933% [-3.937%, -3.929%] | p=0.000 (separa (p≤0.05)) |
| 0.05 | 4445 | -3.653% | -3.986% [-3.991%, -3.982%] | p=0.000 (separa (p≤0.05)) | -3.929% [-3.933%, -3.924%] | p=0.000 (separa (p≤0.05)) |
| 0.06 | 3784 | -3.563% | -3.983% [-3.988%, -3.978%] | p=0.000 (separa (p≤0.05)) | -3.924% [-3.929%, -3.919%] | p=0.000 (separa (p≤0.05)) |
| 0.07 | 3157 | -3.504% | -3.990% [-3.995%, -3.984%] | p=0.000 (separa (p≤0.05)) | -3.913% [-3.919%, -3.908%] | p=0.000 (separa (p≤0.05)) |
| 0.08 | 2646 | -3.464% | -3.984% [-3.990%, -3.978%] | p=0.000 (separa (p≤0.05)) | -3.913% [-3.919%, -3.906%] | p=0.000 (separa (p≤0.05)) |
| 0.09 | 2166 | -3.296% | -3.978% [-3.985%, -3.971%] | p=0.000 (separa (p≤0.05)) | -3.909% [-3.916%, -3.902%] | p=0.000 (separa (p≤0.05)) |
| 0.10 | 1785 | -3.156% | -3.981% [-3.989%, -3.974%] | p=0.000 (separa (p≤0.05)) | -3.912% [-3.920%, -3.904%] | p=0.000 (separa (p≤0.05)) |

**Leitura decisiva:** se o modelo separar de (b) mas não de (c), o sinal vem da
estrutura do spread abertura-fecho por banda de odds (o modelo simplesmente
selecciona mais jogos numa banda com CLV estrutural mais alto), não de
capacidade preditiva do Dixon-Coles.

---

## Estratificação por época — universo "sempre Over"

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| 2023-24 | 4,568 | 52.5% | -1.64% | -4.018% |
| 2022-23 | 4,549 | 48.7% | -4.65% | -4.236% |
| 2024-25 | 4,525 | 50.8% | -3.61% | -4.229% |
| 2021-22 | 4,133 | 49.5% | -4.26% | -3.303% |
| 2025-26 | 1,909 | 51.5% | -1.50% | -4.209% |

## Estratificação por divisão — universo "sempre Over"

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| Championship | 2,333 | 46.9% | -4.64% | -3.801% |
| La Liga 2 | 1,906 | 41.9% | -2.64% | -4.461% |
| Premier League | 1,631 | 56.8% | -1.13% | -3.927% |
| Serie A | 1,620 | 48.0% | -6.60% | -4.139% |
| La Liga | 1,618 | 47.2% | -3.78% | -4.227% |
| Serie B | 1,442 | 43.8% | -4.19% | -3.855% |
| Ligue 1 | 1,438 | 53.3% | -2.67% | -3.681% |
| Ligue 2 | 1,411 | 45.1% | -1.92% | -3.912% |
| Primeira Liga | 1,289 | 50.7% | -1.61% | -3.653% |
| Eredivisie | 1,285 | 58.7% | -3.41% | -3.962% |
| Belgian Pro League | 1,262 | 53.9% | -8.13% | -4.491% |
| Bundesliga | 1,243 | 60.2% | -0.54% | -4.132% |
| Bundesliga 2 | 1,206 | 58.8% | -0.81% | -3.441% |

## Estratificação por época — modelo @ threshold=0.03

| Época | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| 2021-22 | 1,483 | 51.4% | -4.37% | -3.016% |
| 2022-23 | 1,391 | 51.6% | -2.44% | -3.970% |
| 2024-25 | 1,235 | 50.5% | -5.29% | -3.743% |
| 2023-24 | 1,170 | 53.1% | -0.74% | -4.069% |
| 2025-26 | 656 | 50.6% | -3.94% | -3.929% |

## Estratificação por divisão — modelo @ threshold=0.03

| Divisão | N | Win% | ROI | CLV médio |
|---|---|---|---|---|
| La Liga 2 | 625 | 42.4% | -3.36% | -3.907% |
| Championship | 602 | 49.2% | -2.33% | -3.689% |
| Serie A | 562 | 50.2% | -5.07% | -4.127% |
| Serie B | 523 | 43.0% | -7.36% | -3.130% |
| Bundesliga | 494 | 58.7% | -5.57% | -4.522% |
| Premier League | 470 | 58.9% | +4.78% | -3.867% |
| Bundesliga 2 | 463 | 57.0% | -4.82% | -3.357% |
| La Liga | 410 | 51.5% | +2.85% | -3.875% |
| Ligue 1 | 388 | 52.3% | -4.10% | -3.778% |
| Ligue 2 | 363 | 45.2% | -3.78% | -2.927% |
| Belgian Pro League | 360 | 57.5% | -4.44% | -3.421% |
| Eredivisie | 354 | 60.2% | -6.07% | -3.294% |
| Primeira Liga | 321 | 50.2% | -4.31% | -3.785% |

---

## Conclusão

**A CLV absoluta é negativa em todos os buckets, incluindo os controlos aleatórios — isto não é, por si só, evidência de que o mercado se move contra o Over.** `CLV = p_close × P>2.5 − 1` avalia uma odd bruta (com a margem do bookmaker embutida) contra uma probabilidade de-vigada; sem qualquer movimento de linha entre abertura e fecho (`p_close ≈ p_open`), esta fórmula converge sozinha para `−overround/(1+overround)`, tipicamente 2–5% para over/under Pinnacle. O controlo (a) confirma isto: apostar sempre Over, sem qualquer selecção, já dá -3.985% — este é o nível de referência da margem do livro, não um sinal de skill negativo do modelo.

**O que se destaca é a CLV *relativa*.** Em todos os 11 thresholds testados com jogos suficientes, a selecção do modelo teve CLV menos negativa do que ambos os controlos aleatórios de igual N — uniforme: p≤0.05 em todos os thresholds; estratificado por banda de odds: p≤0.05 em todos os thresholds. A vantagem sobre o controlo (c) — o que decide o estudo, porque controla para a banda de odds seleccionada — vai de +0.119pp no threshold mais baixo testado a +0.756pp no mais alto, crescendo de forma aproximadamente monótona com o threshold. Isto é evidência de sinal real no Dixon-Coles bruto face à linha de fecho — não é apenas um artefacto de seleccionar odds de uma banda estruturalmente mais favorável.

**Mas o sinal não chega a superar a margem embutida.** A CLV absoluta nunca fica positiva em nenhum threshold do sweep (0–10pp) — o melhor resultado é -3.156%, ainda bem abaixo de zero. Não há, nesta regra de selecção às odds de abertura, um threshold que produza EV positivo contra o fecho.

**Nota sobre a estratificação por divisão (threshold=0.03):** 2 de 13 divisões com n≥100 mostram ROI pontual positivo — Premier League (ROI +4.78%, n=470, CLV médio -3.867%); La Liga (ROI +2.85%, n=410, CLV médio -3.875%). A CLV média mantém-se negativa em ambas; sem IC no ROI a este nível de estratificação, isto lê-se como variância de amostra (win-rate numa amostra pequena), não como edge específico da liga — 2 em 13 é consistente com ruído, não com um padrão sistemático.

**Resposta à pergunta de investigação:** o Dixon-Coles bruto (sem calibração) contém informação mensurável sobre a linha de fecho — separa-se do controlo estratificado por banda de odds (p≤0.05) em todos os 11 thresholds testados, com a vantagem a crescer nos thresholds mais altos. Mas, avaliado contra as odds de abertura tal como estão cotadas (com a margem do bookmaker embutida), esse sinal não é suficiente para produzir CLV positiva em nenhum threshold testado. **Não há, com estes dados e esta regra de selecção, evidência de edge apostável nas odds de abertura.**

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
