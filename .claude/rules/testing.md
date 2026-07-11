# Regras — Testes

## Estrutura

```
tests/
  test_scanner.py            — smoke tests dos dois scanners (mocks BSD/TG): gates,
                               deduplicação, alertas, escrita picks/rejected
  test_btts_over25.py        — grelha bivariada DC + extract_btts_over25_prob
  test_save_whitelist.py     — whitelist fail-closed
  pipeline/
    test_devig.py            — de-vig (metodo_multiplicativo, metodo_shin)
    test_calibration.py      — calibrador isotónico + compute_final_probability_dc
    test_etl_filters.py      — filter_by_league, filter_alert_candidates
    test_1x2_filters.py      — filter_1x2_alert_candidates
    test_sharp1x2_gates.py   — apply_sharp1x2_gates (porta Python dos gates JS)
    test_historical_1x2.py   — histórico 1X2 football-data.co.uk
    test_lineups.py          — summarize_lineups + _event_fields
  data_quality/
    test_bsd_schema.py       — schema de eventos BSD
    test_picks_quality.py    — qualidade dos picks (consistência temporal, EV vs WR)
```

## Comandos

```bash
pytest tests/ -v --tb=short          # todos os testes
pytest tests/pipeline/test_devig.py -v   # ficheiro específico
pytest -k "btts" -v                  # filtro por nome
pytest --tb=long                     # traceback completo em falhas
```

## Regras anti-leakage (obrigatórias)

1. **Walk-forward estritamente temporal** — o modelo nunca vê dados futuros. O calibrador é treinado na época N e validado na época N+1 (LOEO-CV = Leave-One-Era-Out).

2. **Proibido validar no conjunto de treino.** Qualquer resultado reportado em `backtesting/reports/` deve ser out-of-sample. `--fast` em `run_btts_over25_backtest.py` usa `dc_ratings.json` in-sample e deve ser claramente marcado como tal.

3. **Dados sintéticos apenas em `tests/`.** Nunca usar dados sintéticos no pipeline de produção ou para "fazer passar" um workflow. Marcar sempre com `# synthetic` ou `@pytest.mark.synthetic`.

4. **Gate overlay não discrimina** — o gate de CLV ≥ 5% (BTTS+O2.5) foi seleccionado por não criar leakage no walk-forward. Não adicionar gates retroactivos que usem informação pós-KO para seleccionar picks passados.

5. **`previous_decimal_odds` não é closing line.** É a odd do scan anterior. CLV exacto requer fetch Pinnacle pós-KO (+10min). Usar `previous_decimal_odds` como proxy de CLV só é válido internamente como estimativa.

## xfail activos

| Teste | Motivo |
|---|---|
| CLV exacto Sharp 1X2 | `odds_fecho` real requer fetch Pinnacle pós-KO; ainda não implementado |

Não remover `xfail` sem implementar o fetch real de `odds_fecho`.

## Validação FASE 4 (Over 2.5, época 2526)

| Métrica | Valor |
|---|---|
| Brier calibrado | 0.24168 |
| Brier mercado | 0.24320 |
| CLV IC 95% | [−0.985%, +1.366%] |
| N picks | 83 |

Estes números são a baseline de referência. Se uma alteração ao modelo piorar o Brier calibrado, reverter.
