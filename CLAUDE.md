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

This is a static, no-build sports betting scanner for Over 2.5 goals markets. It consists of two standalone HTML files — no framework, no bundler, no package manager.

- **`index.html`** — Main app ("Over 2.5 SCOUT"): scanner, Sharp 1X2 signals, live monitor, dashboard (~1967 lines)
- **`tracker.html`** — Standalone analytics monitor ("Over 2.5 MONITOR") with ROI curve and breakdown by filter (~555 lines)

Open either file directly in a browser to run. There are no build steps, no tests, and no linters configured.

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

## Language

The UI is in **Portuguese (pt-PT)**. All user-facing strings, variable names like `casa`/`fora`/`liga`, and field names in the GAS schema use Portuguese. Keep this convention when adding new UI text or data fields.
