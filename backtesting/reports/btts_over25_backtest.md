# BTTS+Over 2.5 — Backtest Walk-Forward

> **Dados reais — football-data.co.uk — 22,429 jogos com previsão DC**
> Threshold overlay (backtest): ≥ 10% (p\_dc\_conjunta − p\_naive)
> Período: 2021-08-27 → 2026-05-31

**Metodologia:** Walk-forward semanal sem lookahead. Modelo DC ajustado semanalmente
com todos os jogos anteriores à semana de teste. Equipas com < 5 jogos prévios excluídas.
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
| N jogos | 22,429 | 20,377 |
| WR real (BTTS+O2.5) | 40.8% | 40.5% |
| Overlay médio | +12.73% | +13.21% |

## Calibração — p_dc_conjunta vs frequência real

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
| (-0.001, 0.1]          |    105 | 0.068 | 0.429 | +0.361 |
| (0.1, 0.2]             |    726 | 0.166 | 0.342 | +0.175 |
| (0.2, 0.3]             |   3985 | 0.261 | 0.355 | +0.094 |
| (0.3, 0.4]             |   8012 | 0.351 | 0.384 | +0.033 |
| (0.4, 0.5]             |   6532 | 0.445 | 0.439 | -0.006 |
| (0.5, 0.6]             |   2554 | 0.538 | 0.482 | -0.056 |
| (0.6, 0.7]             |    451 | 0.635 | 0.497 | -0.139 |
| (0.7, 0.8]             |     53 | 0.739 | 0.585 | -0.154 |
| (0.8, 0.9]             |     11 | 0.827 | 0.545 | -0.282 |

## Calibração — p_naive vs frequência real (referência)

| Bucket | N | p_pred_avg | p_real (%) | Diff |
|---|---|---|---|---|
| (-0.001, 0.1]          |   1145 | 0.072 | 0.348 | +0.277 |
| (0.1, 0.2]             |   6285 | 0.157 | 0.364 | +0.206 |
| (0.2, 0.3]             |   7867 | 0.248 | 0.397 | +0.149 |
| (0.3, 0.4]             |   4939 | 0.344 | 0.460 | +0.116 |
| (0.4, 0.5]             |   1730 | 0.439 | 0.475 | +0.035 |
| (0.5, 0.6]             |    374 | 0.540 | 0.527 | -0.013 |
| (0.6, 0.7]             |     65 | 0.636 | 0.492 | -0.143 |
| (0.7, 0.8]             |     21 | 0.744 | 0.619 | -0.125 |
| (0.8, 0.9]             |      3 | 0.822 | 0.667 | -0.156 |

## Por liga — todos os jogos DC

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
| Championship              |   2626 | 38.1% | 36.4% |
| La Liga 2                 |   2173 | 36.6% | 32.4% |
| La Liga                   |   1814 | 38.9% | 34.6% |
| Premier League            |   1813 | 44.8% | 41.7% |
| Serie A                   |   1804 | 37.8% | 37.7% |
| Serie B                   |   1735 | 37.1% | 35.2% |
| Ligue 2                   |   1621 | 37.5% | 32.8% |
| Ligue 1                   |   1598 | 43.4% | 40.5% |
| Belgian Pro League        |   1481 | 42.0% | 42.4% |
| Bundesliga                |   1450 | 48.2% | 47.0% |
| Eredivisie                |   1449 | 45.4% | 42.7% |
| Primeira Liga             |   1442 | 38.4% | 34.3% |
| Bundesliga 2              |   1423 | 47.3% | 46.4% |

## Por liga — overlay ≥ 10%

| Liga | N | WR real | p_pred_avg |
|---|---|---|---|
| Championship              |   2502 | 38.3% | 36.7% |
| La Liga 2                 |   2038 | 36.7% | 33.2% |
| Serie A                   |   1685 | 37.7% | 37.5% |
| Serie B                   |   1664 | 36.7% | 35.3% |
| La Liga                   |   1639 | 38.7% | 34.9% |
| Premier League            |   1620 | 44.7% | 42.3% |
| Ligue 2                   |   1491 | 38.2% | 34.0% |
| Ligue 1                   |   1471 | 42.8% | 40.3% |
| Belgian Pro League        |   1378 | 41.5% | 42.1% |
| Bundesliga 2              |   1329 | 47.0% | 46.1% |
| Primeira Liga             |   1213 | 37.9% | 34.8% |
| Eredivisie                |   1177 | 44.8% | 42.3% |
| Bundesliga                |   1170 | 48.0% | 45.8% |

## Interpretação

- **overlay > 0** é o padrão esperado: P(BTTS AND O2.5) é sempre maior que o produto
  das probabilidades marginais porque os eventos são positivamente correlacionados.
- Jogos com overlay elevado têm lambdas altos em ambas as equipas — são os jogos
  onde o modelo DC vê maior expectativa de golo partilhado.
- Sem odds de mercado específicas para BTTS+Over 2.5, não é possível calcular CLV
  nem ROI real. A validação é feita apenas por calibração e frequência relativa.
- **Gate live scan**: overlay ≥ 8% AND ev\_final\_over25 ≥ 3% AND liga whitelisted.
- **Activação alertas TG**: n ≥ 100 settled com CLV proxy > +5% no período.

Generated: 2026-06-21T09:53:02+00:00 UTC
