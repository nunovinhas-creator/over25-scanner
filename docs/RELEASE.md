# Release — auditoria QuantCode

## Versão recomendada: **2.1.0**

Convenção: o sistema em produção com 3 módulos e governança completa é tratado como a linha `2.x` (a era `1.x` foi o scanner GAS/heurístico original, hoje legado em `etl.py`).

- **MAJOR não:** nenhuma interface pública quebrou; os formatos de `picks*.json` mantêm-se; os workflows correm sem alteração.
- **MINOR sim:** novo módulo `pipeline/scan_common.py`; semântica nova do campo `movimento` (`UNKNOWN` quando a BSD não envia movement); remoção de `models/math/elo.py`.
- **PATCH incluído:** correcção do alerta Telegram Sharp 1X2 (outcome errado).

## Notas de release (2.1.0 — jul 2026)

### Corrigido
- **Alerta TG Sharp 1X2 identificava sempre `HOME`** como outcome, mesmo em picks AWAY (`scan_sharp1x2._build_msg`). Agora usa o outcome real do pick.
- **`movimento` fabricado:** eventos sem `movement` da BSD eram registados como `SHORTENING`; agora `UNKNOWN`. Gates inalterados (só DRIFTING rejeita). Análises por movimento passam a distinguir sinal real de dados em falta.

### Alterado
- Constantes e helpers partilhados dos scanners extraídos para `pipeline/scan_common.py` (whitelist, mapa de ligas BSD, Telegram, git, I/O JSON) — fonte única de verdade.
- Documentação de governança corrigida: fórmula real de `ev_final` (= `p_final × odds_over − 1`), estrutura real de `tests/`, remoção de referências a `elo.py`.

### Removido
- `models/math/elo.py` (experimental, zero usos — ver `docs/KILL_LEDGER.md`).
- Ficheiros-lixo `=x.y.z` na raiz (artefactos de pip).
- Constante morta `BTTS_O25_OVERLAY_MIN` e variável morta `implied_prob`.

### Adicionado
- `docs/` — tese de produto, inventário, kill ledger, revisão quantitativa, revisão de arquitectura, posição de mercado, scope, roadmap e este documento.

## Guia de migração

**Para o operador:** nada a fazer. Workflows, secrets, ficheiros de dados e UI não mudam.

**Para análises/queries sobre `picks.json`:**
- A partir desta versão, `movimento` pode valer `UNKNOWN`. Filtros `movimento == "SHORTENING"` passam a contar apenas SHORTENING confirmado pela BSD (antes incluíam casos sem dados). Picks históricos não foram alterados.

**Para código que importava `models.math.elo`:** não existia nenhum; se algum notebook local o usava, recuperar de `git log -- models/math/elo.py`.

## Dívida técnica remanescente (resumo)

1. Configuração duplicada (config.py vs constantes dos scanners) — roadmap #5.
2. `pipeline/etl.py` legado com um único uso vivo (`--validate`) — roadmap #8.
3. `rejected_picks.json` sem retenção — roadmap #6.
4. `engine.py` conta não-settled como LOSS se o caller não filtrar — roadmap #7.
5. `index.html` monólito — extração incremental para `js/`, sem reescrita antes de C5.

## Dívida científica (resumo)

1. **Edge não demonstrado** — CLV IC 95% inclui zero (N=83); é a razão do modo observação. Resolve-se com n, não com código.
2. **CLV do módulo 3 não é CLV** — rácio contra produto de marginais contém correlação estrutural (`MODEL_REVIEW.md` §6); interpretar como gate de selecção, nunca como edge.
3. **CLV Sharp 1X2 é proxy** até haver massa crítica de `odds_fecho` reais (xfail activo, REST pós-KO em curso).
4. Walk-forward BTTS+O2.5 sem `--fast` (OOS com re-fit semanal) pendente.

## Repository Health Score: **7.8 / 10**

| Dimensão | Peso | Nota | Comentário |
|---|---|---|---|
| Correcção estatística | 25% | 8.5 | Matemática sólida; protocolo LOEO/walk-forward acima da norma |
| Integridade de dados | 20% | 8.5 | Fail-closed + data_quality_flag; movement fail-open corrigido |
| Arquitectura | 20% | 7.0 | Camadas certas; config duplicada e ETL legado penalizam |
| Testes/CI | 15% | 8.0 | 213 testes verdes, mocks sem rede, data quality diário |
| Documentação | 10% | 8.0 | Governança exemplar; drifts corrigidos nesta auditoria |
| Front-end | 10% | 5.5 | Funcional mas monolítico |

Um contribuidor novo consegue orientar-se em <15 min lendo, por ordem: `README.md` → `docs/PRODUCT_THESIS.md` → `docs/ARCHITECTURE_REVIEW.md` (diagrama) → `CLAUDE.md` (operação).
