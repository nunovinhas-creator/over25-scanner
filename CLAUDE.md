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

Alterações a `main` passam sempre por branch + PR. Nunca simular estados de verificação; nenhum estado de erro pode parecer sucesso.

O Stop hook (`/home/user/over25-scanner/.claude/auto-push.sh`) trata do commit+push do branch de trabalho para o que ficar por commitar no fim da sessão. Abrir e fazer merge do PR para `main` exige CI verde e é uma decisão explícita — nunca automática nem silenciosa.

### Âmbito de sessão

- **Um bloco = um branch = um PR.** Não misturar dois blocos de trabalho no mesmo branch/PR.
- **Sem refactors não pedidos** — um bloco resolve o que foi pedido, nada mais (sem limpeza, sem abstracções, sem "já agora também...").
- **Sem merge sem aprovação expressa do Nuno** — a sessão abre o PR e pára; o merge para `main` é sempre uma decisão dele, feita à parte.

### Zona intocável (sem autorização expressa do Nuno)

Estes ficheiros correm em produção 100% autónoma (detecção live, 👁 OBSERVAÇÕES, alertas TG e health check — ver Overview) e não devem ser alterados sem pedido explícito, mesmo dentro do âmbito de um bloco aprovado:

- `.github/workflows/live_scanner.yml`
- `pipeline/scan_live.py`
- `pipeline/scan_common.py::send_telegram`
- `data/observations.json`
- `data/live_scanner_health.json`

---

## Governança — Regras Modulares

As regras detalhadas estão em `.claude/rules/` (referência obrigatória antes de qualquer implementação):

| Ficheiro | Conteúdo |
|---|---|
| [`.claude/rules/backend.md`](.claude/rules/backend.md) | Pipeline Python, scan, modelos, backtest, comandos |
| [`.claude/rules/data.md`](.claude/rules/data.md) | Whitelist 10 ligas, data_quality_flag, picks.json |
| [`.claude/rules/testing.md`](.claude/rules/testing.md) | Regras pytest, anti-leakage, xfail, validação FASE 4 |
| [`.claude/rules/decisions.md`](.claude/rules/decisions.md) | Kelly off, DRAW suspenso, odds cap rejeitado, whitelist |
| [`.claude/rules/cycles.md`](.claude/rules/cycles.md) | Checkpoints C3–C5, gates CLV, backlog técnico |

**Gov Eval:** `.claude/gov-eval/scenarios.md` — 5 cenários de teste de governança (workflow: `gov-eval.yml`).

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
- Scanner LIVE (`pipeline/scan_live.py`) — loop ao minuto, alertas TG independentes da whitelist; desde a sessão live-scanner-backend-autonomous corre também 100% autónomo as 👁 OBSERVAÇÕES (detecção → `data/observations.json` → Telegram), sem depender do browser (`index.html`) estar aberto

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

**Campos informativos por pick (desde 11 jul 2026 — NÃO são gates):**
- `h2h_matches`, `h2h_avg_goals` — H2H agregado embutido no evento BSD (zero chamadas extra)
- `prob_over25_ml`, `prob_btts_ml` — prediction CatBoost da BSD (`/api/v2/predictions/`), para comparação de Brier futura
- `lineup_status`, `indisp_casa`, `indisp_fora`, `indisp_casa_det`, `indisp_fora_det` — indisponíveis (lesões/suspensões) via `/api/v2/events/{id}/lineups/`, 1 chamada por pick novo; incluídos no alerta TG Over 2.5 (`🚑 Indisponíveis`)

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

Bundesliga 2 e Serie B: ausentes da BSD (65 ligas disponíveis) — presentes no histórico football-data.co.uk para backtesting, nunca geram picks em produção.

**`BSD_LEAGUE_ID_MAP` é a única fonte de verdade dos IDs de liga.** Existe em duas cópias que têm de mudar sempre juntas — `pipeline/scan_common.py` (Python) e `const BSD_LEAGUE_ID_MAP` inline em `index.html` (JS) — nunca só uma. Fail-closed: `league_id` desconhecido/irresolúvel → sentinela `UNKNOWN_LEAGUE` ("DESCONHECIDA", nunca `''`) → whitelist rejeita com `reject_reason="liga_desconhecida"`.

`CONFIG.LEAGUE_NAMES` (`js/config.js`) foi **removido na PR #151** — era um segundo mapa `league_id → nome` com IDs contraditórios (ex.: `6: 'Liga Portugal'` vs o `6: 'Ligue 1'` real). Registo em `docs/DEAD-CODE.md`. **Não recriar um segundo mapa de ligas** — qualquer necessidade de mapear `league_id → nome` usa `BSD_LEAGUE_ID_MAP`.

