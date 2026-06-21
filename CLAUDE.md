# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Regras Globais do Autor — nunovinhas-creator

> Estas regras aplicam-se a TODOS os repositórios. As regras específicas abaixo têm prioridade em caso de conflito.

- Trabalho **100% via browser/mobile** — GitHub web editor, DevTools, GitHub Actions
- Sem terminal local — sempre dar **ficheiros completos para colar**, não diffs
- Comentários em **português europeu**, variáveis/funções em **inglês**
- Preferência por **Python** em automações, **Astro** em frontend
- Respostas sempre em **português europeu**, diretas e sem rodeios
- Se algo não funcionar via browser, dizer explicitamente

### Domínios prioritários
1. **Apostas** — sharp money, CLV, movimentos de odds, xG, Poisson
2. **Mercados financeiros** — ETFs, padrões técnicos, alertas automáticos
3. **Marketing digital** — SEO, afiliação, conteúdo, redes sociais
4. **Automação Python** — GitHub Actions, APIs REST, Telegram bots

### Integrações globais
| Serviço | Detalhe |
|---|---|
| Telegram Bot | Chat ID `1352687611` |
| The Odds API | Odds futebol europeu |
| BSD Sports API | EPL=1, La Liga=3, Serie A=4 |
| CJ Affiliate | Publisher ID `7625702` |

---

## Skills Ativas — AbsolutelySkilled

### absolute-work
Ciclo completo de desenvolvimento fase a fase com gates obrigatórios.
Usar quando: "build this end-to-end", "plan and build", "break into tasks", "pick up this ticket", qualquer tarefa multi-step.
Fases: **INTAKE & BRAINSTORM → SPEC → DECOMPOSE & PLAN → EXECUTE → VERIFY → CONVERGE**
- Parar no fim de cada fase e esperar aprovação explícita antes de avançar
- Nada é assumido, nenhum código escrito antes do design aprovado
- Board de tarefas persistido em `.absolute-work/board.md`
- TDD por tarefa; tarefas independentes podem paralelizar, bloqueadas correm sequencialmente

### absolute-simplify
Simplificação autónoma de código antes de commit.
Usar quando: "simplify this", "refactor", "clean up", "reduce complexity", "remove dead code", "tidy this up".
- Detetar mudanças staged/unstaged, analisar oportunidades de simplificação
- Aplicar melhorias, correr testes, mostrar resumo com reasoning
- Linguagem-agnóstico; opiniões fortes em JS/TS/React, Python, Go

### absolute-ui
Design de interfaces polido e intencional.
Usar quando: componentes, layout, cores, tipografia, espaçamento, responsive, dark mode, acessibilidade, animações.
- Cobre CSS, Tailwind, princípios framework-agnósticos
- Objetivo: parecer intencional, não gerado por IA

### absolute-documentations
Documentação driven pelo framework Diátaxis.
Usar quando: "write docs", "document this", "write a README", "improve this doc", "audit our docs".
- Quadrantes: tutorial, how-to, reference, explanation
- Deteta stack de docs (Docusaurus, MkDocs, VitePress, plain Markdown) e segue as suas convenções
- Docs de repo: README, CONTRIBUTING, ARCHITECTURE, ADRs, changelogs, runbooks

---

## Deployment Rule (mandatory)

After **every implementation** — no matter how small — always complete this pipeline without waiting to be asked:

1. `git add` the changed files
2. `git commit` with a descriptive message
3. `git fetch origin claude/claude-md-docs-AUJLa && git rebase origin/claude/claude-md-docs-AUJLa && git push origin HEAD:claude/claude-md-docs-AUJLa`
4. Create a PR from `claude/claude-md-docs-AUJLa` → `main` via `mcp__github__create_pull_request`
5. Merge the PR immediately via `mcp__github__merge_pull_request` (squash method)
6. **After merge**: `git fetch origin main && git reset --hard origin/main` — syncs local branch with squash commit
7. **Anchor commit** (mandatory after every merge): `git config user.email noreply@anthropic.com && git config user.name Claude && git commit --allow-empty -m "chore: verified branch anchor" && git push origin HEAD:claude/claude-md-docs-AUJLa --force-with-lease` — this ensures the branch tip is always a Claude-signed commit, preventing the Stop hook from flagging unverified GitHub squash-merge or data-sync commits
8. If there are merge conflicts: `git checkout --theirs data/` for data files, `git checkout --ours index.html` for app code

The Stop hook (`/home/user/over25-scanner/.claude/auto-push.sh`) handles commit+push for anything left uncommitted at session end, but step 4–6 (PR + merge + reset) must be done by Claude during the session.

