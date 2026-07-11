# Revisão de Arquitectura — auditoria QuantCode (jul 2026)

Tese de referência: [`PRODUCT_THESIS.md`](PRODUCT_THESIS.md).

## Visão geral

```
football-data.co.uk ──(semanal)──▶ matches.csv ──▶ train_dc.py ──▶ dc_ratings.json
                                        │                              │
                                        ▼                              ▼
                              run_calibration.py ──────────▶ calibrator.json
                                                                       │
BSD Sports API ──(30 min)──▶ scan_over25.py / scan_sharp1x2.py ◀───────┘
                                        │
                                        ▼
                    data/picks*.json (registo imutável, commit em main)
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
          index.html (UI)      Telegram (alertas)     roi_metrics (CLV rolling)
```

A separação data acquisition → modelling → pricing → gates → delivery existe e está no sítio certo. Os problemas eram de **duplicação e drift**, não de desenho.

## Pontos fortes

1. **Fail-safe/fail-closed consistente:** extract devolve lista vazia em erro; whitelist rejeita IDs desconhecidos; TG e git falham sem matar o scan; `STAKE_TYPE≠flat` lança `ValueError` (a decisão Kelly-off está *imposta pelo código*, não só documentada — excelente).
2. **Registo append-only com deduplicação por id** e razões de rejeição explícitas (`reject_reason`) — o dataset de rejeitados permite analisar os gates a posteriori.
3. **Separação treino/inferência:** artefactos (`dc_ratings.json`, `calibrator.json`) versionados no repo, re-gerados por workflow semanal; a inferência do scanner é pura leitura. Reprodutível e auditável.
4. **Testes com mocks e sem rede** para os caminhos críticos (gates, dedup, alertas), mais um workflow diário de data quality.
5. **UI sem build** — restrição browser/mobile-only do operador respeitada em todo o stack.

## Problemas encontrados (e o que foi feito)

### P1 — Duplicação entre scanners (CORRIGIDO)
`WHITELIST`, `BSD_LEAGUE_ID_MAP`, `send_telegram`, `git_commit_push`, `_load_list`, `_save_list` estavam copiados integralmente em `scan_over25.py` e `scan_sharp1x2.py`. Uma edição na whitelist num ficheiro e não no outro faria os módulos divergirem silenciosamente. **Extraído para `pipeline/scan_common.py`** (fonte única); os scanners re-exportam os nomes, preservando o patching dos testes.

### P2 — Alerta Sharp 1X2 com outcome hardcoded (CORRIGIDO)
`_build_msg` imprimia `Outcome: HOME` fixo. Corrigido para `pick['outcome']`. Detalhe agravante: HOME está bloqueado na Eredivisie e DRAW nunca alerta, portanto os alertas mais frequentes (AWAY) diziam o lado errado.

### P3 — `movement` fail-open (CORRIGIDO)
Eventos sem `movement` da BSD eram registados como `SHORTENING`. Agora `UNKNOWN`. Gates inalterados (só DRIFTING rejeita); o registo deixa de fabricar sinal. Ver `MODEL_REVIEW.md` §8 e `KILL_LEDGER.md` §3.

### P4 — `pipeline/config.py` não é usado pela produção (DOCUMENTADO, roadmap)
Existe um `Config` dataclass com validação — mas os scanners hardcodam `MODEL_WEIGHT`, `MIN_EV`, bandas de odds e whitelist. Dois sítios para a mesma verdade = drift garantido a prazo (a whitelist já esteve duplicada 3×: config, scanner A, scanner B — agora 2×: config e scan_common). **Decisão:** não migrar os scanners para `load_config()` nesta auditoria (mexeria no caminho quente de produção sem testes de integração equivalentes); item de roadmap com prioridade alta. A duplicação restante está agora anotada em ambos os ficheiros.

### P5 — `pipeline/etl.py` é arquitectura legada (DOCUMENTADO)
O desenho original (ETL GAS-centrado com `run_etl`) foi ultrapassado pelos scanners autónomos. Só os `filter_*` e o `--validate` (workflow diário) têm uso vivo. Classificado DEPRECATE no inventário; plano de redução na época 2627.

### P6 — Motor de backtest fail-dangerous com picks não-settled (DOCUMENTADO)
`engine.py` conta qualquer `result_over25 ≠ "WIN"` como LOSS. Callers actuais filtram antes, mas a API convida ao erro. Correção recomendada (filtrar para `{WIN, LOSS}` dentro de `run()`), adiada para não alterar números de relatórios históricos durante o período de observação — mudar o denominador dos backtests a 20 dias do C5 confundiria a comparação com os relatórios já publicados.

### P7 — `rejected_picks.json` cresce sem limite (DOCUMENTADO, roadmap)
1.8 MB / 4.791 entradas e a crescer a cada scan. Não é bug, mas commits de 30 em 30 minutos com um ficheiro sempre crescente degradam clone e diffs. Recomendação: retenção de 90 dias no próprio scan.

### P8 — `index.html` monólito de 320 KB (ACEITE)
Estado global, ~7.600 linhas, lógica de negócio no cliente. Mitigado por extração incremental para `js/` (8 módulos já carregados). Reescrever agora viola o princípio de estabilidade pré-C5. O front-end é *cockpit*, não é a fonte de verdade — os KPIs de decisão vêm do pipeline Python.

### P9 — Consistência menor
- `models/math/kelly.py` vs `backtesting/engine.py::kelly_full` — implementações duplicadas (consistentes entre si); consolidar quando Kelly sair de DEPRECATE.
- `scan_sharp1x2._get_list` devolve tuplo `(page, next)` mas chama-se `_get_list` — nome enganador; menor.
- `git_commit_push` faz `reset --soft origin/main` antes de commitar — correcto para o caso de uso (workflows concorrentes), mas merece o comentário que agora tem em `scan_common.py`.

## Avaliação por dimensão

| Dimensão | Nota (0-10) | Notas |
|---|---|---|
| Modularidade | 7 | Camadas certas; ETL legado e config paralela penalizam |
| Coesão | 8 | Módulos de matemática exemplares (docstrings, referências) |
| Acoplamento | 7 | Scanners → BSD directo (aceitável para 1 fornecedor); UI → GAS via JSONP é o ponto mais frágil |
| Error handling | 8 | Fail-safe consistente; excepções largas mas sempre logadas |
| Configuração | 5 | Duas fontes de verdade (config.py vs constantes) — principal dívida |
| Testabilidade | 8 | Mocks completos dos scanners; suite verde em 10 min |
| Manutenibilidade | 7 | Docs de governança fortes; index.html pesa contra |
| Determinismo | 9 | Backtests determinísticos, artefactos versionados |
