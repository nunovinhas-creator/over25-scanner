# Regras — Backend Python

## Pipeline Python: estrutura e responsabilidades

```
pipeline/extract.py      — chamadas HTTP à BSD API (fail-safe: lista vazia em erro)
pipeline/etl.py          — orquestração extract → transform → load
pipeline/transform.py    — compute_final_probability_dc (blend 30/70)
pipeline/scan_over25.py  — Over 2.5 scan + BTTS+O2.5 scan (30 min cron)
pipeline/scan_sharp1x2.py — Sharp 1X2 scan (30 min cron)
pipeline/settle_sharp1x2.py — settlement Sharp 1X2 (resultado_outcome via BSD, 2.5h–48h pós-KO;
                              corre antes de update_closing_odds.py, mesmo job/cron)
pipeline/update_closing_odds.py — fetch odds_fecho Pinnacle pós-KO + CLV (15min–24h; job
                              settle_and_close_odds em sharp1x2_analysis.yml, cron 30min)
pipeline/scan_common.py  — whitelist, BSD_LEAGUE_ID_MAP, UNKNOWN_LEAGUE, TG, git, I/O partilhados
pipeline/config.py       — MODEL_WEIGHT=0.30 (não alterar sem nova validação LOEO-CV)
pipeline/historical.py   — download football-data.co.uk → matches.csv
pipeline/scan_live.py    — loop ao minuto (live_scanner.yml): detecção/scoring/padrões live +
                            👁 OBSERVAÇÕES autónomas (gate → data/observations.json → TG) e health
                            check (data/live_scanner_health.json); "🔥 APOSTAR AGORA" desactivado
                            (LIVE_ALERTS_ENABLED=False — não reactivar sem decisão explícita)
```

## Modelos

```
models/math/devig.py       — metodo_multiplicativo, metodo_shin, devig()
models/math/poisson.py     — fit_dixon_coles_fast, prob_over25_poisson,
                              build_dc_grid, extract_btts_over25_prob
models/math/calibration.py — Platt scaling, isotonic regression, temperature scaling
models/math/skellam.py     — Skellam distribution para 1X2 (não em produção)
models/math/kelly.py       — Kelly criterion (DESACTIVADO — ver decisions.md)
models/metrics/brier_score.py — Brier + decomposição Murphy 1973
models/metrics/ece.py         — Expected Calibration Error
models/metrics/roi_metrics.py — ROI, profit factor, drawdown, CLV rolling
models/train_dc.py            — CLI: treino DC por liga → data/dc_ratings.json
```

## Fórmula de probabilidade final

```
p_model  = Dixon-Coles (fit semanal por liga via train_dc.py)
p_calib  = isotonic_calibrator(p_model)   ← LOEO-CV
p_market = devig(odds Pinnacle over/under)
p_final  = MODEL_WEIGHT × p_calib + (1 − MODEL_WEIGHT) × p_market
         = 0.30 × p_calib + 0.70 × p_market
ev_final = p_final × odds_over − 1
```

Nota (auditoria jul 2026): `ev_final` é o EV real às odds brutas (inclui a margem
do bookmaker) — é o que `pipeline/transform.py` calcula e o que o gate `MIN_EV=0.03`
usa. A fórmula antiga documentada (`p_final / p_market − 1`) media divergência
modelo-vs-mercado sem vig e nunca correspondeu ao código.

`MODEL_WEIGHT=0.30` é fixo — melhor Brier calibrado em LOEO-CV. Alteração exige nova validação completa.

## Backtesting

```
backtesting/engine.py              — Backtester + BacktestConfig: walk-forward determinístico
backtesting/strategies.py          — catálogo de estratégias e filter presets
backtesting/report.py              — geração de relatórios (texto e markdown)
backtesting/run_backtest.py        — CLI: todas as estratégias Over 2.5
backtesting/run_walkforward.py     — OOS predictions com calibrator_fn + season filter
backtesting/run_calibration.py     — FASE 4: LOEO-CV → calibrator.json + validation report
backtesting/run_sharp1x2_signal.py — Q1–Q6 analysis Sharp 1X2
backtesting/run_btts_over25_backtest.py — walk-forward BTTS+O2.5
backtesting/send_sharp1x2_weekly.py    — relatório TG semanal Sharp 1X2
```

### Estratégias disponíveis

| Estratégia | Critério |
|---|---|
| `baseline` | todos os picks com EV ≥ 3% |
| `shortening_only` | + movimento SHORTENING |
| `sharp_only` | + sinal Sharp confirmado |
| `shortsharp` | SHORTENING e Sharp combinados |
| `high_xg` | + xG total > threshold |
| `high_score` | + score composto ≥ 65/100 |
| `value_only` | + odds_band seleccionada |
| `conservative` | critérios mais restritivos |
| `kelly_sizing` | Kelly sizing (DESACTIVADO em produção) |

## Comandos

```bash
python -m models.train_dc                    # treina DC por liga
python -m backtesting.run_calibration        # LOEO-CV → calibrator.json
python -m backtesting.run_walkforward        # walk-forward OOS
python -m backtesting.run_backtest           # todas as estratégias
python -m pipeline.historical --synthetic    # dados históricos (--synthetic só para testes)
pytest tests/ -v --tb=short                  # testes unitários
```

## Infraestrutura automática (GitHub Actions)

| Workflow | Trigger | Acção |
|---|---|---|
| `scanner.yml` | a cada 30 min | Over 2.5 + Sharp 1X2 scan + commit picks |
| `live_scanner.yml` | 6×/dia, loop ao minuto (~5h50/arranque) | Scanner LIVE (`pipeline/scan_live.py`) — 👁 OBSERVAÇÕES autónomas (TG + `data/observations.json`) + health check, independente da whitelist e do browser; "🔥 APOSTAR AGORA" desactivado |
| `historical_data.yml` | seg 06:00 UTC | actualiza matches.csv |
| `retrain_dc.yml` | seg 07:00 UTC | re-treina DC + calibrador + relatório TG |
| `deploy_version.yml` | push main | version.json + BUILD_SHA [skip ci] |
| `data_quality.yml` | diário 07:00 UTC | schema validation + backtests |
| `sharp1x2_analysis.yml` | workflow_dispatch | Q1–Q6 analysis report |

**Secrets necessários:** `BSD_API_KEY`, `TG_TOKEN`, `TG_CHAT_ID`

## Regra de dados sintéticos

**NUNCA substituir dados reais por sintéticos para fazer um pipeline 'passar'.** Dados sintéticos são permitidos **apenas** em testes unitários (`tests/`), sempre claramente marcados com `# synthetic` ou `@pytest.mark.synthetic`.
