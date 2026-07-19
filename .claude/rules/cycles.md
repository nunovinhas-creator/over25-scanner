# Ciclos de Revisão e Critérios de Activação

## Estado actual: MODO OBSERVAÇÃO (todos os módulos)

Nenhum módulo está em apostas reais. Picks são gerados e tracked mas não apostados. Transição para apostas reais exige passar os gates CLV abaixo.

---

## Gates de activação por módulo

### Over 2.5 Scanner
- CLV rolling-30 > **+1%**
- n ≥ **300** settled (excluindo picks com `data_quality_flag`)
- Observação efectiva desde: **17 jun 2026**

### Sharp 1X2
- CLV rolling-30 > **+1%**
- n ≥ **200** settled (excluindo picks com `data_quality_flag`)
- Observação efectiva desde: **17 jun 2026** (351 picks anteriores excluídos)

### BTTS+Over 2.5
- CLV rolling-30 > **+5%** (threshold mais alto — menor n histórico)
- n ≥ **100** settled
- Observação efectiva desde: **21 jun 2026**

---

## Checkpoints formais

| Checkpoint | Data | O que verificar |
|---|---|---|
| **C3** | 30 jun 2026 | Workflows activos + CLV rolling primeiros picks reais (3 módulos) |
| **C4** | 15 jul 2026 | Primeira leitura com peso estatístico (n≈200 Over 2.5, n≈50 Sharp, n≈50 BTTS) |
| **C5** | 31 jul 2026 | Decisão de agosto: apostar ou manter MODO OBSERVAÇÃO por módulo |

**Não iniciar apostas reais sem passar pelo checkpoint imediatamente anterior.**

---

## Backlog técnico (por prioridade)

| Item | Estado | Critério de activação |
|---|---|---|
| `odds_fecho` real — CLV exacto Sharp 1X2 | Settlement + REST pós-KO activos (`pipeline/settle_sharp1x2.py` 2.5h–48h, `update_closing_odds.py` 15min–24h, ambos no job `settle_and_close_odds` de `sharp1x2_analysis.yml`, cron 30min) | Causa raiz encontrada em 19 jul 2026: nada definia `resultado_outcome`, por isso o fetch nunca tinha picks elegíveis — não era um problema do endpoint. Corrigido nesta sessão (data-quality-fixes, Ponto 2). Via WebSocket avaliada e **rejeitada em 11 jul 2026**: o Live WebSocket da BSD é addon pago ($3/mês) — decisão: não pagar. Falta acumular n suficiente de settlements reais via REST antes de confiar no CLV rolling-30 |
| 2º soft book no Sharp 1X2 (além da Bet365) | não iniciado | Melhorar robustez do sinal `div_b365_pin` |
| DRAW N1 Eredivisie | tracking 0/50 | 50 settled CLV>+1% → activar excepção Gate 2 para DRAW N1 |
| HOME N1 Eredivisie | bloqueado | 100 settled ao vivo → rever (histórico ROI −6.07%) |
| Skellam para 1X2 | não iniciado | Segundo sinal independente do DC para Sharp 1X2 |
| Walk-forward BTTS+O2.5 sem `--fast` | pendente | OOS com DC re-fit semanal (actual usa dc_ratings.json in-sample) |
| Live scanner "Prob Over 100% · Odd 1.00 · xG —" | **Resolvido** ([issue #127](https://github.com/nunovinhas-creator/over25-scanner/issues/127), sessão fix-suspended-odds) | `classify_odds()` em `pipeline/scan_common.py` classifica cada odd em VALID/SUSPENDED/MISSING (piso `MIN_VALID_ODDS=1.01` — 1.00 é a sentinela confirmada; status de mercado dedicado da BSD não confirmado nesta sessão, sem acesso a rede/API key). `enrich_event()` (`scan_live.py`) e `loadLive()` (`index.html`) nunca mais transformam a sentinela num preço real — odds suspensas/ausentes ficam `None`/`null` (mostram "—"), o evento fica registado explicitamente em stdout/console (`ODDS_STATUS=...`), e o scan retoma normalmente quando o mercado reabre. xG já falhava correctamente para `None` — não tinha o mesmo bug. Micro-fix relacionado: `settle_sharp1x2.py` ganhou guarda para nunca reescrever um `settlement_error` já registado (evitava commits `[skip ci]` repetidos em jogos presos sem resolução da BSD, PR #128) |
| `origem_alerta="live"` nunca gerado | não iniciado | `pipeline/scan_live.py` hoje só envia TG, não persiste picks próprios com os campos `score_no_alerta`/`minuto_no_alerta`/`origem_alerta` (ver `.claude/rules/data.md`) |

---

## Validação estatística de referência (Over 2.5, época 2526)

- Brier calibrado: **0.24168** vs mercado: **0.24320**
- CLV IC 95%: **[−0.985%, +1.366%]** (N=83)
- Walk-forward Sharp 1X2 Ronda 1 (2425): ROI +1.03%, CLV sim +2.50%
- Walk-forward Sharp 1X2 Ronda 2 (2526): ROI −10.10%, CLV sim +2.49%
- Walk-forward BTTS+O2.5: 22.429 jogos, WR 40.8%, zero leakage

---

## Regras de sessão (browser/mobile-only)

1. **Ficheiros completos** — nunca diffs, nunca fragmentos parciais
2. **Merge para main antes de fechar** — o Stop hook faz commit+push mas o merge PR→main deve ser feito durante a sessão
3. **Sem terminal local** — se algo só for possível via terminal, dizer explicitamente e propor alternativa (GitHub Actions, workflow_dispatch)
4. **Não iniciar apostas reais** sem passar pelos checkpoints C3→C4→C5
