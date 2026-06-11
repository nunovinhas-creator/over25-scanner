# Walk-Forward Backtest Report

Generated: 2026-06-11T17:40:42+00:00 UTC

## Dataset

| | |
|---|---|
| Total games evaluated | 9,112 |
| Date range | 2021-09-02 → 2026-05-26 |
| Leagues | 13 (Belgian Pro League, Bundesliga, Bundesliga 2, Championship, Eredivisie, La Liga, La Liga 2, Ligue 1, Ligue 2, Premier League, Primeira Liga, Serie A, Serie B) |
| EV threshold (MIN\_EV) | 0.03 (3%) |

## No-leakage verification

Walk-forward uses **strictly historical data** at every step:
- Training set for week W: all games with `date < week_start(W)`
- Test set for week W: games in `[week_start(W), week_start(W+1))`
- Cold-start filter: teams with < 5 prior games are skipped
- Verified programmatically: 0 leakage violations detected ✓

## Results by blend weight

`p_final = w × p_dc + (1 − w) × p_market`

| w    | N bets | Win%  |     P&L | Brier    | Log-loss | Avg CLV |
|------|--------|-------|---------|----------|----------|---------|
| 0.15 |   2873 | 28.3% |  -105.4u | 0.19926 | 0.58539 | -0.69% |
| 0.30 |   5622 | 32.7% |  -305.4u | 0.21235 | 0.61406 | -0.62% |
| 0.50 |   7447 | 34.7% |  -486.8u | 0.22119 | 0.63313 | -0.63% |
| 1.00 |   9112 | 37.2% |  -461.7u | 0.24459 | 0.68444 | -0.62% |

> **Brier Score reference**: Pinnacle benchmark ≈ 0.220–0.230 (over/under 2.5 market).
> CLV = `P>2.5 / PC>2.5 − 1`; positive means we bet at better odds than the closing line.

## Recommended blend weight: **w = 0.15** (Brier Score = 0.19926, Win% = 28.3%, P&L = -105.4u)

## Notes

- Dixon-Coles model re-fitted weekly (every Monday) per division
- Time-decay parameter ξ = 0.0018 (≈2-year half-life)
- Market probability: de-vigged Pinnacle opening (`P>2.5` / `P<2.5`) via multiplicative method
- Fallback when `P<2.5` missing: `(1 / P>2.5) / 1.04`
- Bet criterion: `ev_final = p_final × P>2.5 − 1 ≥ 0.03`
- Staking: flat 1 unit per bet (Kelly disabled per `Config.STAKE_TYPE`)
