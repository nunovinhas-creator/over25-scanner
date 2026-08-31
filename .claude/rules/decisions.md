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
| Critério de aceitação — DC vs. linha de fecho (investigação offline) | PRÉ-REGISTADO — 31 ago 2026, antes da 2ª tentativa (Max>2.5) | CLV rolling/médio > +1,0% E n ≥ 1000 E positivo em ≥4 das 5 épocas (2122–2526). Uma só variante nova por tentativa; sem 3ª tentativa se esta falhar. Ver detalhe abaixo. |

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

### Critério de decisão — DC vs. linha de fecho (investigação offline)

**Pré-registado:** 31 ago 2026, antes de correr a 2ª tentativa (variante `Max>2.5`) e antes de ver os resultados dessa corrida.

**Contexto:** PR #176 (mergeado) respondeu à pergunta "o Dixon-Coles bate o fecho da Pinnacle?" usando `P>2.5` (odd de abertura Pinnacle) como odd tomada — ver `backtesting/reports/dc_vs_closing.md`. Resultado: o modelo separa-se de controlos aleatórios (p≤0.05 em todos os thresholds do sweep), mas a CLV absoluta nunca fica positiva — o sinal não supera a margem embutida na odd de abertura Pinnacle.

**Critério (usa o mesmo padrão já aplicado para ler o estudo anterior):**
- CLV rolling/médio > **+1,0%**
- n ≥ **1000** apostas
- positivo em **≥4 das 5 épocas** (2122–2526)

**Regra de tentativa única:** a 2ª tentativa troca apenas a odd tomada para `Max>2.5` (melhor preço de mercado, ~2–4% acima de `P>2.5` em média nos dados — ver `backtesting/reports/dc_vs_closing_bestprice.md`), mantendo tudo o resto do estudo original (splitter, controlos, sweep, estratificação). Se esta variante não atingir o critério acima, **não há 3ª tentativa** — fica registado como resultado nulo e a linha de investigação "DC vs. fecho" fecha-se nesta forma. Isto evita procurar variantes sucessivas até uma "passar" por acaso (p-hacking).
