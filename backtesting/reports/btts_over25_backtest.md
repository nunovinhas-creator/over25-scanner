# BTTS+Over 2.5 — Backtest Fast

> **Dados reais — football-data.co.uk — 18,577 jogos com previsão DC**
> Threshold overlay (backtest): ≥ 10% (p\_dc\_conjunta − p\_naive)
> Período: 2021-07-23 → 2026-05-31
> ⚠️ **Modo rápido (in-sample)**: probabilidades calculadas com dc_ratings.json actual.
> Modelo treinado em dados sobrepostos — calibração indicativa, não EV real.
> Para análise walk-forward sem leakage, correr sem flag `--fast`.

Sem odds BTTS+Over 2.5 disponíveis no dataset — ROI não calculado.

## Definições

| Termo | Descrição |
|---|---|
| `p_dc_conjunta` | P(BTTS AND Over 2.5) extraída da grelha bivariada DC |
| `p_btts_dc` | P(BTTS) = P(home≥1 AND away≥1) da grelha |
| `p_over25_dc` | P(Over 2.5) = P(total≥3) da grelha |
| `p_naive` | `p_btts_dc × p_over25_dc` (assumindo independência) |
| `overlay` | `p_dc_conjunta − p_naive` (excesso de probabilidade conjunta) |
| Resultado real | BTTS real AND Over 2.5 real (FTHG≥1, FTAG≥1, total≥3) |

## Resultados globais

| | Todos os jogos DC | Overlay ≥ 10% |
|---|---|---|
| N jogos | 18,577 | 17,309 |
| WR real (BTTS+O2.5) | 41.4% | 41.2% |
| Overlay médio | +12.92% | +13.27% |

## Calibração — p_dc_conjunta vs frequência real

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
| (0.1, 0.2]             |    264 | 0.169 | 0.250 | +0.081 |
| (0.2, 0.3]             |   2513 | 0.265 | 0.331 | +0.066 |
| (0.3, 0.4]             |   7019 | 0.354 | 0.377 | +0.023 |
| (0.4, 0.5]             |   6023 | 0.447 | 0.449 | +0.002 |
| (0.5, 0.6]             |   2366 | 0.537 | 0.516 | -0.021 |
| (0.6, 0.7]             |    387 | 0.631 | 0.568 | -0.062 |
| (0.7, 0.8]             |      5 | 0.724 | 1.000 | +0.276 |

## Calibração — p_naive vs frequência real (referência)

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
| (-0.001, 0.1]          |    415 | 0.079 | 0.255 | +0.177 |
| (0.1, 0.2]             |   4391 | 0.162 | 0.343 | +0.180 |
| (0.2, 0.3]             |   7183 | 0.248 | 0.398 | +0.150 |
| (0.3, 0.4]             |   4665 | 0.342 | 0.473 | +0.131 |
| (0.4, 0.5]             |   1569 | 0.437 | 0.511 | +0.073 |
| (0.5, 0.6]             |    325 | 0.537 | 0.606 | +0.069 |
| (0.6, 0.7]             |     29 | 0.619 | 0.621 | +0.002 |

## Por liga — todos os jogos DC

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
| Championship              |   2148 | 39.0% | 36.3% |
| Premier League            |   1752 | 44.0% | 41.9% |
| La Liga 2                 |   1712 | 37.3% | 35.6% |
| La Liga                   |   1644 | 38.5% | 38.8% |
| Serie A                   |   1610 | 38.0% | 34.0% |
| Ligue 1                   |   1427 | 43.9% | 42.3% |
| Bundesliga                |   1334 | 49.0% | 49.7% |
| Eredivisie                |   1334 | 45.4% | 47.0% |
| Belgian Pro League        |   1287 | 42.3% | 40.3% |
| Primeira Liga             |   1134 | 39.0% | 35.5% |
| Bundesliga 2              |   1086 | 49.2% | 46.9% |
| Serie B                   |   1074 | 38.7% | 35.4% |
| Ligue 2                   |   1035 | 36.5% | 38.0% |

## Por liga — overlay ≥ 10%

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
| Championship              |   2033 | 39.6% | 37.2% |
| Premier League            |   1663 | 44.0% | 42.2% |
| La Liga 2                 |   1656 | 37.6% | 35.9% |
| La Liga                   |   1544 | 38.4% | 38.5% |
| Serie A                   |   1543 | 38.3% | 33.9% |
| Ligue 1                   |   1299 | 43.3% | 42.4% |
| Belgian Pro League        |   1214 | 42.2% | 40.1% |
| Bundesliga                |   1158 | 47.8% | 48.6% |
| Eredivisie                |   1121 | 44.2% | 45.5% |
| Serie B                   |   1041 | 38.3% | 35.4% |
| Bundesliga 2              |   1040 | 49.3% | 46.8% |
| Ligue 2                   |   1025 | 36.6% | 37.9% |
| Primeira Liga             |    972 | 39.1% | 35.4% |

## Interpretação

- **overlay > 0** é o padrão esperado: P(BTTS AND O2.5) é sempre maior que o produto
  das probabilidades marginais porque os eventos são positivamente correlacionados.
- Jogos com overlay elevado têm lambdas altos em ambas as equipas — são os jogos
  onde o modelo DC vê maior expectativa de golo partilhado.
- Sem odds de mercado específicas para BTTS+Over 2.5, não é possível calcular CLV
  nem ROI real. A validação é feita apenas por calibração e frequência relativa.
- **Gate live scan**: overlay ≥ 8% AND ev\_final\_over25 ≥ 3% AND liga whitelisted.
- **Activação alertas TG**: n ≥ 100 settled com CLV proxy > +5% no período.

Generated: 2026-06-21T09:42:38+00:00 UTC
