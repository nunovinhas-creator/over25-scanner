# Inventário do Repositório — auditoria QuantCode (jul 2026)

Classificação: **KEEP** (suporta a tese tal como está) · **REFACTOR** (suporta a tese mas precisa de intervenção) · **DEPRECATE** (mantido por decisão de governança, fora de produção) · **REMOVE** (removido — justificação em `KILL_LEDGER.md`).

Tese de referência: [`PRODUCT_THESIS.md`](PRODUCT_THESIS.md) — laboratório de validação de sinais disciplinado por CLV.

## Pipeline Python (produção)

| Componente | Classificação | Justificação |
|---|---|---|
| `pipeline/scan_over25.py` | **REFACTOR** | Núcleo do módulo 1 e 3 (Over 2.5 + BTTS+O2.5). Corre a cada 30 min. Problemas: constantes e helpers duplicados com `scan_sharp1x2.py`; default `movement="SHORTENING"` fail-open regista dados inventados (corrigido nesta auditoria). |
| `pipeline/scan_sharp1x2.py` | **REFACTOR** | Núcleo do módulo 2. Bug real corrigido: alerta TG dizia sempre `Outcome: HOME` (linha `_build_msg`). Duplicação extraída para `pipeline/scan_common.py`. |
| `pipeline/scan_common.py` | **KEEP** (novo) | Criado nesta auditoria: WHITELIST, `BSD_LEAGUE_ID_MAP`, Telegram, git commit/push, I/O JSON e lineups partilhados pelos dois scanners — uma única fonte de verdade. |
| `pipeline/transform.py` | **REFACTOR** | `compute_final_probability_dc` é o coração do módulo 1 — KEEP. Mas metade do ficheiro (`enrich_picks`, `create_feature_matrix`, `compute_form_features`, `add_calibrated_prob`) serve o caminho ETL/GAS legado, não a produção. Manter por ora (usado em backtests), consolidar quando o ETL legado for removido. |
| `pipeline/extract.py` | **KEEP** | Cliente BSD fail-safe + lineups + football-data.co.uk. Usado pelos scanners (lineups) e pelo histórico. |
| `pipeline/historical.py` | **KEEP** | Alimenta `matches.csv` semanalmente — sem histórico não há Dixon-Coles. Fallback sintético claramente marcado e restrito a testes. |
| `pipeline/etl.py` | **DEPRECATE** | Orquestração extract→transform→load do desenho original (GAS-centrado). Produção usa os scanners directamente. Só os filtros (`filter_*`) têm uso vivo (testes + `data_quality.yml --validate`). Candidato a redução na próxima época; não remover já porque `data_quality.yml` invoca `pipeline/etl.py --validate`. |
| `pipeline/config.py` | **REFACTOR** | Boa ideia (Config única) mas a produção não a usa — os scanners hardcodam MODEL_WEIGHT/MIN_EV/whitelist. Risco de drift documentado em `ARCHITECTURE_REVIEW.md`. Convergência é item de roadmap. |
| `pipeline/update_closing_odds.py` | **KEEP** | Único caminho para `odds_fecho` real (REST pós-KO) — pré-requisito para remover o xfail do CLV exacto Sharp 1X2. |

## Modelos e métricas

| Componente | Classificação | Justificação |
|---|---|---|
| `models/math/poisson.py` | **KEEP** | Dixon-Coles (lento + rápido), grelha bivariada, BTTS+O2.5. Matematicamente correcto (ver `MODEL_REVIEW.md`). É o único modelo em produção. |
| `models/math/devig.py` | **KEEP** | Multiplicativo + Shin (1992) correctos, com referências. Base de todo o p_market. |
| `models/math/calibration.py` | **REFACTOR** | Isotónica (produção) correcta. Platt/Beta/Temperature/Ensemble só usados na selecção LOEO — manter porque a comparação de métodos é parte do protocolo de calibração; `calibrate_from_picks` (caminho GAS) é legado. |
| `models/math/skellam.py` | **KEEP** | Não está em produção, mas é item de backlog pré-registado (2º sinal 1X2, ver `cycles.md`). |
| `models/math/kelly.py` | **DEPRECATE** | Kelly DESACTIVADO por decisão permanente com critério de revisão explícito — mantém-se como implementação de referência para quando o gate CLV for atingido. Não importar em produção. |
| `models/math/elo.py` | **REMOVE** | Experimental, zero importadores, zero testes, fora de todos os planos. Ver `KILL_LEDGER.md`. |
| `models/metrics/roi_metrics.py` | **KEEP** | CLV rolling-30 é a métrica de decisão do produto. |
| `models/metrics/brier_score.py`, `ece.py` | **KEEP** | Métricas de calibração usadas na FASE 4 e nos relatórios. |
| `models/train_dc.py` | **KEEP** | CLI de treino semanal → `dc_ratings.json`. |

