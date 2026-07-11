# Scope do Produto — auditoria QuantCode (jul 2026)

Regra única: **uma feature só entra se encurtar o caminho até uma decisão C5 fiável** (apostar ou não, por módulo). Tese: [`PRODUCT_THESIS.md`](PRODUCT_THESIS.md).

## Em scope (produção)

| Feature | Estado | Justificação |
|---|---|---|
| Over 2.5 (DC + isotónica + blend 30/70, gate EV≥3%) | ✅ activo | Hipótese 1 pré-registada; gate CLV>+1%, n≥300 |
| Sharp 1X2 (div_b365_pin>3%, gates de liga/outcome/timing) | ✅ activo | Hipótese 2 pré-registada; gate CLV>+1%, n≥200 |
| BTTS+O2.5 (grelha bivariada DC, gate joint-ratio≥5%) | ✅ activo | Hipótese 3 pré-registada; gate ≥+5%, n≥100 (ver ressalva `MODEL_REVIEW.md` §6) |
| CLV rolling-30 por módulo + relatório TG semanal | ✅ activo | É a métrica de decisão |
| Closing odds reais via REST pós-KO (`update_closing_odds.py`) | ✅ activo | Converte o proxy de CLV em CLV exacto — melhora directamente a qualidade da decisão C5 |
| Monitorização EV (registo de picks/rejeitados + data quality diário) | ✅ activo | Registo imutável = matéria-prima da experiência |
| Dashboard/tracker (index.html, tracker.html, analytics) | ✅ activo | Cockpit do operador; leitura, não fonte de verdade |
| Campos informativos (H2H, prediction CatBoost BSD, indisponíveis) | ✅ activo | Não são gates; contexto para leitura humana dos alertas — custo marginal ~zero |

## Em tracking (não geram apostas nem alertas de activação)

| Item | Condição de saída |
|---|---|
| DRAW N1 Eredivisie | 50 settled com CLV>+1% → activa excepção |
| HOME N1 | 100 settled ao vivo → rever bloqueio (histórico −6.07%) |

## Fora de scope (avaliado e recusado)

| Feature | Decisão | Razão |
|---|---|---|
| **Kelly staking / bankroll tracking** | Fora até gate CLV | Decisão permanente (`decisions.md`); não há staking em modo observação, logo não há bankroll para gerir. Reentra automaticamente com a activação de apostas reais. |
| **Under 2.5** | Fora | É o complemento do mercado já modelado; um sinal Under com o mesmo modelo é a mesma hipótese com sinal trocado — não acrescenta informação, dobra as comparações. |
| **Asian Handicap** | Fora | Exigiria modelo de margem de vitória validado (Skellam está no backlog como *sinal 1X2*, não AH), nova fonte de odds e novo gate. Nada disto serve a decisão C5. |
| **Correct Score** | Fora | Mercado de variância altíssima, liquidez baixa fora das big leagues; DC produz a grelha mas a validação exigiria n enorme. |
| **Odds history / time-series completa de odds** | Fora (mantém-se `scan_state` mínimo) | Guardar séries completas de odds por evento multiplicaria o storage por ordens de grandeza para responder a perguntas que o CLV pós-KO já responde melhor. |
| **Mais ligas além das 10 whitelisted** | Fora | Whitelist é decisão permanente com processo de entrada definido (histórico + re-treino + OOS). |
| **Multi-utilizador / comercialização / API pública** | Fora | Tese 3 e 5 rejeitadas em `PRODUCT_THESIS.md`. |
| **Novos modelos Over 2.5 (Elo, ML)** | Fora | Elo removido (`KILL_LEDGER.md`); a prediction CatBoost da BSD fica registada por pick apenas para futura comparação de Brier — não é sinal. |

## Critério para reabrir o scope

Só no checkpoint C5 (31 jul 2026) ou em checkpoint posterior, com:
1. A decisão do módulo existente tomada (activado ou encerrado);
2. Hipótese nova escrita *antes* de olhar para dados live (sinal, gate, n mínimo, threshold);
3. Registo em `decisions.md`/`cycles.md`.