---

## Infraestrutura Automática

| Workflow | Trigger | O que faz |
|---|---|---|
| `scanner.yml` | a cada 30 min | Over 2.5 scan + Sharp 1X2 scan + commit `data/picks*.json` |
| `live_scanner.yml` | 6×/dia (00:07/04:07/08:07/12:07/16:07/20:07 UTC), loop interno ao minuto | Scanner LIVE (`pipeline/scan_live.py`) — detecção/scoring/padrões + 👁 OBSERVAÇÕES (Telegram + `data/observations.json`) 100% autónomos, sem browser; "🔥 APOSTAR AGORA" continua desactivado (`LIVE_ALERTS_ENABLED=False`) |
| `historical_data.yml` | seg 06:00 UTC | Actualiza `data/historical/matches.csv` (football-data.co.uk) |
| `retrain_dc.yml` | seg 07:00 UTC | Re-treina DC + calibrador + relatório TG Sharp 1X2 semanal |
| `deploy_version.yml` | cada push main | Actualiza `version.json` + `BUILD_SHA` em `index.html` [skip ci] |
| `dashboard.yml` | trigger | Gera dashboard HTML analítico |
| `data_quality.yml` | diário 07:00 UTC | Schema + data quality + backtests automáticos |
| `sharp1x2_analysis.yml` | workflow_dispatch | Q1–Q6 analysis report Sharp 1X2 |
| `probe_bsd_markets.yml` | workflow_dispatch | Diagnóstico markets disponíveis na BSD API |

**Secrets necessários:** `BSD_API_KEY`, `TG_TOKEN`, `TG_CHAT_ID`

---

## Decisões Permanentes

Ver detalhe completo em [`.claude/rules/decisions.md`](.claude/rules/decisions.md).

| Decisão | Estado |
|---|---|
| `MODEL_WEIGHT=0.30` | FIXO |
| Kelly staking | DESACTIVADO |
| Odds cap (`MAX_ODDS_OVER`) | REJEITADO |
| DRAW (todos os módulos) | SUSPENSO (excepção: DRAW N1 Eredivisie em tracking) |
| HOME N1 (Sharp 1X2) | BLOQUEADO |
| Bundesliga 2 / Serie B | FORA da whitelist BSD |
| `pin_drop` como sinal 1X2 | SUBSTITUÍDO por `div_b365_pin` |
| `previous_decimal_odds` | NÃO é closing line |

---

## Ciclos de Revisão e Gates de Activação

Ver detalhe completo em [`.claude/rules/cycles.md`](.claude/rules/cycles.md).

| Checkpoint | Data | O que verificar |
|---|---|---|
| C3 | 30 jun 2026 | Workflows activos + CLV rolling primeiros picks reais |
| C4 | 15 jul 2026 | Primeira leitura com peso estatístico |
| C5 | 31 jul 2026 | Decisão de agosto: apostar ou manter MODO OBSERVAÇÃO |

---

## External Dependencies

All logic is inline JavaScript. Three external services are used:

| Service | Purpose | Config |
|---|---|---|
| BSD Sports API (`https://sports.bzzoiro.com`) | Live tab (`loadLive()`) + on-demand per-game "🧠 Análise" (`reactAnalyzeGame()`) — **not** the bulk Scanner/Sharp 1X2/Dashboard load, see Data Flow below | API key entered at runtime, saved to `localStorage` |
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

**`loadAll()` (Scanner, Sharp 1X2, Dashboard) does not call the BSD API.** Since **PR
#149** (10 ago 2026) it reads `data/picks.json`, `data/picks_1x2.json` and
`data/picks_btts_over25.json` from the repo itself — same origin as GitHub Pages, no
CORS. Reason: the BSD server does not send `Access-Control-Allow-Origin` for the
GitHub Pages origin, so no browser call to BSD from these three tabs can ever succeed
(confirmed by diagnostic, with or without `Authorization`). `scanner.yml` writes those
files every 30 min via `pipeline/scan_over25.py` / `scan_sharp1x2.py`, which already
compute `p_final`/`ev_final`/EV gates server-side; `mapPickToGame()` and
`buildSharpFromPicks1x2()` just reshape those records for the UI, they don't fetch or
recompute scores. **A future session proposing to "fix" this BSD fetch in the browser
would be repeating work already diagnosed and deliberately routed around — don't.**

