# Calibration Validation Report — Época 2526

> **Split temporal estrito**
> Treino/Calibração: épocas 2122, 2223, 2324, 2425
> Validação (intocada): época **2526**

Generated: 2026-06-22T12:56:41+00:00 UTC

## Calibrador seleccionado

| Métrica | Valor |
|---------|-------|
| Método seleccionado | **Isotonic** |
| CV Brier (LOEO 4-fold) | 0.24558 |
| Platt avg Brier | 0.24622 |
| Isotónico avg Brier | 0.24558 ← **seleccionado** |
| Parâmetros | 49 pares de threshold |
| N amostras de treino | 17,775 |
| Épocas de treino | 2122, 2223, 2324, 2425 |

Brier por fold — Platt: `[0.25037, 0.24498, 0.24429, 0.24524]`
Brier por fold — Isotónico: `[0.24913, 0.24457, 0.24352, 0.24511]`

## Baseline de mercado (época 2526, todos os jogos com odds Pinnacle)

| Métrica | Valor |
|---------|-------|
| N jogos | 1,912 |
| Brier (p_market) | 0.24421 |
| Log-loss (p_market) | 0.68144 |

> Brier benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230

## Resultados por peso — calibrado vs não-calibrado (época 2526)

`p_final = w × p_cal + (1 − w) × p_market`

| w | N cal | ROI cal | CLV cal | Brier cal || N unc | ROI unc | Brier unc |
|---|-------|---------|---------|----------||-------|---------|----------|
| **0.10** | 0 | — | — | — || 2 | +18.00% | 0.28249 |
| **0.15** | 5 | +2.60% | +1.486% | 0.25872 || 11 | -36.00% | 0.25760 |
| **0.20** | 14 | -43.43% | +1.006% | 0.22690 || 31 | -22.77% | 0.25473 | ◄
| **0.30** | 85 | +4.64% | -0.509% | 0.25497 || 108 | -7.20% | 0.25495 |

> **Cal** = `p_dc` calibrado pelo Isotonic antes do blend
> **Unc** = `p_dc` directo do Dixon-Coles (sem calibração)
> ◄ = peso seleccionado (melhor Brier calibrado)

## Peso seleccionado: w = 0.2

| | Calibrado | Não-calibrado |
|---|---|---|
| N apostas | 14 | 31 |
| Win% | 21.4% | 35.5% |
| P&L | -6.1u | -7.1u |
| ROI | -43.43% | -22.77% |
| Brier | 0.22690 | 0.25473 |

### CLV com IC 95% (calibrado, w = 0.2)

| Métrica | Valor |
|---------|-------|
| N apostas com CLV | 14 |
| CLV médio | +1.006% |
| SE | ±1.008% |
| IC 95% | [-0.968%, +2.981%] |

 ⚠️ IC inclui zero — CLV não significativamente positivo

## Tabela de calibração — w = 0.2

### Calibrado (buckets devem alinhar melhor)

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|----------|
| (0.31, 0.331] |    1 | 0.311 | 0.000 | -0.311 |
| (0.37, 0.389] |    3 | 0.379 | 0.667 | +0.288 |
| (0.409, 0.428] |    2 | 0.416 | 0.000 | -0.416 |
| (0.428, 0.448] |    1 | 0.444 | 0.000 | -0.444 |
| (0.448, 0.467] |    4 | 0.455 | 0.250 | -0.205 |
| (0.467, 0.487] |    2 | 0.469 | 0.000 | -0.469 |
| (0.487, 0.506] |    1 | 0.506 | 0.000 | -0.506 |

### Não-calibrado (para comparação)

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|----------|
| (0.387, 0.41] |    1 | 0.388 | 1.000 | +0.612 |
| (0.41, 0.432] |    3 | 0.423 | 0.333 | -0.090 |
| (0.432, 0.454] |    2 | 0.444 | 0.000 | -0.444 |
| (0.454, 0.476] |    7 | 0.465 | 0.429 | -0.037 |
| (0.476, 0.498] |    6 | 0.481 | 0.333 | -0.148 |
| (0.52, 0.542] |    5 | 0.531 | 0.400 | -0.131 |
| (0.542, 0.564] |    5 | 0.558 | 0.200 | -0.358 |
| (0.586, 0.608] |    2 | 0.604 | 0.500 | -0.104 |

## Resultados por banda de odds — w = 0.2

### Calibrado

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|----|
| outros (n<30) |    14 | 21.4% | — |  -43.43% |

### Não-calibrado

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|----|
| outros (n<30) |    31 | 35.5% | — |  -22.77% |

## Decisão: Cap de odds

**INCONCLUSIVO** — Banda 2.00–2.50 sem apostas suficientes (< 10) após calibração na época de validação. Sem cap aplicado.

Reavaliar com dados de mais uma época.

## Notas metodológicas

- Split temporal estrito: calibrador ajustado apenas em épocas 2122, 2223, 2324, 2425
- Época 2526 nunca tocada durante ajuste do calibrador (gold rule)
- LOEO-CV: 4 folds, leave-one-epoch-out
- Calibrador Isotonic: `p_model = calibrate(p_dc)` → `p_final = w·p_model + (1-w)·p_market`
- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ 0.03`
- Serialização em `data/calibrator.json` — sem pickle, parâmetros legíveis