## Backtesting

| Componente | Classificação | Justificação |
|---|---|---|
| `backtesting/engine.py` | **REFACTOR** | Motor determinístico sólido; duplica `kelly_full` de `models/math/kelly.py` e conta picks não-settled como LOSS se o caller não filtrar (documentado em `MODEL_REVIEW.md`). |
| `backtesting/run_walkforward.py`, `run_calibration.py` | **KEEP** | LOEO-CV e walk-forward OOS são a espinha dorsal científica da tese. |
| `backtesting/run_backtest.py`, `strategies.py`, `report.py` | **KEEP** | Catálogo de estratégias com relatórios reprodutíveis. |
| `backtesting/run_sharp1x2_signal.py`, `run_btts_over25_backtest.py` | **KEEP** | Validação específica dos módulos 2 e 3. `--fast` in-sample está correctamente rotulado. |
| `backtesting/send_sharp1x2_weekly.py` | **KEEP** | Relatório TG semanal — telemetria do ciclo de decisão. |

## Front-end e delivery

| Componente | Classificação | Justificação |
|---|---|---|
| `index.html` (~7.6k linhas) | **REFACTOR** | Cockpit operacional (scanner, sharp, live, dashboard). Funciona e é a UI mobile do operador, mas é um monólito de 320 KB com estado global; extração incremental para `js/` já começou. Não reescrever antes de C5 — risco > benefício. |
| `tracker.html` | **KEEP** | Monitor ROI/picks independente com GAS Sheet dedicada. |
| `js/*` (8 módulos) | **KEEP** | Todos carregados por `index.html` (verificado). |
| `dashboard/generate_dashboard.py` + `analytics.html` | **KEEP** | Telemetria analítica gerada por workflow. |
| `assets/` | **KEEP** | Estáticos da UI. |

## Automação (GitHub Actions)

| Workflow | Classificação | Justificação |
|---|---|---|
| `scanner.yml` (30 min) | **KEEP** | O coração do produto. |
| `historical_data.yml`, `retrain_dc.yml` (semanais) | **KEEP** | Ciclo de re-treino DC + calibrador. |
| `data_quality.yml` (diário) | **KEEP** | Guarda-costas da integridade dos dados — central à tese. |
| `deploy_version.yml`, `update_readme.yml` | **KEEP** | Versionamento do front-end + status dinâmico no README. |
| `dashboard.yml`, `sharp1x2_analysis.yml` | **KEEP** | Relatórios on-demand. |
| `probe_bsd_markets.yml`, `probe_bsd_closing_odds.yml`, `fetch_bsd_leagues.yml` | **KEEP** | Diagnóstico da BSD API (workflow_dispatch, custo zero em repouso). |
| `gov-eval.yml` | **KEEP** | Teste dos cenários de governança. |

## Dados

| Ficheiro | Classificação | Notas |
|---|---|---|
| `data/picks*.json`, `rejected_*`, `scan_state_*`, `observations.json` | **KEEP** | Registo imutável da experiência. **Atenção:** `rejected_picks.json` cresce sem limite (1.8 MB, 4.791 entradas) — item de roadmap: rotação/retention. |
| `data/dc_ratings.json`, `calibrator.json` | **KEEP** | Artefactos de modelo re-gerados às segundas. |
| `data/historical/matches.{csv,parquet}` | **KEEP** | Base de treino. |
| `data/schema/*.py` | **KEEP** | Validação de schema usada por `data_quality.yml`. |

## Raiz e configuração

| Item | Classificação | Notas |
|---|---|---|
| Ficheiros `=0.17.0`, `=1.0.0`, `=1.11.0`, `=1.24.0`, `=1.3.0`, `=2.0.0`, `=2.31.0`, `=5.17.0`, `=7.4.0` | **REMOVE** | Lixo de `pip install pkg >=x.y.z` sem aspas. Ver `KILL_LEDGER.md`. |
| `requirements.txt` | **KEEP** | Limpo e comentado. |
| `README.md` | **KEEP** | Status dinâmico auto-gerado; alinhado com a tese. |
| `CLAUDE.md`, `.claude/rules/*`, `.claude/agents/*` | **KEEP** | Governança operacional — é o mecanismo que faz cumprir a tese. Nota: `.claude/rules/testing.md` descreve uma estrutura `tests/models/` que não existe (corrigido nesta auditoria — ver `KILL_LEDGER.md`). |
| `scripts/generate_readme.py`, `probe_*`, `fetch_bsd_leagues.py` | **KEEP** | Suporte a workflows. |

## Testes

| Item | Classificação | Notas |
|---|---|---|
| `tests/` (10 ficheiros, ~2.100 linhas) | **KEEP** | Cobrem gates, devig, calibração, schema, scanners com mocks. Estrutura real difere da documentada em `testing.md` (doc corrigida). |