Two call sites still hit BSD directly from the browser, both out of that scope:
- **Live tab** (`loadLive()`) — needs the BSD key, auto-refreshes every 30s.
- **Per-game "🧠 Análise" modal** (`reactAnalyzeGame()`, triggered from a Scanner card)
  — an on-demand, single-event deep dive (detail/odds/predictions), independent of the
  30-min scan cycle.

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
MIN_VALID_ODDS = 1.01  // floor below which an odd is SUSPENDED, never a real price (issue #127)
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
    calibration.py    — Platt scaling, isotonic regression, temperature scaling
    skellam.py        — Skellam distribution (diferença de Poisson) para 1X2
    kelly.py          — Kelly criterion (DESACTIVADO em produção)
  metrics/
    brier_score.py    — Brier score + decomposição Murphy 1973, reliability diagram
    ece.py            — Expected Calibration Error (ECE) e métricas de calibração
    roi_metrics.py    — ROI, profit factor, drawdown, CLV rolling (unit staking)
  train_dc.py         — CLI to train DC per league → data/dc_ratings.json
backtesting/
  engine.py           — Backtester + BacktestConfig: walk-forward determinístico
  strategies.py       — Catálogo de estratégias e filter presets (baseline, sharp, etc.)
  report.py           — Geração de relatórios de backtest (texto e markdown)
  run_backtest.py     — CLI entrypoint para backtesting Over 2.5 (todas as estratégias)
  run_walkforward.py  — OOS predictions com calibrator_fn + season filter
  run_calibration.py  — FASE 4: LOEO-CV → calibrator.json + validation report
  run_sharp1x2_signal.py      — Q1–Q6 analysis: div threshold, walk-forward, N1 anomaly
  run_btts_over25_backtest.py — Walk-forward BTTS+O2.5 (--fast: in-sample via dc_ratings.json)
  send_sharp1x2_weekly.py     — Weekly TG report (CLV rolling-30, HOME/AWAY, DRAW N1 progress)
  reports/                    — calibration_validation.md, sharp1x2_signal.md,
                                btts_over25_backtest.md, walkforward.md, + .txt por estratégia
pipeline/
  scan_over25.py      — Over 2.5 scan + BTTS+Over 2.5 scan (30 min cron)
  scan_sharp1x2.py    — Sharp 1X2 scan (30 min cron)
  scan_live.py        — Scanner LIVE ao minuto: detectPatterns() (12 padrões), alertas TG "🔥 APOSTAR AGORA"
                        (LIVE_ALERTS_ENABLED=False, desactivado — só detecção/scoring correm),
                        independente da whitelist (não usa DC nem histórico por liga). Corre também
                        👁 OBSERVAÇÕES (autónomo, porta de autoLogObservations() do index.html): gate
                        (liga whitelisted + patternScore≥6 + probLive≥25% + não tardio sem golos) →
                        data/observations.json (dedup persistente por event_id, sobrevive a restart) →
                        Telegram. Health check em data/live_scanner_health.json
  scan_common.py      — whitelist, BSD_LEAGUE_ID_MAP, Telegram, git e I/O partilhados pelos scanners
  transform.py        — compute_final_probability, compute_final_probability_dc
  etl.py              — ETL orchestration: coordena extract → transform → load
  extract.py          — Data extraction da BSD API (fail-safe: lista vazia em erro)
  historical.py       — Download e normalização football-data.co.uk → matches.csv
  config.py           — MODEL_WEIGHT=0.30
data/
  dc_ratings.json          — fitted DC parameters per league (auto-updated Mondays)
  calibrator.json          — isotonic calibrator (auto-updated Mondays)
  picks.json               — Over 2.5 picks (auto-scan)
  picks_1x2.json           — Sharp 1X2 picks (auto-scan)
  picks_btts_over25.json   — BTTS+Over 2.5 picks (auto-scan)
  rejected_picks.json      — Over 2.5 rejeitados (para análise de gates)
  rejected_picks_1x2.json  — Sharp 1X2 rejeitados
  scan_state_over25.json   — estado do scan anterior (odds + movimento por event_id)
  observations.json        — 👁 observações live, geradas autonomamente por pipeline/scan_live.py
                              (também visível na tab Live do index.html, só leitura)
  live_scanner_health.json — health check do worker LIVE (running, last_scan_at,
                              live_games_found, observations_generated_total, errors)
  historical/              — matches.csv + matches.parquet (auto-updated Mondays)
  schema/
    bsd_schema.py    — BSD API event schema e validação
    picks_schema.py  — picks.json schema e validação
dashboard/
  generate_dashboard.py — gera dashboard/analytics.html (workflow dashboard.yml)
  analytics.html        — dashboard HTML analítico (auto-gerado)
