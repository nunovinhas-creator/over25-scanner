# Sharp 1X2 — Análise de Sinais (pin_drop + divergência)

Gerado em 2026-06-12 08:28 UTC
Fonte: `matches.csv` · 21,087 jogos · épocas ['2122', '2223', '2324', '2425', '2526'] · divisões ['B1', 'D1', 'D2', 'E0', 'E1', 'F1', 'F2', 'I1', 'I2', 'N1', 'P1', 'SP1', 'SP2']

> **Limitação dos dados**: football-data.co.uk tem apenas odds de abertura e fecho,
> sem timestamps intraday. `pin_drop` = pressão acumulada, não timing. Interpretado com cautela.

---

## Q1 — pin_drop direto: apostar no outcome com maior queda de odds

Estratégia: por cada jogo, apostar no outcome com maior `pin_drop` (PSx/PSCx − 1).
Odds de liquidação: Pinnacle closing (PSCH/PSCD/PSCA).

### Por divisão

| Div | n | roi_pct | wr_pct | avg_close_odds | avg_drop_pct |
| --- | --- | --- | --- | --- | --- |
| D1 | 1373.0 | 1.25 | 39.0 | 3.419 | 6.69 |
| F2 | 1538.0 | -0.23 | 39.9 | 2.79 | 7.56 |
| E0 | 1730.0 | -2.84 | 37.7 | 3.64 | 6.58 |
| N1 | 1366.0 | -2.9 | 41.5 | 3.504 | 7.16 |
| B1 | 1328.0 | -2.91 | 40.5 | 2.984 | 7.29 |
| P1 | 1376.0 | -3.42 | 41.9 | 3.613 | 7.76 |
| E1 | 2468.0 | -3.96 | 38.0 | 2.901 | 5.71 |
| F1 | 1525.0 | -3.96 | 37.8 | 3.259 | 6.91 |
| I2 | 1606.0 | -5.07 | 36.2 | 2.898 | 7.84 |
| SP2 | 2041.0 | -5.42 | 36.6 | 2.931 | 7.75 |
| D2 | 1312.0 | -5.99 | 36.6 | 2.881 | 6.53 |
| I1 | 1717.0 | -8.71 | 36.4 | 3.28 | 6.48 |
| SP1 | 1707.0 | -9.25 | 37.1 | 3.195 | 6.47 |


### Global

| n | roi_pct | wr_pct | avg_close_odds | avg_drop_pct |
| --- | --- | --- | --- | --- |
| 21087.0 | -4.25 | 38.3 | 3.16 | 6.93 |


---

## Q2 — pin_drop inverso: apostar no outcome com MENOR queda (maior drift)

Estratégia: por cada jogo, apostar no outcome cujas odds mais SUBIRAM ou menos caíram.
Racional: Q2 do Q1 sugere correlação inversa — drift pode indicar mercado a corrigir excesso.

### Por quartil de min_drop

| quartile | n | min_drop_range | roi_pct | wr_pct |
| --- | --- | --- | --- | --- |
| Q1 (maior drift+) | 5272 | -72.3%–-11.3% | -9.68 | 22.2 |
| Q2 | 5276 | -11.3%–-6.7% | -6.26 | 30.6 |
| Q3 | 5268 | -6.7%–-3.8% | -3.02 | 36.1 |
| Q4 (menor drift) | 5271 | -3.8%–0.9% | -3.57 | 39.5 |


**Global**: ROI -5.63% (n=21,087, WR 32.1%)

---

## Q3 — Sinal de empate (pin_drop_d > 5%)

Filtro: pin_drop_d > 5% (odds de empate encurtaram ≥ 5%)

| Métrica | Valor |
| --- | --- |
| Jogos no sinal | 3,665 |
| WR observado (DRAW) | 26.1% |
| Breakeven (closing odds) | 25.9% |
| Edge bruto | +0.21% |
| Avg closing odds DRAW | 3.857 |
| ROI flat | -5.69% |

> Resultado reportado apenas — não activar sem backtesting temporal.

---

## Q4 — Divergência B365 / Pinnacle: apostar onde Bet365 é mais generosa

Fórmula: `div_x = B365x / PSx − 1` (positivo = Bet365 acima da Pinnacle abertura).
Estratégia: para cada threshold, apostar no outcome com maior divergência se > threshold.
Odds de liquidação: Pinnacle closing (evita viés de usar as odds do sinal).

### Global por threshold

| threshold | n_bets | pct_jogos | wr_pct | avg_close_odds | roi_pct |
| --- | --- | --- | --- | --- | --- |
| >2% | 5463 | 25.9% | 27.4 | 4.578 | 0.29 |
| >3% | 3731 | 17.7% | 26.0 | 4.945 | 2.46 |
| >5% | 1774 | 8.4% | 22.4 | 5.855 | 0.59 |
| >8% | 607 | 2.9% | 19.9 | 7.522 | -0.09 |
| >10% | 347 | 1.6% | 15.9 | 8.84 | -5.05 |


### Por liga (n ≥ 300 por célula)

| liga | threshold | n | wr_pct | roi_pct |
| --- | --- | --- | --- | --- |
| N1 | >2% | 571 | 26.8 | 13.6 |
| E0 | >2% | 312 | 22.8 | 7.87 |
| I2 | >2% | 501 | 33.3 | 5.93 |
| SP2 | >2% | 903 | 33.7 | 5.15 |
| F2 | >2% | 416 | 30.5 | -0.98 |
| P1 | >2% | 315 | 26.3 | -1.28 |
| B1 | >2% | 418 | 28.7 | -3.15 |
| E1 | >2% | 559 | 25.2 | -4.69 |
| SP1 | >2% | 308 | 21.8 | -11.81 |
| D2 | >2% | 316 | 24.4 | -16.04 |
| N1 | >3% | 438 | 24.4 | 16.68 |
| SP2 | >3% | 645 | 33.3 | 7.23 |
| I2 | >3% | 354 | 31.9 | 1.2 |
| E1 | >3% | 366 | 24.0 | -1.55 |


---

## Conclusão — Qual o sinal mais promissor?

Análise sobre 21,087 jogos.

| Sinal | ROI global | n | Veredicto |
| --- | --- | --- | --- |
| pin_drop direto (max drop) | -4.25% | 21,087 | negativo (-4.25%) |
| pin_drop inverso (min drop) | -5.63% | 21,087 | negativo (-5.63%) |
| divergência B365/Pin (>3%) | +2.46% | 3,731 | **promissor** (+2.46%) |

> **Sinal mais promissor**: divergência (ROI +2.46%, n=3,731)
> Validar com backtesting temporal estrito antes de qualquer uso em produção.

---

_Análise automática — ver `backtesting/run_sharp1x2_signal.py`_