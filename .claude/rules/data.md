# Regras — Dados

## Whitelist de Produção — 10 Ligas BSD

| ID BSD | Liga |
|---|---|
| 1 | Premier League |
| 2 | Primeira Liga |
| 3 | La Liga |
| 4 | Serie A |
| 5 | Bundesliga |
| 6 | Ligue 1 |
| 10 | Eredivisie |
| 12 | Championship |
| 14 | Belgian Pro League |
| 38 | La Liga 2 |

**Regra fail-closed:** `BSD_LEAGUE_ID_MAP` mapeia `league_id` → nome canónico. ID desconhecido/irresolúvel → `UNKNOWN_LEAGUE` ("DESCONHECIDA", nunca `''` — ver `pipeline/scan_common.py`) → whitelist rejeita com `reject_reason="liga_desconhecida"`. Nunca adicionar uma liga sem confirmar que a BSD API a suporta e que existe evidência histórica suficiente (mínimo 1 época completa).

**Bundesliga 2 e Serie B:** ausentes da BSD (65 ligas disponíveis) — presentes no histórico football-data.co.uk para backtesting, **nunca** geram picks em produção.

## Ficheiros de dados

```
data/dc_ratings.json          — fitted DC parameters por liga (auto-updated segundas)
data/calibrator.json          — isotonic calibrator (auto-updated segundas)
data/picks.json               — Over 2.5 picks (auto-scan 30 min)
data/picks_1x2.json           — Sharp 1X2 picks (auto-scan 30 min)
data/picks_btts_over25.json   — BTTS+O2.5 picks (id: {ev_id}_btts)
data/rejected_picks.json      — Over 2.5 rejeitados (análise de gates)
data/rejected_picks_1x2.json  — Sharp 1X2 rejeitados
data/scan_state_over25.json   — estado anterior do scan (deteção de movimento/DRIFTING)
data/observations.json        — observações live (tab Live)
data/historical/matches.csv   — histórico football-data.co.uk (auto-updated segundas)
data/historical/matches.parquet — versão parquet do histórico
data/schema/bsd_schema.py     — BSD API event schema e validação
data/schema/picks_schema.py   — picks.json schema e validação (Over 2.5)
data/schema/picks_1x2_schema.py — picks_1x2.json schema mínimo (Sharp 1X2)
data/schema/picks_btts_schema.py — picks_btts_over25.json schema mínimo (BTTS+O2.5)
```

## data_quality_flag

Picks anteriores a **17 jun 2026** (Over 2.5 e Sharp 1X2) têm `data_quality_flag=true` e são **excluídos de todos os KPIs** de produção. Picks anteriores a **21 jun 2026** (BTTS+O2.5) idem.

**Regra:** nunca remover o `data_quality_flag` de picks existentes para inflar o n do dashboard. Fazê-lo invalida os KPIs e constitui data leakage estatístico.

## Markets BSD confirmados

| Market | Parâmetro BSD |
|---|---|
| Over/Under 2.5 | `market=over_under_25` |
| 1X2 | `market=1x2` |
| BTTS | `market=btts`, outcome=`yes`/`no` |

## De-vig BTTS

```python
# Se yes + no disponíveis:
p_yes_devig = p_yes / (p_yes + p_no)

# Fallback (só yes disponível):
p_yes_devig = (1 / odds_yes) / 1.05
```

## Formato de pick (campos obrigatórios)

Cada entrada em `picks*.json` deve incluir:
- `ev_id` — event ID BSD
- `p_final` — probabilidade calibrada final
- `ev_final` — expected value (p_final × odds_over − 1, às odds brutas)
- `odds_band` — banda de odds no momento do pick
- `data_quality_flag` — bool (true se antes da data de activação)
- `league_id` — ID BSD da liga

Picks BTTS têm id `{ev_id}_btts`.

### Scoreline no momento do alerta (desde a sessão data-quality-fixes)

Os três tipos de pick (Over 2.5, Sharp 1X2, BTTS+O2.5) gravam também:
- `score_no_alerta` — scoreline no momento do alerta, ex. `"0-0"`
- `minuto_no_alerta` — minuto do jogo no alerta (string int), `null` se pré-KO
- `origem_alerta` — `"pre-ko"` ou `"live"`

Hoje **todos** os picks automáticos são `origem_alerta="pre-ko"` — os gates dos três scanners exigem `timing_h >= 0` (jogo ainda não começou). `"live"` está reservado para quando `pipeline/scan_live.py` passar a persistir os seus próprios picks (não persiste hoje — ver backlog em `.claude/rules/cycles.md`). Campos ausentes em picks anteriores a esta sessão são `null`/`NaN` após validação (retrocompatível — ver `data/schema/picks_schema.py`, `picks_1x2_schema.py`, `picks_btts_schema.py`).
