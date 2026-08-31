# Decisões Permanentes

> Não reverter sem evidência nova. Cada decisão tem critério de revisão explícito.

## Tabela de decisões

| Decisão | Estado | Critério de revisão |
|---|---|---|
| `MODEL_WEIGHT=0.30` | FIXO | Melhor Brier calibrado em LOEO-CV. Nova validação completa obrigatória antes de alterar. |
| Kelly staking | DESACTIVADO | `ValueError` se `STAKE_TYPE ≠ "flat"`. Rever apenas quando CLV rolling-30 validado ao vivo por módulo. |
| Odds cap (`MAX_ODDS_OVER`) | REJEITADO | Evidência não-monotónica (>2.50: ROI +3.69%, N=35 insuficiente). `odds_band` gravado por pick para análise futura. |
| DRAW (todos os módulos) | SUSPENSO | Excepção: DRAW N1 Eredivisie em tracking (0/50). Activar excepção quando 50 settled com CLV>+1%. |
| HOME N1 (Sharp 1X2) | BLOQUEADO | ROI histórico −6.07% em 100 apostas. Rever quando n≥100 settled ao vivo. |
| Bundesliga 2 / Serie B | FORA da whitelist | BSD não disponibiliza estas ligas. Mantidas no histórico football-data.co.uk para backtesting. |
| `pin_drop` como sinal 1X2 | SUBSTITUÍDO | Desde 12 jun 2026: sinal é `div_b365_pin > 3%`. `pin_drop` gravado por pick mas não é gate. |
| `previous_decimal_odds` | NÃO é closing line | É a odd do scan anterior. CLV exacto requer fetch Pinnacle pós-KO (+10min). Ver testing.md. |
| Feed de odds BSD (scanner ao vivo) | CONGELADO — 25 ago 2026 | Cobertura de odds BSD ≥ 90% sustentada 7 dias seguidos (ou fonte alternativa validada). Ver detalhe abaixo. |
| Dixon-Coles vs. linha de fecho — fonte de edge Over 2.5 (investigação offline) | ENCERRADO — 31 ago 2026, resultado nulo (2/2 tentativas) | Critério pré-registado (CLV>+1,0%, n≥1000, ≥4/5 épocas) falhou em `P>2.5` (PR #176) e em `Max>2.5` (PR #177). Não reabrir com mais variantes de odd tomada — só com pergunta genuinamente nova, pré-registada à parte. Ver detalhe abaixo. |

---

## Detalhe por decisão

### Kelly staking — DESACTIVADO

O Kelly Criterion está implementado em `models/math/kelly.py` mas desactivado em produção. O código lança `ValueError` se `STAKE_TYPE ≠ "flat"`. Razão: CLV ao vivo ainda não validado com n suficiente para confiar na estimativa de edge. Activar Kelly com edge incerto pode levar a overbetting.

**Critério de activação:** CLV rolling-30 > +1% com n ≥ 300 settled (Over 2.5) ou equivalente por módulo.

### DRAW suspenso

DRAW está suspenso em **todos os módulos** com excepção de DRAW N1 Eredivisie, que está em tracking (objectivo: 50 settled com CLV>+1%). O ROI histórico do DRAW em geral não justifica excepção adicional sem evidência ao vivo.

**Não activar DRAW noutras ligas ou módulos sem evidência nova.**

### HOME N1 Sharp 1X2 — BLOQUEADO

HOME N1 tem ROI histórico de −6.07% em 100 apostas. O gate de divergência `div_b365_pin > 3%` não compensa a fraqueza estrutural deste outcome. Em tracking com objectivo de 100 settled ao vivo.

### Odds cap — REJEITADO

`MAX_ODDS_OVER` foi testado e rejeitado. A evidência não é monotónica (>2.50 tem ROI +3.69% mas N=35 é insuficiente para conclusão). O campo `odds_band` é gravado por pick para análise futura quando o n for suficiente.

### Whitelist de 10 ligas

Apenas as 10 ligas listadas em `data.md` podem gerar picks em produção. Adicionar uma liga exige:
1. Confirmar disponibilidade na BSD API
2. Confirmar histórico football-data.co.uk disponível (mínimo 1 época completa)
3. Re-treinar Dixon-Coles com a nova liga incluída
4. Validar out-of-sample antes de activar em produção

### Feed de odds BSD — CONGELADO (25 ago 2026)

**Data da decisão:** 25 de agosto de 2026.

**Causa:** o feed de odds da BSD Sports API deixou de devolver dados válidos. Série de cobertura diária (jogos das 10 ligas whitelisted com odds válidas / jogos candidatos), evidência dos runs de `scanner.yml` / `sharp1x2_analysis.yml` na Actions tab desse período:

| Data | Cobertura de odds BSD |
|---|---|
| 17 ago 2026 | 96% |
| 21 ago 2026 | 0% |
| 22 ago 2026 | 0% |
| 23 ago 2026 | 0% |
| 24 ago 2026 | 0% |

**Decisão:** não substituir a fonte de odds nem contratar feed pago alternativo. Todos os workflows dependentes da BSD ficam desactivados (Actions → Disable — ficheiros mantidos no repositório, não apagados):

`scanner.yml`, `sharp1x2_analysis.yml`, `live_scanner.yml`, `live_coverage_summary.yml`, `live_shadow_summary.yml`, `fetch_bsd_leagues.yml`, `probe_bsd_closing_odds.yml`, `probe_bsd_markets.yml`, `probe_bsd_odds_states.yml`, `probe_bsd_odds_transitions.yml`.

`historical_data.yml` e `retrain_dc.yml` mantêm-se activos — usam football-data.co.uk (gratuito), não dependem da BSD.

O repositório passa a **modo de investigação offline** sobre `data/historical/matches.csv` (23.766 jogos, 2021–2026, 13 divisões, odds de abertura e fecho Pinnacle).

**Tag git:** `v-freeze-2026-08` marca o estado de `main` no momento da decisão (commit `8e6a751`).

**O que é preciso para reverter:** cobertura de odds BSD (ou de uma fonte alternativa validada) ≥ 90% sustentada durante 7 dias seguidos. Reactivar os workflows um a um pela Actions tab (Enable workflow), começando por `scanner.yml`; só reactivar `sharp1x2_analysis.yml` e `live_scanner.yml` depois de confirmar picks reais consistentes no scanner principal.

### Dixon-Coles vs. linha de fecho Pinnacle — ENCERRADO (31 ago 2026)

**Pergunta:** o Dixon-Coles bruto (sem calibração), usado como sinal de selecção contra
a Pinnacle de abertura, bate a linha de fecho da Pinnacle — produz edge apostável
sobre Over 2.5?

**Critério pré-registado** (fixado a 31 ago 2026, antes da 2ª tentativa e antes de ver
os seus resultados; aplicado sem alteração às duas tentativas):
- CLV rolling/médio > **+1,0%**
- n ≥ **1000** apostas
- positivo em **≥4 das 5 épocas** (2122–2526)

**Tentativa 1 — odd tomada `P>2.5` (abertura Pinnacle).** PR #176,
[`backtesting/reports/dc_vs_closing.md`](../../backtesting/reports/dc_vs_closing.md).
O modelo separa-se de controlos aleatórios (uniforme e estratificado por banda de
odds, p≤0.05 em todos os thresholds do sweep 0–10pp) — sinal relativo real face à
linha de fecho. Mas a CLV absoluta nunca fica positiva em nenhum threshold; ao
threshold de referência (0.03): CLV −3,70%, n=5.935, 0/5 épocas com CLV positiva.
**Critério falha.**

**Tentativa 2 — odd tomada `Max>2.5` (melhor preço de mercado).** PR #177,
[`backtesting/reports/dc_vs_closing_bestprice.md`](../../backtesting/reports/dc_vs_closing_bestprice.md).
Mesmo splitter, mesmos três controlos, mesmo sweep, mesma estratificação — a única
variável trocada foi a odd tomada. Cobertura de `Max>2.5` face ao universo da
tentativa 1: 100% (19.684/19.684 jogos — os dois estudos são directamente
comparáveis). Ao threshold de referência (0.03): CLV −2,33%, n=5.935 (passa),
0/5 épocas com CLV positiva. Trocar para o melhor preço de mercado fechou cerca de
metade do fosso original (~+1,4pp) mas não superou a margem embutida do bookmaker.
**Critério falha.**

**Regra de tentativa única, cumprida:** conforme pré-registado, não houve procura de
uma 3ª variante (ex. `Avg>2.5`, `B365>2.5`) até uma "passar" por acaso — isso seria a
mesma forma de p-hacking que a regra existia para evitar.

**Conclusão:** o Dixon-Coles bruto não produz edge apostável sobre Over 2.5 — nem à
odd de abertura Pinnacle, nem à melhor odd de mercado disponível. Isto **não**
invalida o uso do Dixon-Coles no pipeline de produção (blend 30/70 com mercado,
calibração isotónica, gate de EV≥3% — ver `.claude/rules/backend.md`), que responde a
uma pergunta diferente (o modelo tem informação incremental face ao mercado, não que
bata a linha de fecho sozinho, sem calibração, à cabeça). É uma pergunta respondida,
não um resultado em aberto.

**Como reabrir:** não reabrir esta investigação com mais variantes de odd tomada. Só
reabre com uma pergunta genuinamente nova (ex.: sinal adicional, calibração aplicada
ao `p_dc` antes da comparação, janela temporal diferente) pré-registada à parte, com o
mesmo padrão de rigor (controlos, sweep, estratificação, critério fixado antes de
correr).

**Tag git:** `v-research-closed-2026-08` marca o estado de `main` no momento do
encerramento desta linha de investigação.
