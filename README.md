# Over 2.5 Scout — Sistema de Apostas em Futebol

📋 Documentação interna e índice de portfólio mantidos em repositório privado.

[![Scanner](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/scanner.yml/badge.svg)](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/scanner.yml)
[![Sharp 1X2](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/sharp1x2_analysis.yml/badge.svg)](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/sharp1x2_analysis.yml)
[![Data Quality](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/data_quality.yml/badge.svg)](https://github.com/nunovinhas-creator/over25-scanner/actions/workflows/data_quality.yml)
[![Python](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/)
[![Licença](https://img.shields.io/badge/licen%C3%A7a-privado-lightgrey.svg)]()

Sistema automatizado de identificação de valor em mercados de apostas de futebol. Três módulos em produção, todos em **modo observação** — sem apostas reais até os gates de CLV serem atingidos.

---

## Estado Actual

<!-- DYNAMIC_STATUS_START -->
| Módulo | Picks válidos | Settled | CLV rolling-30 | Gate | Estado |
|---|---|---|---|---|---|
| Over 2.5 | 8 | — | — | CLV>+1% n≥300 | OBSERVAÇÃO |
| Sharp 1X2 | 1 | 1 | — | CLV>+1% n≥200 | OBSERVAÇÃO |
| BTTS+O2.5 | — | — | — | CLV>+5% n≥100 | OBSERVAÇÃO |

_Actualizado: 2026-08-10 14:41 UTC_
<!-- DYNAMIC_STATUS_END -->

**Observação efectiva:** Over 2.5 e Sharp 1X2 desde 17 Jun 2026 · BTTS+O2.5 desde 21 Jun 2026

---

## Arquitectura

```
                    BSD Sports API
                         │
              ┌──────────┴──────────┐
              │  GitHub Actions     │
              │  (a cada 30 min)    │
              └──────────┬──────────┘
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
  │  Over 2.5   │ │  Sharp 1X2  │ │  BTTS+O2.5  │
  │  Scanner    │ │  Scanner    │ │  Scanner    │
  │             │ │             │ │             │
  │ DC+isotonic │ │ div_b365_pin│ │ DC bivariate│
  │ EV ≥ 3%     │ │ div > 3%    │ │ CLV ≥ 5%    │
  └──────┬──────┘ └──────┬──────┘ └──────┬──────┘
         │               │               │
         ▼               ▼               ▼
   picks.json    picks_1x2.json  picks_btts_over25.json
         │               │               │
         └───────────────┼───────────────┘
                         ▼
                ┌────────────────┐
                │  CLV Tracker   │
                │  rolling-30    │
                │  gate check    │
                └───────┬────────┘
                        │ gate atingido?
                        ▼
                 Telegram Alert
                 (TG chat 1352687611)
```

**Módulos:**

| Módulo | Sinal | Gate |
|---|---|---|
| **Over 2.5** | Dixon-Coles + calibração isotónica + blend mercado (`MODEL_WEIGHT=0.30`) | EV ≥ 3%, liga whitelisted, não-DRIFTING, timing < 6h KO |
| **Sharp 1X2** | Divergência Bet365/Pinnacle (`div_b365_pin > 3%`) | HOME/AWAY apenas, timing 0–6h KO |
| **BTTS+O2.5** | Grid bivariada DC — `p_dc_conjunta = P(BTTS AND O2.5)` | `clv_btts_over25 ≥ 5%` + EV over25 ≥ 3% |

> Jogos de ligas whitelisted fora da janela 0–6h antes do kick-off são rejeitados com `reject_reason="timing_apos_6h"` (ambos os scanners) — é o gate de timing a funcionar como esperado, não uma falha do scanner.

### Frontend — leitura local, não BSD directa (desde PR #149)

Os separadores **Scanner**, **Sharp 1X2** e **Dashboard** leem `data/picks.json`, `data/picks_1x2.json` e `data/picks_btts_over25.json` do próprio repositório (mesma origem do GitHub Pages, sem CORS) em vez de chamar a BSD API a partir do browser. Isto é uma decisão arquitectural, não um workaround temporário: diagnóstico confirmado a 10/08/2026 mostrou que a BSD não envia `Access-Control-Allow-Origin` para a origem do GitHub Pages — nenhuma chamada do browser à BSD funciona, com ou sem `Authorization`. Os GitHub Actions não são afectados e continuam a escrever esses três ficheiros a cada 30 min (`scanner.yml`).

O separador **Live** é a excepção: continua a chamar a BSD directamente a partir do browser (`loadLive()`) e está sujeito à mesma limitação de CORS.

### Scanner LIVE (`pipeline/scan_live.py`)

Corre server-side via GitHub Actions (`live_scanner.yml`, loop ao minuto), independente do browser e da whitelist de 10 ligas. Dois fluxos partilham a mesma detecção/scoring por ciclo:

- **👁 Observações** — activo: gate (liga whitelisted + patternScore≥6 + probLive≥25% + não tardio sem golos) → `data/observations.json` (dedup persistente por `event_id`) → Telegram.
- **🔥 APOSTAR AGORA** — desactivado em produção desde 9 Ago 2026 (`LIVE_ALERTS_ENABLED=False`, pedido do autor). Detecção/scoring continuam a correr (alimentam a tab Live, só leitura); apenas o envio Telegram está bloqueado.

Filtros de envio do alerta "🔥 APOSTAR AGORA" (`ALERT_FILTERS`, calibrados sobre uma amostra de n=17 — PR #136; só se aplicam a este alerta, hoje desactivado):

| Filtro | Regra | Efeito |
|---|---|---|
| Zona morta xG | 1.0 ≤ xG < 1.5 | Bloqueia envio (pior banda de conversão na amostra) |
| Minuto tardio | minuto ≥ 85 | Bloqueia envio (tempo estrutural insuficiente) |
| Tier alta convicção | xG ≥ 2.5 | Marca a mensagem com ⭐, não bloqueia |

Correcções recentes:
- **PR #134** — dedup do alerta TG corrigido: um jogo só é marcado como `alertado` depois do envio Telegram confirmado (gate Pressão≥90 E Score≥20), não ao atingir o limiar mais brando de detecção interna (score≥12). Antes, uma qualificação prematura ou uma falha de envio bloqueava o jogo para o resto do ciclo (~5h50) sem nunca ser reavaliado.
- **PR #135** — janela de fetch de `odds_fecho` pós-KO (Sharp 1X2) passou a contar a partir do settlement (`settled_at`), não do kick-off. Antes, `update_closing_odds.py` só tentava até 24h pós-KO, mas `settle_sharp1x2.py` pode legitimamente demorar até 48h a definir o resultado — qualquer settlement fora dessa janela nunca chegava a ser tentado. Janela actual: 15min–12h pós-settlement.

---

## Stack Técnica

| Componente | Tecnologia |
|---|---|
| Modelo estatístico | Dixon-Coles (por liga, retrain semanal) |
| Calibração | Regressão isotónica LOEO-CV (`calibrator.json`) |
| Blend modelo/mercado | `p_final = 0.30 × p_model + 0.70 × p_market` |
| De-vig | Método multiplicativo + Shin |
| Dados ao vivo | BSD Sports API (`sports.bzzoiro.com`) |
| Dados históricos | football-data.co.uk (épocas 2122–2526) |
| Automação | GitHub Actions (cron 30min, retrain segunda-feira) |
| Alertas | Telegram Bot API |
| Frontend | HTML/JS estático (`index.html`, `tracker.html`) |
| Testes | pytest + validação de schema Pandera |

---

## Ligas Suportadas (Whitelist BSD)

| ID BSD | Liga | País |
|---|---|---|
| 1 | Premier League | Inglaterra |
| 2 | Primeira Liga | Portugal |
| 3 | La Liga | Espanha |
| 4 | Serie A | Itália |
| 5 | Bundesliga | Alemanha |
| 6 | Ligue 1 | França |
| 10 | Eredivisie | Holanda |
| 12 | Championship | Inglaterra |
| 14 | Belgian Pro League | Bélgica |
| 38 | La Liga 2 | Espanha |

> Bundesliga 2 e Serie B: presentes no histórico para backtesting, ausentes da BSD API — nunca geram picks em produção.

> `BSD_LEAGUE_ID_MAP` (`pipeline/scan_common.py`) é a única fonte de verdade dos IDs de liga — está espelhado no `const BSD_LEAGUE_ID_MAP` de `index.html` e os dois têm de mudar sempre juntos. Validado contra a BSD API a 10/08/2026 via o workflow **Fetch BSD Leagues (one-shot)**; essa revalidação deve repetir-se a cada viragem de época, já que a BSD pode alterar IDs. O mapa morto e desalinhado `CONFIG.LEAGUE_NAMES` (`js/config.js`) foi removido (PR #151).

---

## Estratégias — Backtesting Over 2.5

Resultados walk-forward out-of-sample (épocas 2122–2526). Estratégias definidas em `backtesting/strategies.py`.

| Estratégia | n apostas | WR% | ROI% | MaxDD | Sharpe |
|---|---|---|---|---|---|
| `high_score` | 7 | 71.4% | +27.89% | 10 | 0.313 |
| `shortsharp` | 10 | 60.0% | +9.93% | 20 | 0.104 |
| `sharp_only` | 10 | 60.0% | +9.93% | 20 | 0.104 |
| `shortening_only` | 14 | 57.1% | +2.62% | 30 | 0.028 |
| `baseline` | 35 | 51.4% | -6.27% | 80 | -0.067 |

> **Validação FASE 4 (época 2526):** Brier calibrado=0.24168 vs mercado=0.24320 · CLV IC 95% [−0.985%, +1.366%] (N=83)

**Sharp 1X2 (histórico football-data.co.uk):** 21.087 jogos · ROI +2.46% em 3.731 apostas (threshold div>3%)

---

## Estratégias 1X2 — legacy, sem evidência válida

> Investigação de 10 ago 2026. Detalhe completo e reabertura do assunto em
> [`docs/SHARP1X2_LEGACY_EVIDENCE.md`](docs/SHARP1X2_LEGACY_EVIDENCE.md).

| Estratégia (legacy) | Amostra citada | Estado |
|---|---|---|
| 1X2 WATCH+HOME | 245 de 352 · +44% ROI · WR 82% | **SEM EVIDÊNCIA VÁLIDA** |
| 1X2 HOME | +31% ROI · WR 76% | **SEM EVIDÊNCIA VÁLIDA** |
| (+ 2 outras variantes 1X2 do mesmo dashboard) | mesma proveniência de dados | **SEM EVIDÊNCIA VÁLIDA** |

Os números não foram apagados — pertencem ao sistema legacy (auto-log client-side
`autoLogSharp1x2()`, que parou de correr sozinho a 20/06/2026) e ficam registados
como tal. Todos os 352 registos de `data/picks_1x2.json` vêm desse auto-log; o
pipeline actual (`pipeline/scan_sharp1x2.py`) nunca escreveu nenhum pick neste
ficheiro. 97% dos registos (341/352) não têm o campo `liga` gravado
(`data_quality_flag="pre_bugfix_liga_vazia"`), e ao correr os gates actuais contra
os 352 registos, 0 passam (0/245 no subconjunto WATCH+HOME).

**As estratégias Over 2.5 acima não são afectadas** — vêm de `data/picks.json`,
escrito pelo pipeline actual (`pipeline/scan_over25.py`), fonte de dados diferente
da usada pelas estratégias 1X2 legacy.

---

## Ciclos de Revisão

| Checkpoint | Data | Critério |
|---|---|---|
| **C3** | 30 Jun 2026 | Workflows activos + primeiros CLV com n suficiente |
| **C4** | 15 Jul 2026 | Primeira leitura estatística (n≈200 Over 2.5, n≈50 Sharp, n≈50 BTTS) |
| **C5** | 31 Jul 2026 | Decisão de agosto: apostar ou manter MODO OBSERVAÇÃO |

> Os três checkpoints já passaram no calendário (hoje: 10 Ago 2026). Não há registo de uma decisão explícita de activação — o sistema mantém-se em MODO OBSERVAÇÃO porque nenhum módulo atingiu ainda o `n` settled exigido pelos gates abaixo (ver *Estado Actual* no topo deste README), pelo que a regra "não iniciar apostas reais sem passar pelo checkpoint" continua a aplicar-se por defeito.

Gates de activação para apostas reais:

```
Over 2.5:  CLV rolling-30 > +1%  e  n ≥ 300 settled
Sharp 1X2: CLV rolling-30 > +1%  e  n ≥ 200 settled
BTTS+O2.5: CLV rolling-30 > +5%  e  n ≥ 100 settled
```

---

## Documentação (auditoria jul 2026)

| Documento | Conteúdo |
|---|---|
| [`docs/PRODUCT_THESIS.md`](docs/PRODUCT_THESIS.md) | Tese canónica do produto + teses rejeitadas |
| [`docs/PRODUCT_SCOPE.md`](docs/PRODUCT_SCOPE.md) | O que está dentro e fora de scope, com justificação |
| [`docs/MODEL_REVIEW.md`](docs/MODEL_REVIEW.md) | Revisão quantitativa: DC, de-vig, calibração, EV, CLV |
| [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md) | Revisão de arquitectura + problemas corrigidos |
| [`docs/REPOSITORY_INVENTORY.md`](docs/REPOSITORY_INVENTORY.md) | Inventário KEEP/REFACTOR/DEPRECATE/REMOVE |
| [`docs/KILL_LEDGER.md`](docs/KILL_LEDGER.md) | Registo de remoções com justificação |
| [`docs/MARKET_POSITION.md`](docs/MARKET_POSITION.md) | Comparação com alternativas open-source e comerciais |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | Roadmap até C5 e época 2627 |
| [`docs/RELEASE.md`](docs/RELEASE.md) | Versão 2.1.0, migração, dívida técnica e científica |

---

## Comando `/scan`

Orquestra 4 subagentes em sequência via Claude Code:

```
/scan
```

| Passo | Subagente | O que faz |
|---|---|---|
| 1 | `data-fetcher` | Fetch eventos + odds (over/under, BTTS, 1X2) da BSD API para hoje |
| 2 | `model-runner` | Aplica DC + calibrador isotónico → `p_final`, `ev_final` por evento |
| 3 | `clv-tracker` | Lê picks dos 3 módulos, exclui `data_quality_flag`, calcula CLV rolling-30 |
| 4 | `telegram-notifier` | Envia alerta TG **apenas se** gate de activação for atingido |

---

## Workflows Automáticos

| Workflow | Trigger | Função |
|---|---|---|
| `scanner.yml` | cron 30min | Over 2.5 scan + Sharp 1X2 scan |
| `sharp1x2_analysis.yml` | cron 30min + dispatch | Update closing odds + análise de sinal |
| `retrain_dc.yml` | seg 07:00 UTC | Re-treina DC + calibrador + relatório TG |
| `historical_data.yml` | seg 06:00 UTC | Actualiza `matches.csv` (football-data.co.uk) |
| `data_quality.yml` | diário 07:00 UTC | Schema validation + backtests automáticos |
| `probe_bsd_closing_odds.yml` | dispatch | Diagnóstico odds pós-KO na BSD API |
| `deploy_version.yml` | push main | Actualiza `version.json` + `BUILD_SHA` |

---

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/nunovinhas-creator/over25-scanner.git
cd over25-scanner

# Instalar dependências Python
pip install -r requirements.txt

# Treinar modelo (requer data/historical/matches.csv)
python -m models.train_dc

# Calibrar (LOEO-CV)
python -m backtesting.run_calibration

# Correr testes
pytest tests/ -v --tb=short
```

### Secrets necessários (GitHub Actions)

| Secret | Descrição |
|---|---|
| `BSD_API_KEY` | Chave da BSD Sports API (`sports.bzzoiro.com`) |
| `TG_TOKEN` | Token do bot Telegram para alertas |
| `TG_CHAT_ID` | Chat ID Telegram (default: `1352687611`) |

### Frontend

Abrir `index.html` directamente no browser — sem build steps.
Os separadores Scanner, Sharp 1X2 e Dashboard leem `data/picks*.json` do próprio repositório e não precisam de chave. Só o separador Live chama a BSD directamente e precisa da `BSD_API_KEY` (introduzida na interface → guardada em `localStorage`).

---

## Estrutura do Projecto

```
over25-scanner/
├── index.html                  # Over 2.5 SCOUT (scanner + Sharp 1X2 + Live + Dashboard)
├── tracker.html                # Over 2.5 MONITOR (ROI curve, picks table)
├── pipeline/
│   ├── scan_over25.py          # Over 2.5 + BTTS scan (30min cron)
│   ├── scan_sharp1x2.py        # Sharp 1X2 scan + fetch_closing_odds()
│   ├── update_closing_odds.py  # Preenche odds_fecho pós-KO + calcula CLV
│   ├── etl.py / extract.py     # BSD API ETL layer
│   └── config.py               # MODEL_WEIGHT=0.30, whitelist
├── models/
│   ├── math/
│   │   ├── poisson.py          # Dixon-Coles, grid bivariada BTTS+O2.5
│   │   ├── devig.py            # Método multiplicativo + Shin
│   │   └── calibration.py      # Platt, isotónica, temperature scaling
│   ├── metrics/
│   │   └── roi_metrics.py      # ROI, CLV rolling, Sharpe, clv_analysis_1x2()
│   └── train_dc.py             # CLI: treina DC por liga → dc_ratings.json
├── backtesting/
│   ├── engine.py               # Walk-forward determinístico
│   ├── run_calibration.py      # LOEO-CV → calibrator.json
│   └── reports/                # Relatórios markdown + txt por estratégia
├── data/
│   ├── picks.json              # Over 2.5 picks (auto-scan)
│   ├── picks_1x2.json          # Sharp 1X2 picks (auto-scan)
│   ├── picks_btts_over25.json  # BTTS+O2.5 picks (auto-scan)
│   ├── dc_ratings.json         # Parâmetros DC por liga
│   ├── calibrator.json         # Calibrador isotónico
│   └── historical/             # matches.csv (football-data.co.uk)
├── scripts/
│   ├── generate_readme.py      # Regenera secções dinâmicas do README
│   └── probe_bsd_*.py          # Diagnósticos da BSD API
└── .github/workflows/          # 8 workflows automáticos
```

---

> **Aviso:** Este projecto é de uso privado. Em modo observação — nenhuma aposta real activa.
