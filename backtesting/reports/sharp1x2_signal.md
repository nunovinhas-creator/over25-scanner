# Sharp 1X2 — Análise do Sinal pin_drop

Gerado em 2026-06-12 07:46 UTC
Fonte: `matches.csv` · 21,087 jogos · épocas ['2122', '2223', '2324', '2425', '2526'] · divisões ['B1', 'D1', 'D2', 'E0', 'E1', 'F1', 'F2', 'I1', 'I2', 'N1', 'P1', 'SP1', 'SP2']

> **Aviso de limitação**: os dados football-data.co.uk têm apenas odds de abertura e fecho,
> sem timestamps intraday. O `pin_drop` é um proxy da pressão total sobre o mercado,
> não da proximidade temporal ao KO. Resultados interpretados com esta limitação.

---

## Q1 — Outcome com maior pin_drop tem ROI positivo?

Estratégia: por cada jogo, apostar no outcome cujas odds mais encurtaram (pin_drop máximo).
Odds usadas: Pinnacle closing (PSCH/PSCD/PSCA).

### Por divisão

| Div | n | roi_pct | wr_pct | avg_drop_pct | avg_close_odds |
| --- | --- | --- | --- | --- | --- |
| D1 | 1373.0 | 1.25 | 39.0 | 6.69 | 3.419 |
| F2 | 1538.0 | -0.23 | 39.9 | 7.56 | 2.79 |
| E0 | 1730.0 | -2.84 | 37.7 | 6.58 | 3.64 |
| N1 | 1366.0 | -2.9 | 41.5 | 7.16 | 3.504 |
| B1 | 1328.0 | -2.91 | 40.5 | 7.29 | 2.984 |
| P1 | 1376.0 | -3.42 | 41.9 | 7.76 | 3.613 |
| E1 | 2468.0 | -3.96 | 38.0 | 5.71 | 2.901 |
| F1 | 1525.0 | -3.96 | 37.8 | 6.91 | 3.259 |
| I2 | 1606.0 | -5.07 | 36.2 | 7.84 | 2.898 |
| SP2 | 2041.0 | -5.42 | 36.6 | 7.75 | 2.931 |
| D2 | 1312.0 | -5.99 | 36.6 | 6.53 | 2.881 |
| I1 | 1717.0 | -8.71 | 36.4 | 6.48 | 3.28 |
| SP1 | 1707.0 | -9.25 | 37.1 | 6.47 | 3.195 |


### Global

| n | roi_pct | wr_pct | avg_drop_pct | avg_close_odds |
| --- | --- | --- | --- | --- |
| 21087.0 | -4.25 | 38.3 | 6.93 | 3.16 |


---

## Q2 — Magnitude do drop como proxy de timing (ROI por quartil)

Jogos ordenados por max(pin_drop_h, pin_drop_d, pin_drop_a).
Q4 = jogos com maior drop total — maior concentração de sharp money.

| quartile | n | drop_range_pct | roi_pct | wr_pct |
| --- | --- | --- | --- | --- |
| Q1 (menor drop) | 5275 | -2.9%–2.7% | -1.65 | 42.6 |
| Q2 | 5269 | 2.7%–5.2% | -4.94 | 38.7 |
| Q3 | 5284 | 5.2%–9.1% | -5.33 | 37.4 |
| Q4 (maior drop) | 5259 | 9.1%–113.1% | -5.1 | 34.3 |


> **Limitação**: drop maior não implica necessariamente aposta mais tardia.
> Sem dados intraday, esta tabela é a melhor aproximação disponível.

---

## Q3 — Sinal de empate (DRAW quando pin_drop_d > 5%)

Filtro: pin_drop_d > 5% (odds de empate encurtaram ≥5%)

| Métrica | Valor |
| --- | --- |
| Jogos no sinal | 3,665 |
| WR observado (DRAW) | 26.1% |
| Breakeven a closing odds | 25.9% |
| Edge bruto | +0.21% |
| Avg. closing odds DRAW | 3.857 |
| ROI flat (closing odds) | -5.69% |

> **Estado**: resultado reportado apenas — não activar automaticamente. Validar com backtesting temporal antes de qualquer uso em produção.

---

_Análise automática — ver `backtesting/run_sharp1x2_signal.py`_