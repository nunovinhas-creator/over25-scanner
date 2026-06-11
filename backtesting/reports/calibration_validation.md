# Calibration Validation Report — Época 2526

> **Split temporal estrito**
> Treino/Calibração: épocas 2122, 2223, 2324, 2425
> Validação (intocada): época **2526**

Generated: 2026-06-11T23:06:49+00:00 UTC

## Calibrador seleccionado

| Métrica | Valor |
|---------|-------|
| Método seleccionado | **Isotonic** |
| CV Brier (LOEO 4-fold) | 0.24565 |
| Platt avg Brier | 0.24620 |
| Isotónico avg Brier | 0.24565 ← **seleccionado** |
| Parâmetros | 45 pares de threshold |
| N amostras de treino | 17,628 |
| Épocas de treino | 2122, 2223, 2324, 2425 |

Brier por fold — Platt: `[0.25031, 0.24496, 0.24419, 0.24535]`
Brier por fold — Isotónico: `[0.24951, 0.24464, 0.24333, 0.2451]`

## Baseline de mercado (época 2526, todos os jogos com odds Pinnacle)

| Métrica | Valor |
|---------|-------|
| N jogos | 1,889 |
| Brier (p_market) | 0.24320 |
| Log-loss (p_market) | 0.67938 |

> Brier benchmark Pinnacle (over/under 2.5): ≈ 0.220–0.230

## Resultados por peso — calibrado vs não-calibrado (época 2526)

`p_final = w × p_cal + (1 − w) × p_market`

| w | N cal | ROI cal | CLV cal | Brier cal || N unc | ROI unc | Brier unc |
|---|-------|---------|---------|----------||-------|---------|----------|
| **0.10** | 0 | — | — | — || 0 | — | — |
| **0.15** | 3 | -7.67% | +3.054% | 0.25837 || 11 | -57.45% | 0.23702 |
| **0.20** | 23 | -17.78% | +0.400% | 0.24522 || 29 | -33.62% | 0.24393 |
| **0.30** | 83 | -14.39% | +0.190% | 0.24168 || 102 | -18.83% | 0.25110 | ◄

> **Cal** = `p_dc` calibrado pelo Isotonic antes do blend
> **Unc** = `p_dc` directo do Dixon-Coles (sem calibração)
> ◄ = peso seleccionado (melhor Brier calibrado)

## Peso seleccionado: w = 0.3

| | Calibrado | Não-calibrado |
|---|---|---|
| N apostas | 83 | 102 |
| Win% | 34.9% | 40.2% |
| P&L | -11.9u | -19.2u |
| ROI | -14.39% | -18.83% |
| Brier | 0.24168 | 0.25110 |

### CLV com IC 95% (calibrado, w = 0.3)

| Métrica | Valor |
|---------|-------|
| N apostas com CLV | 83 |
| CLV médio | +0.190% |
| SE | ±0.600% |
| IC 95% | [-0.985%, +1.366%] |

 ⚠️ IC inclui zero — CLV não significativamente positivo

## Tabela de calibração — w = 0.3

### Calibrado (buckets devem alinhar melhor)

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|----------|
| (0.325, 0.349] |   10 | 0.337 | 0.300 | -0.037 |
| (0.349, 0.373] |    7 | 0.358 | 0.286 | -0.073 |
| (0.373, 0.396] |    2 | 0.389 | 0.500 | +0.111 |
| (0.396, 0.419] |   15 | 0.405 | 0.467 | +0.061 |
| (0.419, 0.443] |    8 | 0.433 | 0.250 | -0.183 |
| (0.443, 0.466] |    3 | 0.455 | 0.333 | -0.121 |
| (0.466, 0.489] |   17 | 0.477 | 0.412 | -0.065 |
| (0.489, 0.513] |   12 | 0.505 | 0.250 | -0.255 |
| (0.513, 0.536] |    6 | 0.524 | 0.333 | -0.191 |
| (0.536, 0.559] |    3 | 0.555 | 0.333 | -0.222 |

### Não-calibrado (para comparação)

| Bucket previsto | N | Pred médio | Win% real | Diferença |
|-----------------|---|-----------|-----------|----------|
| (0.388, 0.424] |    5 | 0.401 | 0.600 | +0.199 |
| (0.424, 0.459] |   14 | 0.442 | 0.286 | -0.156 |
| (0.459, 0.494] |   15 | 0.480 | 0.467 | -0.014 |
| (0.494, 0.529] |   24 | 0.512 | 0.250 | -0.262 |
| (0.529, 0.564] |   20 | 0.544 | 0.350 | -0.194 |
| (0.564, 0.599] |   13 | 0.586 | 0.538 | -0.047 |
| (0.599, 0.634] |    7 | 0.611 | 0.571 | -0.040 |
| (0.634, 0.669] |    2 | 0.643 | 1.000 | +0.357 |
| (0.669, 0.704] |    1 | 0.683 | 1.000 | +0.317 |
| (0.704, 0.739] |    1 | 0.739 | 0.000 | -0.739 |

## Resultados por banda de odds — w = 0.3

### Calibrado

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|----|
| 2.00–2.50    |    42 | 33.3% | +0.0553 |  -26.50% |
| >2.50        |    35 | 37.1% | +0.0500 |   +3.69% |
| outros (n<30) |     6 | 33.3% | — |  -35.00% |

### Não-calibrado

| Odds | N apostas | Win% | EV médio | ROI |
|------|-----------|------|----------|----|
| 1.70–2.00    |    41 | 48.8% | +0.0502 |  -10.24% |
| 2.00–2.50    |    49 | 30.6% | +0.0681 |  -32.06% |
| outros (n<30) |    12 | 50.0% | — |   +5.83% |

## Decisão: Cap de odds

**CAP RECOMENDADO ≤ 2.00** — A assimetria persiste na banda 2.00–2.50 após calibração (ROI=-26.5%, N=42). Não-calibrado: ROI=-32.1%. A calibração não resolveu o overconfidence nesta banda.

**Implementação sugerida**: adicionar `MAX_ODDS_OVER = 2.00` em `config.py`.

**Justificação**: Dixon-Coles sobrestima probabilidades em jogos de baixo expected score (odds altas Over). A calibração suaviza mas não elimina o viés.

## Notas metodológicas

- Split temporal estrito: calibrador ajustado apenas em épocas 2122, 2223, 2324, 2425
- Época 2526 nunca tocada durante ajuste do calibrador (gold rule)
- LOEO-CV: 4 folds, leave-one-epoch-out
- Calibrador Isotonic: `p_model = calibrate(p_dc)` → `p_final = w·p_model + (1-w)·p_market`
- Critério de aposta: `ev_final = p_final × P>2.5 − 1 ≥ 0.03`
- Serialização em `data/calibrator.json` — sem pickle, parâmetros legíveis