---

## Overview

Estado do sistema a **21 jun 2026** — três módulos em produção, todos em MODO OBSERVAÇÃO.

**Camada 1 — Front-end estático** (browser, sem build):
- **`index.html`** — "Over 2.5 SCOUT": scanner, Sharp 1X2, monitor live, dashboard (tabs: Over 2.5, Sharp 1X2, BTTS+O2.5, Live)
- **`tracker.html`** — "Over 2.5 MONITOR": ROI curve, breakdown por filtro, picks table (GAS Sheet dedicada)

**Camada 2 — Pipeline Python** (GitHub Actions):
- Dixon-Coles por liga (`models/train_dc.py`) → `data/dc_ratings.json`
- Calibrador isotónico LOEO-CV (`backtesting/run_calibration.py`) → `data/calibrator.json`
- Três scanners automáticos (`pipeline/scan_over25.py`, `pipeline/scan_sharp1x2.py`) — correm a cada 30 min

Open either HTML file directly in a browser to run. No build steps, no tests, no linters for the front-end.

---

## Três Módulos em Produção

### 1 — Over 2.5 Scanner

| Campo | Valor |
|---|---|
| Sinal | Dixon-Coles + calibração isotónica + blend mercado (`MODEL_WEIGHT=0.30`) |
| Gate | EV ≥ 3% (`MIN_EV`) + liga whitelisted + não-DRIFTING + timing < 6h KO |
| CLV proxy | `div_over_pin` (Over 2.5 Pinnacle vs NVP) |
| Validação | FASE 4 (época 2526): Brier calibrado=0.24168 vs market=0.24320; CLV IC 95% [−0.985%, +1.366%] (N=83) |
| Activação apostas reais | CLV rolling-30 > +1% com n ≥ 300 settled |
| Observação efectiva | **17 jun 2026** — picks anteriores têm `data_quality_flag` e excluídos dos KPIs |

### 2 — Sharp 1X2

| Campo | Valor |
|---|---|
| Sinal | Divergência Bet365/Pinnacle > 3% (`div_b365_pin`) |
| Gate | div > 3% + liga whitelisted + não-DRAW + HOME N1 bloqueado + timing 0–6h KO |
| CLV proxy | `div_b365_pin` (B365/NVP−1, %). CLV exacto requer `odds_fecho` pós-KO (xfail activo) |
| Evidência histórica | 21.087 jogos, ROI +2.46% em 3.731 apostas (threshold >3%) |
| Walk-forward | Ronda 1 (2425): ROI +1.03%, CLV sim +2.50%; Ronda 2 (2526): ROI −10.10%, CLV sim +2.49% — CLV positivo em ambas |
| Em tracking | DRAW N1 Eredivisie: 0/50 settled; HOME N1: 0/100 settled (bloqueado: ROI histórico −6.07%) |
| Activação apostas reais | CLV rolling-30 > +1% com n ≥ 200 settled |
| Observação efectiva | **17 jun 2026** — 351 picks anteriores com `data_quality_flag` excluídos dos KPIs |

### 3 — BTTS+Over 2.5

| Campo | Valor |
|---|---|
| Sinal | Grelha bivariada DC — `p_dc_conjunta = P(BTTS AND O2.5)` via `build_dc_grid()` + `extract_btts_over25_prob()` |
| Gate | `clv_btts_over25 ≥ 5%` + EV over25 ≥ 3% + liga whitelisted |
| CLV real | `p_dc_conjunta / (p_btts_market × p_over25_market) − 1`; BSD market `btts` outcome=yes/no confirmado |
| De-vig BTTS | Se yes+no disponíveis: `p_yes/(p_yes+p_no)`; senão: `(1/odds_yes)/1.05` (fallback) |
| Picks | `data/picks_btts_over25.json` — auto-scan, formato `{ev_id}_btts` |
| Alertas TG | Activos: `⚽ BTTS+Over 2.5 — CLV +X.X% vs mercado` |
| Backtest | Walk-forward: 22.429 jogos, WR 40.8%, zero leakage (gate overlay não discrimina → CLV gate substituiu) |
| Activação apostas reais | CLV rolling-30 > +5% com n ≥ 100 settled |
| Observação efectiva | **21 jun 2026** |

**Markets BSD confirmados:** `market=1x2`, `market=over_under_25`, `market=btts` (outcome=yes/no)

---

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

