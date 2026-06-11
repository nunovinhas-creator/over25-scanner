# Walk-Forward Backtest Report

> **DADOS REAIS — football-data.co.uk — 13,643 jogos**
> 5 épocas × 13 divisões × temporadas 2021-22 a 2025-26

> ⚠️ **10,122 linhas excluídas** (Div=`?`): CSVs de football-data.co.uk com BOM UTF-8 lido incorrectamente em latin-1 — correcto após próximo `--download-all` com a versão fixada do pipeline.

Generated: 2026-06-11T21:29:47+00:00 UTC

## Dataset

| | |
|---|---|
| Jogos usados (Div conhecida) | **13,643** |
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
| **0.15** |    177 | 46.9% |   -12.8u |  -7.26% | 0.23887 | 0.67067 | +0.63% |
| **0.30** |   1115 | 49.8% |   -35.7u |  -3.21% | 0.24529 | 0.68379 | -0.08% |
| **0.50** |   2317 | 50.8% |   -58.3u |  -2.52% | 0.24685 | 0.68745 | -0.41% |
| **1.00** |   3618 | 51.1% |  -119.1u |  -3.29% | 0.25524 | 0.70903 | -0.54% |

> Brier Score benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230
> CLV = `P>2.5 / PC>2.5 − 1`; positivo = apostámos a odds melhores que o fecho.
> ROI = (P&L / N apostas) × 100

## Peso recomendado: w = 0.15 (melhor Brier = 0.23887)

Win%=46.9%  |  ROI=-7.26%  |  N=177

## Tabela de calibração (w = 0.15)

Buckets de probabilidade prevista vs taxa real de vitória.

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|-----------|
| (0.371, 0.406] |    10 | 0.391 | 0.100 | -0.291 |
| (0.406, 0.439] |    15 | 0.425 | 0.467 | +0.042 |
| (0.439, 0.473] |    27 | 0.457 | 0.333 | -0.124 |
| (0.473, 0.506] |    23 | 0.491 | 0.348 | -0.144 |
| (0.506, 0.54] |    38 | 0.522 | 0.579 | +0.057 |
| (0.54, 0.573] |    22 | 0.553 | 0.409 | -0.144 |
| (0.573, 0.606] |    13 | 0.595 | 0.615 | +0.021 |
| (0.606, 0.64] |    20 | 0.622 | 0.650 | +0.028 |
| (0.64, 0.673] |     6 | 0.654 | 0.833 | +0.180 |
| (0.673, 0.706] |     3 | 0.694 | 0.333 | -0.361 |

## Resultados por liga (w = 0.15)

| Liga | N apostas | Win% | ROI | Avg CLV |
|------|-----------|------|-----|---------|
| La Liga 2                 |     34 | 38.2% |  -10.15% | +2.16% |
| Championship              |     27 | 48.1% |   -8.59% | +1.64% |
| La Liga                   |     22 | 50.0% |   +3.95% | -0.51% |
| Serie B                   |     20 | 40.0% |  -17.65% | +2.13% |
| Ligue 1                   |     18 | 55.6% |   +6.94% | +0.59% |
| Primeira Liga             |     13 | 38.5% |  -26.38% | -0.44% |
| Serie A                   |     11 | 72.7% |  +28.27% | -6.40% |
| Premier League            |      9 | 44.4% |  -16.00% | +0.62% |
| Bundesliga                |      6 | 33.3% |  -35.67% | -3.82% |
| Eredivisie                |      6 | 66.7% |  +11.67% | +3.77% |
| Bundesliga 2              |      5 | 80.0% |  +37.60% | -3.30% |
| Ligue 2                   |      5 | 0.0% | -100.00% | +7.20% |
| Belgian Pro League        |      1 | 100.0% |  +65.00% | +3.77% |

## Resultados por banda de odds (w = 0.15)

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|-----|
| <1.50        |      2 | 50.0% | +0.0342 |  -27.00% |
| 1.50–1.70    |     19 | 68.4% | +0.0406 |  +11.58% |
| 1.70–2.00    |     59 | 61.0% | +0.0453 |  +13.63% |
| 2.00–2.50    |     81 | 33.3% | +0.0522 |  -27.02% |
| >2.50        |     16 | 37.5% | +0.0512 |   -4.13% |

## Notas metodológicas

- Modelo Dixon-Coles re-treinado semanalmente (cada segunda-feira) por divisão
- Decay ξ = 0.0018 (semi-vida ≈ 2 anos)
- Probabilidade de mercado: devig multiplicativo sobre Pinnacle opening (`P>2.5` / `P<2.5`)
- Fallback quando `P<2.5` ausente: `(1 / P>2.5) / 1.04`
- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ 0.03`
- Stake: flat 1 unidade (Kelly desabilitado — `Config.STAKE_TYPE = "flat"`)
