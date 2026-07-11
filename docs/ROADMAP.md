# Roadmap — pós-auditoria QuantCode (jul 2026)

Orientado ao milestone do produto: **decisão C5 (31 jul 2026)** — apostar ou manter observação, por módulo.

## Agora → C4 (15 jul 2026)

| # | Item | Porquê | Esforço |
|---|---|---|---|
| 1 | Confirmar acumulação de `odds_fecho` reais (REST pós-KO) e n de closings por módulo | Sem closings reais, o C5 decide sobre um proxy | S |
| 2 | Verificar n settled projectado vs gates (300/200/100) e reportar em `update_readme` | Saber já se o C5 terá poder estatístico | S |

## C4 → C5 (15–31 jul 2026)

| # | Item | Porquê | Esforço |
|---|---|---|---|
| 3 | Relatório C5 por módulo: CLV rolling-30 (proxy e exacto quando disponível), WR vs p_final (calibração live), n settled | É o deliverable da tese | M |
| 4 | Renomear/reinterpretar `clv_btts_over25` → `joint_ratio` na leitura do C5 (sem mudar o gate) | O rácio contém correlação estrutural, não é CLV (`MODEL_REVIEW.md` §6) | S |

## Época 2627 (pós-C5)

| # | Item | Porquê | Esforço |
|---|---|---|---|
| 5 | Unificar configuração: scanners lêem `pipeline/config.py` (uma fonte para MODEL_WEIGHT, MIN_EV, bandas, whitelist via scan_common) | Dívida principal de arquitectura (`ARCHITECTURE_REVIEW.md` P4) | M |
| 6 | Retenção de 90 dias em `rejected_picks.json` | Crescimento sem limite (P7) | S |
| 7 | `engine.py`: filtrar `result ∈ {WIN, LOSS}` dentro de `run()` e re-gerar relatórios com nota de versão | Fail-dangerous com picks não-settled (P6) | S |
| 8 | Reduzir `pipeline/etl.py`: mover `--validate` para módulo próprio, remover `run_etl`/caminho GAS morto | DEPRECATE → remoção limpa | M |
| 9 | Se módulo 2 activado: 2º soft book além da Bet365 (backlog existente) | Robustez do sinal div | M |
| 10 | Se módulo 2 encerrado e houver apetite: Skellam como 2º sinal 1X2, pré-registado | Backlog existente (`cycles.md`) | L |
| 11 | Remover xfail do CLV exacto Sharp 1X2 quando n de closings reais ≥ 100 | Regra em `testing.md` | S |
| 12 | Consolidar `kelly_full` (engine vs models/math/kelly) se Kelly reactivar | Duplicação anotada | S |

## Direcções de investigação futura (sem compromisso)

- **Calibração live contínua:** re-ajustar o calibrador com picks settled live (com guarda anti-leakage — só após n≥300) em vez de só histórico.
- **xi (decaimento DC) por liga:** validar se 0.0018 é óptimo por LOEO em vez de universal.
- **Modelo de margem (Skellam/bivariado)** como segundo sinal independente do 1X2.
- **Estimador de correlação BTTS×O2.5 de mercado:** se a BSD algum dia expuser odds do mercado conjunto (BTTS+Over), o pseudo-CLV do módulo 3 passa a CLV verdadeiro sem mudar código de modelo.
- **Comparação Brier DC vs CatBoost BSD** com os campos `prob_over25_ml` já registados por pick (quando n settled ≥ 200).