Bundesliga 2 e Serie B: ausentes da BSD (65 ligas disponíveis) — presentes no histórico football-data.co.uk para backtesting, nunca geram picks em produção. `BSD_LEAGUE_ID_MAP` no código usa estes IDs para mapear `league_id` → nome canónico (fail-closed: ID desconhecido → `''` → whitelist rejeita).

---

## Infraestrutura Automática

| Workflow | Trigger | O que faz |
|---|---|---|
| `scanner.yml` | a cada 30 min | Over 2.5 scan + Sharp 1X2 scan + commit `data/picks*.json` |
| `historical_data.yml` | seg 06:00 UTC | Actualiza `data/historical/matches.csv` (football-data.co.uk) |
| `retrain_dc.yml` | seg 07:00 UTC | Re-treina DC + calibrador + relatório TG Sharp 1X2 semanal |
| `deploy_version.yml` | cada push main | Actualiza `version.json` + `BUILD_SHA` em `index.html` [skip ci] |
| `dashboard.yml` | trigger | Gera dashboard HTML analítico |
| `data_quality.yml` | diário 07:00 UTC | Schema + data quality + backtests automáticos |
| `sharp1x2_analysis.yml` | workflow_dispatch | Q1–Q6 analysis report Sharp 1X2 |
| `probe_bsd_markets.yml` | workflow_dispatch | Diagnóstico markets disponíveis na BSD API |

**Secrets necessários:** `BSD_API_KEY`, `TG_TOKEN`, `TG_CHAT_ID`

---

## Decisões Permanentes (não reverter sem evidência nova)

| Decisão | Estado | Critério de revisão |
|---|---|---|
| `MODEL_WEIGHT=0.30` | fixo | Melhor Brier calibrado em LOEO-CV. Nova validação obrigatória. |
| Kelly staking | DESACTIVADO | `ValueError` se `STAKE_TYPE ≠ "flat"`. Rever quando CLV validado ao vivo. |
| Odds cap (`MAX_ODDS_OVER`) | REJEITADO | Evidência não-monotónica (>2.50: ROI +3.69%, N=35 insuficiente). `odds_band` gravado por pick. |
| DRAW (todos os módulos) | SUSPENSO | Excepção: DRAW N1 Eredivisie em tracking. Activar quando 50 settled CLV>+1%. |
| HOME N1 (Sharp 1X2) | BLOQUEADO | ROI histórico −6.07%, 100 apostas. Rever quando n≥100 ao vivo. |
| Bundesliga 2 / Serie B | FORA da whitelist BSD | BSD não tem estas ligas. Mantidas no histórico para backtesting. |
| `pin_drop` como sinal 1X2 | SUBSTITUÍDO | Desde 12 jun 2026: sinal é `div_b365_pin > 3%`. `pin_drop` gravado por pick mas não é gate. |
| `previous_decimal_odds` | NÃO é closing line | É a odd do scan anterior. CLV exacto requer fetch Pinnacle pós-KO (+10min). |

---

## Ciclos de Revisão

| Checkpoint | Data | O que verificar |
|---|---|---|
| C3 | 30 jun 2026 | Workflows activos + CLV rolling primeiros picks reais (3 módulos) |
| C4 | 15 jul 2026 | Primeira leitura com peso estatístico (n≈200 Over 2.5, n≈50 Sharp, n≈50 BTTS) |
| C5 | 31 jul 2026 | Decisão de agosto: apostar ou manter observação (gates CLV por módulo) |

---

## Backlog Técnico (por prioridade)

| Item | Estado | Critério de activação |
|---|---|---|
| `odds_fecho` real — CLV exacto Sharp 1X2 | xfail activo | Fetch Pinnacle pós-KO (+10min): `decimal_odds` nesse momento = closing line. `previous_decimal_odds` não serve. |
| 2º soft book no Sharp 1X2 (além da Bet365) | não iniciado | Melhorar robustez do sinal `div_b365_pin` |
| DRAW N1 Eredivisie | tracking 0/50 | 50 settled CLV>+1% → activar excepção Gate 2 para DRAW N1 |
| HOME N1 Eredivisie | bloqueado | 100 settled ao vivo → rever (histórico ROI −6.07%) |
| Skellam para 1X2 | não iniciado | Segundo sinal independente do DC para Sharp 1X2 |
| Walk-forward BTTS+O2.5 sem `--fast` | pendente | Out-of-sample com DC re-fit semanal (backtest actual usa dc_ratings.json in-sample) |

---

## External Dependencies

All logic is inline JavaScript. Three external services are used:

