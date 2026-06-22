---
description: Orquestra os 4 subagentes em sequência — fetch BSD → modelo DC → CLV tracker → alerta TG. Confirma o estado dos 3 módulos em produção numa única invocação.
allowed-tools:
  - Agent
  - Bash
  - Read
---

# /scan — Orquestrador de Scan Manual

Corre o pipeline completo de observação em 4 passos sequenciais, delegando em cada passo ao subagente especializado.

## Contrato de execução (obrigatório)

Deves completar os 4 passos pela ordem indicada. Não podes:
- Saltar o Passo 1 (os eventos são input dos passos seguintes)
- Chamar o telegram-notifier antes do clv-tracker terminar
- Inferir ou inventar dados — cada agente trabalha com dados reais da BSD API

## Execução

### Passo 1 — Fetch BSD API
Invoca o subagente `data-fetcher`:
```
Agent: data-fetcher
Tarefa: Fetch eventos de hoje + odds over/under + odds BTTS da BSD API. Reporta total de eventos por liga e disponibilidade de odds.
```
Regista o output: número de eventos, ligas cobertas, disponibilidade de odds.

---

### Passo 2 — Modelo Dixon-Coles
Invoca o subagente `model-runner`:
```
Agent: model-runner
Tarefa: Aplica o pipeline DC+calibrador aos eventos do Passo 1. Reporta top-5 por EV e quantos passam o gate EV ≥ 3%.
```
Regista o output: top events, breakdown de p_model_source.

---

### Passo 3 — CLV e ROI
Invoca o subagente `clv-tracker`:
```
Agent: clv-tracker
Tarefa: Lê picks dos 3 módulos (picks.json, picks_1x2.json, picks_btts_over25.json), exclui data_quality_flag, calcula CLV rolling-30 e WR por módulo. Verifica se algum gate de activação foi atingido.
```
Regista: CLV por módulo, n settled, estado do gate.

---

### Passo 4 — Alerta TG (condicional)
Invoca o subagente `telegram-notifier` **apenas se** o Passo 3 reportar pelo menos um gate atingido:
```
Agent: telegram-notifier
Tarefa: Envia alerta TG para chat 1352687611 com o módulo, CLV exacto e n settled. Só envia se gate atingido — nunca envia alerta falso.
```
Se nenhum gate atingido, reporta "Nenhum gate atingido — MODO OBSERVAÇÃO mantido" e não invoca o agente.

---

## Sumário final

Após os 4 passos, apresenta uma tabela resumo:

| Módulo | Eventos fetched | Gate EV | CLV roll-30 | n settled | Gate activo? |
|---|---|---|---|---|---|
| Over 2.5 | — | ≥3% | — | — | Não |
| Sharp 1X2 | — | div>3% | — | — | Não |
| BTTS+O2.5 | — | CLV≥5% | — | — | Não |

## Contexto relevante

- Observação efectiva: Over 2.5 e Sharp 1X2 desde 17 jun 2026; BTTS desde 21 jun 2026
- Gates de activação: Over 2.5 CLV>+1% n≥300 | Sharp CLV>+1% n≥200 | BTTS CLV>+5% n≥100
- Próximos checkpoints: C3=30Jun 2026 | C4=15Jul 2026 | C5=31Jul 2026
