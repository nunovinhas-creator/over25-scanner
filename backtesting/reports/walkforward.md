# Walk-Forward Backtest Report

> **DADOS REAIS — football-data.co.uk — 23,765 jogos**
> 5 épocas × 13 divisões × temporadas 2021-22 a 2025-26
Generated: 2026-06-11T22:09:27+00:00 UTC

## Dataset

| | |
|---|---|
| Jogos usados (Div conhecida) | **23,765** |
| Date range | 2021-07-23 → 2026-05-31 |
| Ligas | 13 (Belgian Pro League, Bundesliga, Bundesliga 2, Championship, Eredivisie, La Liga, La Liga 2, Ligue 1, Ligue 2, Premier League, Primeira Liga, Serie A, Serie B) |
| EV threshold (MIN\_EV) | 0.03 (3%) |

## No-leakage verification

- Training: todos os jogos com `date < week_start(W)` (sem lookahead)
- Test: jogos em `[week_start(W), week_start(W+1))`
- Cold-start: equipas com < 5 jogos anteriores ignoradas
- **0 violações de leakage detectadas ✓**

## Resultados por peso de blend

`p_final = w × p_dc + (1 − w) × p_market`

| w | N apostas | Win% | P&L | ROI | Brier | Log-loss | Avg CLV |
|---|-----------|------|-----|-----|-------|----------|---------|
| **0.00** |      0 | — |    +0.0u |   +nan% | 0.24064 | 0.67412 | N/A |
| **0.10** |     33 | 51.5% |    +4.0u | +12.24% | 0.26177 | 0.71691 | +2.22% |
| **0.15** |    216 | 44.0% |   -27.6u | -12.77% | 0.23722 | 0.66724 | +0.88% |
| **0.20** |    540 | 46.3% |   -42.2u |  -7.82% | 0.24437 | 0.68175 | +0.42% |
| **0.30** |   1454 | 48.7% |   -67.4u |  -4.64% | 0.24367 | 0.68046 | -0.12% |
| **0.50** |   3261 | 49.9% |  -111.3u |  -3.41% | 0.24687 | 0.68733 | -0.45% |
| **1.00** |   5434 | 50.0% |  -243.9u |  -4.49% | 0.25591 | 0.70899 | -0.57% |

> `w=0.00` — baseline do mercado: Brier/Log-loss calculados sobre todos os jogos com odds Pinnacle disponíveis (não filtrados por EV); N apostas=0 porque EV≈0 a odds de abertura.
> Brier Score benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230
> CLV = `P>2.5 / PC>2.5 − 1`; positivo = apostámos a odds melhores que o fecho.
> ROI = (P&L / N apostas) × 100

## Peso recomendado: w = 0.15 (melhor Brier = 0.23722)

Win%=44.0%  |  ROI=-12.77%  |  N=216

## CLV — Intervalo de Confiança 95% (w = 0.15)

| Métrica | Valor |
|---------|-------|
| N apostas com CLV | 216 |
| CLV médio | +0.881% |
| Erro padrão (SE) | ±0.439% |
| IC 95% | [+0.021%, +1.741%] |

 ✓ IC não inclui zero

## Simulação de timing realista (-30min KO)

CLV calculado a preço médio `(P>2.5 + PC>2.5) / 2` — proxy para entrada ~30min antes do KO.

| Métrica | Valor |
|---------|-------|
| CLV mid-price médio | +0.441% |
| Erro padrão (SE) | ±0.219% |
| IC 95% | [+0.011%, +0.870%] |

 ✓ IC não inclui zero

> Interpretação: `CLV opening` = valor assumindo entrada na abertura. `CLV mid-price` = valor assumindo entrada 30min antes. Se mid-price > opening, odds melhoram à medida que se aproxima o KO.

## Tabela de calibração (w = 0.15)

Buckets de probabilidade prevista vs taxa real de vitória.

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|----------|
| (0.371, 0.406] |    11 | 0.389 | 0.182 | -0.208 |
| (0.406, 0.439] |    17 | 0.425 | 0.412 | -0.013 |
| (0.439, 0.473] |    38 | 0.456 | 0.263 | -0.193 |
| (0.473, 0.506] |    32 | 0.490 | 0.344 | -0.146 |
| (0.506, 0.54] |    46 | 0.520 | 0.500 | -0.020 |
| (0.54, 0.573] |    26 | 0.553 | 0.500 | -0.053 |
| (0.573, 0.606] |    16 | 0.596 | 0.562 | -0.033 |
| (0.606, 0.64] |    20 | 0.622 | 0.600 | -0.022 |
| (0.64, 0.673] |     8 | 0.652 | 0.875 | +0.223 |
| (0.673, 0.706] |     2 | 0.693 | 0.500 | -0.193 |

## Resultados por liga (w = 0.15)

> Apenas segmentos com n ≥ 100 apostas têm linha própria; o resto é agregado.

| Liga | N apostas | Win% | ROI | Avg CLV |
|------|-----------|------|-----|--------|
| outros (216 apostas, n < 100/liga) |    216 | 44.0% |  -12.77% | +0.88% |

## Resultados por banda de odds (w = 0.15)

> Apenas bandas com n ≥ 100 apostas têm linha própria; o resto é agregado.

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|----|
| 2.00–2.50    |    110 | 30.0% | +0.0494 |  -34.58% |
| outros (n < 100) |    106 | 58.5% | +0.0451 |   +9.87% |

## Notas metodológicas

- Modelo Dixon-Coles re-treinado semanalmente (cada segunda-feira) por divisão
- Decay ξ = 0.0018 (semi-vida ≈ 2 anos)
- Probabilidade de mercado: devig multiplicativo sobre Pinnacle opening (`P>2.5` / `P<2.5`)
- Fallback quando `P<2.5` ausente: `(1 / P>2.5) / 1.04`
- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ 0.03`
- Stake: flat 1 unidade (Kelly desabilitado — `Config.STAKE_TYPE = "flat"`)