| Service | Purpose | Config |
|---|---|---|
| BSD Sports API (`https://sports.bzzoiro.com`) | Match data, odds, predictions, live events | API key entered at runtime, saved to `localStorage` |
| Google Apps Script (GAS) | Serverless data persistence for picks | Two hardcoded `SHEET_URL` constants (one per file) |
| Telegram Bot API | Push notifications for picks and daily reports | Token + Chat ID entered at runtime |

## Architecture

### State (index.html)
Global variables hold all in-memory state:
- `allGames` — enriched scanner events
- `allSharp1x2` — Sharp 1X2 signals
- `sharp1x2Picks` — saved 1X2 picks with results
- `liveData` — live match data
- `allPicks` — Over 2.5 picks from the sheet

### Data Flow
1. `loadAll()` fetches events, predictions, over/under odds, and 1X2 odds from BSD in parallel
2. Events are enriched in batches of 8 (detail + Pinnacle odds per event)
3. `calcScore()` computes a 0–100 score per game from: ML probability (up to 40 pts), xG (up to 20 pts), BTTS (up to 15 pts), sharp money signals (up to 15 pts), divergence, H2H
4. Sharp 1X2 detection runs separately on the 1X2 odds data and labels signals as STEAM/SHARP/WATCH based on Pinnacle movement % and timing

### CORS Workaround (GAS communication)
Since GAS endpoints don't support CORS, all reads/writes use a JSONP pattern: a `<script>` tag is injected with a callback parameter. If JSONP fails, a fallback fires a GET via `<img src>` (pixel ping) — this write-only method still triggers the GAS doGet handler even without reading the response. See `sheetSave()` and `sheetGet()`.

### Pick Normalization
`normalizePick()` in `index.html` normalises field names from GAS responses (multiple aliases per field), cleans odds values (rejects dates accidentally stored as odds), and extracts base event IDs from composite IDs (e.g. `123456_sh`, `123456_live_1746123456789`).

### Sharp Detection Logic
Defined inside `loadAll()` — the `sharpRaw` build loop scores each 1X2 outcome on:
- Pinnacle movement ≥5% → 50 pts, ≥3% → 35 pts, ≥2% → 20 pts, ≥1% → 10 pts
- Timing bonus multiplier: ×3 if <2h to KO, ×2 if <6h, ×1.5 if <12h
- Multi-book confirmation (2+ recreational bookmakers also moving): +15 pts
- Shortening direction: +5 pts
- Minimum score threshold: 8 pts to appear

Labels: `STEAM` = movement ≥5% and ≤30min to KO; `SHARP` = movement ≥5% or (≥2% and ≤12h); `WATCH` = anything above threshold.

### Auto-reload de versão (index.html)
`checkVersion()` corre no load e a cada 5 min: faz `fetch('version.json', {cache:'no-store'})`, compara o SHA remoto com `BUILD_SHA` embutido no HTML. Se divergirem, limpa caches e faz `location.reload()`. Flag `reload_attempted_<sha>` em `sessionStorage` evita loop infinito. Reload é adiado se `_autoLogRunning=true`.

`version.json` e `BUILD_SHA` são actualizados pelo workflow `deploy_version.yml` em cada push para `main` (commit `[skip ci]`). Se GitHub Pages/Fastly cachear `version.json`, a deteção pode demorar até ao TTL da cache (≈10 min).

### Key Constants (index.html)
```js
TH_MOVE   = 1.0   // minimum % Pinnacle movement
DIV_MIN   = 2.0   // minimum % Pin vs recreational divergence
DIV_STEAM = 8.0   // % threshold for STEAM classification
RECR = ['bet365','bwin','unibet', ...]  // recreational bookmaker slugs
```

## UI Structure (index.html)

