# Regras — Testes

## Estrutura

```
tests/
  pipeline/
    test_devig.py       — testes de de-vig (metodo_multiplicativo, metodo_shin)
    test_transform.py   — testes de compute_final_probability_dc
    test_extract.py     — testes do cliente BSD API (mocks)
  models/
    test_poisson.py     — testes DC grid, extract_btts_over25_prob
    test_calibration.py — testes do calibrador isotónico
  backtesting/
    test_engine.py      — testes do Backtester walk-forward
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