scripts/
  probe_bsd_markets.py  — diagnóstico markets BSD API (workflow_dispatch)
  fetch_bsd_leagues.py  — lista ligas disponíveis na BSD API (workflow_dispatch)
js/                     — módulos JS auxiliares carregados por index.html
  api-client.js    — HTTP client com retry, timeout, deduplicação e cache 60s
  config.js        — constantes e endpoints centralizados
  state-manager.js — estado global com pub/sub e persistência automática
  storage.js / logger.js / error-handler.js / validators.js / github-api.js
.github/workflows/
  scanner.yml           — a cada 30 min: Over 2.5 + Sharp 1X2 scan
  historical_data.yml   — seg 06:00 UTC: update matches.csv
  retrain_dc.yml        — seg 07:00 UTC: retrain DC + recalibrate + Sharp 1X2 TG report
  deploy_version.yml    — cada push main: version.json + BUILD_SHA [skip ci]
  data_quality.yml      — diário 07:00 UTC: schema validation + backtests
  sharp1x2_analysis.yml — workflow_dispatch: Q1–Q6 analysis report
  probe_bsd_markets.yml — workflow_dispatch: diagnóstico markets BSD API
  fetch_bsd_leagues.yml — workflow_dispatch: lista ligas BSD disponíveis
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

---

## Subagentes — `.claude/agents/`

Quatro subagentes especializados, orquestrados pelo comando `/scan`:

| Agente | Ficheiro | Responsabilidade |
|---|---|---|
| `data-fetcher` | `.claude/agents/data-fetcher.md` | Fetch eventos + odds da BSD API via `pipeline/etl.py` / `scan_over25._fetch_all_events()` |
| `model-runner` | `.claude/agents/model-runner.md` | Dixon-Coles + calibrador isotónico → `p_final`, `ev_final` por evento |
| `clv-tracker` | `.claude/agents/clv-tracker.md` | CLV rolling-30, WR e ROI dos 3 módulos a partir de `data/picks*.json` |
| `telegram-notifier` | `.claude/agents/telegram-notifier.md` | Alerta TG quando gate de activação é atingido (CLV > threshold e n ≥ mínimo) |

**Comando `/scan`** (`.claude/commands/scan.md`) — orquestra os 4 agentes em sequência e apresenta tabela de estado dos 3 módulos.

---

## Estratégias e ROI (backtesting histórico)

Resultados disponíveis em `backtesting/reports/`. Estratégias definidas em `backtesting/strategies.py`:

| Estratégia | Ficheiro report | Critério de selecção |
|---|---|---|
| `baseline` | `baseline.txt` | Todos os picks com EV ≥ 3% |
| `shortening_only` | `shortening_only.txt` | + movimento SHORTENING |
| `sharp_only` | `sharp_only.txt` | + sinal Sharp confirmado |
| `shortsharp` | `shortsharp.txt` | SHORTENING e Sharp combinados |
| `high_xg` | `high_xg.txt` | + xG total > threshold |
| `high_score` | `high_score.txt` | + score composto ≥ 65/100 |
| `value_only` | `value_only.txt` | + odds_band seleccionada |
| `conservative` | `conservative.txt` | Critérios mais restritivos |
| `kelly_sizing` | `kelly_sizing.txt` | Kelly sizing (DESACTIVADO em produção) |
| Walk-forward OOS | `walkforward.md` | Validação out-of-sample temporal |
| Comparação | `comparison.md` | Todas as estratégias lado a lado |

Para correr todas as estratégias: `python -m backtesting.run_backtest`

---

## Regra Mobile (browser/mobile-only)

Este projecto é gerido **100% via browser ou mobile** — sem terminal local.

**Regras obrigatórias para cada sessão:**

1. **Ficheiros completos** — quando partilhares código para colar, dá sempre o ficheiro completo. Nunca diffs, nunca fragmentos parciais.
2. **Sessão termina com o PR aberto, não fechado** — o Stop hook (`auto-push.sh`) faz commit+push do branch, mas a sessão nunca faz merge do PR para `main`. O merge é sempre uma decisão do Nuno, feita à parte (ver "Âmbito de sessão" acima).
3. **Sem terminal** — se algo só for possível via terminal local, dizer explicitamente e propor alternativa (GitHub Actions, GitHub web editor, ou workflow_dispatch).
4. **Ciclos de revisão** — não iniciar apostas reais sem passar pelos checkpoints:
   - **C3 — 30 Jun 2026**: confirmar workflows + primeiros CLV com n suficiente
   - **C4 — 15 Jul 2026**: primeira leitura com peso estatístico por módulo
   - **C5 — 31 Jul 2026**: decisão de agosto (apostar ou manter MODO OBSERVAÇÃO)