Four tabs, each with a `panel-{name}` div:
- **Scanner** — game cards sorted by score with collapsible detail
- **Sharp 1X2** — sub-tabs: Sinais (signals list) and Dashboard 1X2 (analytics)
- **Live** — auto-refreshes every 30s, alerts TG when saved picks are in danger (>60' with <3 goals)
- **Dashboard** — ROI curve, breakdown by filter (SHORTENING, xG, BTTS, Sharp), picks table

Config (API key, TG token, chat ID) persists in `localStorage` under key `ov_cfg`. Saved pick IDs persist under `saved_picks`.

## UI Structure (tracker.html)

Standalone page reading from its own GAS Sheet URL. Renders KPIs, SVG ROI curve, breakdown cards per filter group, daily timeline, and a picks table. Uses the same JSONP pattern. Falls back to hardcoded sample data if the Sheet doesn't respond.

## Python Pipeline Commands

```bash
# Train Dixon-Coles ratings (requires data/historical/matches.csv)
python -m models.train_dc

# Calibrate + validate (LOEO-CV, saves data/calibrator.json + backtesting/reports/)
python -m backtesting.run_calibration

# Walk-forward backtest only
python -m backtesting.run_walkforward

# Run tests
pytest tests/ -v --tb=short

# Run a single test file
pytest tests/pipeline/test_devig.py -v

# Download/update historical data (synthetic fallback for testing)
python -m pipeline.historical --synthetic
```

## Python File Structure

```
models/
  math/
    devig.py          — metodo_multiplicativo, metodo_shin, devig()
    poisson.py        — fit_dixon_coles_fast, prob_over25_poisson, prob_over25_from_model,
                        build_dc_grid, extract_btts_over25_prob
  train_dc.py         — CLI to train DC per league → data/dc_ratings.json
backtesting/
  run_walkforward.py          — OOS predictions with calibrator_fn + season filter
  run_calibration.py          — FASE 4: LOEO-CV → calibrator.json + validation report
  run_sharp1x2_signal.py      — Q1–Q6 analysis: div threshold, walk-forward, N1 anomaly
  run_btts_over25_backtest.py — Walk-forward BTTS+O2.5 (--fast: in-sample via dc_ratings.json)
  send_sharp1x2_weekly.py     — Weekly TG report (CLV rolling-30, HOME/AWAY, DRAW N1 progress)
  reports/                    — calibration_validation.md, sharp1x2_signal.md, btts_over25_backtest.md
pipeline/
  scan_over25.py      — Over 2.5 scan + BTTS+Over 2.5 scan (30 min cron)
  scan_sharp1x2.py    — Sharp 1X2 scan (30 min cron)
  transform.py        — compute_final_probability, compute_final_probability_dc
  config.py           — MODEL_WEIGHT=0.30
data/
  dc_ratings.json          — fitted DC parameters per league (auto-updated Mondays)
  calibrator.json          — isotonic calibrator (auto-updated Mondays)
  picks.json               — Over 2.5 picks (auto-scan)
  picks_btts_over25.json   — BTTS+Over 2.5 picks (auto-scan)
  historical/              — matches.csv (auto-updated Mondays via historical_data.yml)
.github/workflows/
  scanner.yml           — a cada 30 min: Over 2.5 + Sharp 1X2 scan
  historical_data.yml   — seg 06:00 UTC: update matches.csv
  retrain_dc.yml        — seg 07:00 UTC: retrain DC + recalibrate + Sharp 1X2 TG report
  deploy_version.yml    — cada push main: version.json + BUILD_SHA [skip ci]
  data_quality.yml      — diário 07:00 UTC: schema validation + backtests
  sharp1x2_analysis.yml — workflow_dispatch: Q1–Q6 analysis report
  probe_bsd_markets.yml — workflow_dispatch: diagnóstico markets BSD API
```

## Data Rules

**NUNCA substituir dados reais por sintéticos para fazer um pipeline 'passar'.** Dados sintéticos são permitidos apenas em testes unitários (`tests/`), sempre claramente marcados.

## BTTS+Over 2.5 — Componentes

| Componente | Ficheiro | Estado |
|---|---|---|
| Grid bivariada DC + p_conjunta | `models/math/poisson.py` — `build_dc_grid`, `extract_btts_over25_prob` | ✅ produção |
| Scan automático + gate CLV≥5% | `pipeline/scan_over25.py` | ✅ activo |
| De-vig BTTS (BSD market=btts) | `scan_over25.py` — `_compute_btts_over25()` | ✅ devig ou fallback 5% |
| Picks guardados | `data/picks_btts_over25.json` — id `{ev_id}_btts` | ✅ auto-scan |
| Dashboard sub-tab | `index.html` → `dtab-btts` / `dpanel-btts` | ✅ CLV rolling-30 visível |
| TG alertas | `scan_over25.py` — `send_telegram(...)` | ✅ activo (CLV ≥ 5%) |
| Backtest walk-forward | `backtesting/run_btts_over25_backtest.py` | ✅ `--fast` (in-sample); OOS pendente |
| CLV real de mercado | BSD `market=btts` outcome=yes/no | ✅ disponível e integrado |

## Language

The UI is in **Portuguese (pt-PT)**. All user-facing strings, variable names like `casa`/`fora`/`liga`, and field names in the GAS schema use Portuguese. Keep this convention when adding new UI text or data fields.

Python code uses English variable names, Portuguese comments where needed.
