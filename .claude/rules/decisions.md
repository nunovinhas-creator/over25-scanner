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
