# Kill Ledger — remoções e depreciações

> Registo obrigatório de tudo o que foi removido ou despromovido na auditoria QuantCode (jul 2026), com a justificação de por que razão a remoção **fortalece** o produto. Nada aqui foi removido por gosto — cada entrada cita a tese ([`PRODUCT_THESIS.md`](PRODUCT_THESIS.md)).

## Removido nesta auditoria

### 1. Ficheiros `=0.17.0`, `=1.0.0`, `=1.11.0`, `=1.24.0`, `=1.3.0`, `=2.0.0`, `=2.31.0`, `=5.17.0`, `=7.4.0` (raiz)
- **O que eram:** artefactos acidentais de `pip install pandas >=2.0.0` executado sem aspas na shell — o operador `>=` redirecionou output para ficheiros chamados `=2.0.0`, etc. Um deles continha apenas um warning do pip.
- **Porque a remoção fortalece:** são ruído puro na raiz do repositório; um contribuidor novo perde tempo a perceber o que são. Zero referências no código.
- **Reversível?** Irrelevante — não têm conteúdo útil.

### 2. `models/math/elo.py` (Elo Over/Under 2.5, experimental)
- **O que era:** adaptação de Elo para prever Over 2.5 via logística sobre a soma de ratings, com constantes "calibradas" (`_OVER25_INTERCEPT=-2.10`, `_OVER25_SLOPE=0.0008`) sem validação registada em nenhum relatório.
- **Porque a remoção fortalece:**
  - Zero importadores em produção, backtesting, scripts ou testes (verificado por grep).
  - Não consta do backlog técnico (`cycles.md`) — ao contrário do Skellam, que é item pré-registado.
  - A tese exige hipóteses pré-registadas com gates; um segundo modelo Over 2.5 "à espera" convida a data-dredging (correr o Elo sobre os mesmos dados até parecer bom).
  - Mantê-lo tem custo real: é superfície documentada em CLAUDE.md/backend.md que qualquer sessão futura tem de ler e considerar.
- **Reversível?** Sim — `git log` preserva o ficheiro; se um dia o Elo for pré-registado como sinal candidato, recupera-se com validação walk-forward desde o início.
- **Nota:** `skellam_from_elo()` em `skellam.py` recebe ratings como floats e não importa o módulo — não é afectado.

### 3. Campo `movimento` inventado por omissão (`scan_over25.py`)
- **O que era:** `mov_map.get(eid, "SHORTENING")` e `(ev.get("movement") or "SHORTENING")` — quando a BSD não devolvia `movement`, o scanner registava **SHORTENING** por omissão (fail-open).
- **Porque a remoção fortalece:** é fabricação de dados. O gate 3 só rejeita DRIFTING, portanto o default não mudava decisões de gate, mas contaminava o registo: a estratégia `shortening_only` e o breakdown do dashboard tratavam "sem dados" como "sinal SHORTENING confirmado". A tese vive da integridade do registo (regra `data.md`: nunca inflar KPIs). Agora regista `UNKNOWN`.
- **Impacto:** picks futuros com `movimento="UNKNOWN"` quando a BSD não enviar movement. Comportamento dos gates inalterado. Análises por movimento passam a distinguir sinal real de dados em falta. Picks históricos não são tocados (regra: nunca reescrever registos).

### 4. Alerta Telegram Sharp 1X2 com outcome errado (`scan_sharp1x2.py::_build_msg`)
- **O que era:** o texto do alerta dizia sempre `Outcome: HOME`, hardcoded, mesmo quando o pick era AWAY (HOME até está bloqueado na Eredivisie e DRAW nunca chega ao alerta — a maioria dos alertas reais são AWAY e diziam HOME).
- **Porque a correção fortalece:** um alerta que identifica o lado errado da aposta é o pior defeito possível num sistema cujo output são alertas. Corrigido para `pick['outcome']`.

### 5. Variável morta `implied_prob` (`pipeline/transform.py::enrich_picks`)
- **O que era:** cálculo `implied_prob = 1.0 / odds` nunca usado.
- **Porque a remoção fortalece:** código morto em função de enriquecimento sugere um passo de análise que não existe.

### 6. Estrutura de testes fantasma em `.claude/rules/testing.md`
- **O que era:** a doc descrevia `tests/models/test_poisson.py`, `tests/models/test_calibration.py`, `tests/backtesting/test_engine.py` — ficheiros que **não existem**; a estrutura real é `tests/`, `tests/pipeline/`, `tests/data_quality/`.
- **Porque a correção fortalece:** documentação que descreve ficheiros inexistentes falha o teste dos "15 minutos para um contribuidor novo" e mina a confiança em toda a restante governança. Corrigida para a árvore real.

## Despromovido (DEPRECATE — mantido com justificação)

### `pipeline/etl.py` (orquestração ETL GAS-centrada)
Produção não passa por `run_etl` — os scanners chamam BSD directamente. Mantido apenas porque `data_quality.yml` invoca `pipeline/etl.py --validate` e os filtros `filter_*` têm testes vivos. Plano: migrar `--validate` para um módulo próprio e reduzir `etl.py` na época 2627. Removê-lo hoje partiria um workflow diário em produção — risco sem benefício imediato.

### `models/math/kelly.py`
Kelly está DESACTIVADO por decisão permanente (`decisions.md`) com critério de reactivação explícito (CLV rolling-30 validado ao vivo). O módulo fica como implementação de referência **não importada em produção** — removê-lo apagaria trabalho que a própria decisão prevê reutilizar; mantê-lo activo violaria a decisão. DEPRECATE é o único estado coerente com a governança.

## Avaliado e mantido intencionalmente (não removido)

| Item | Razão para manter |
|---|---|
| `models/math/skellam.py` | Item de backlog pré-registado (2º sinal 1X2). |
| Calibradores Platt/Beta/Temperature/Ensemble | Fazem parte do protocolo LOEO de selecção de método — a comparação é o que dá credibilidade à escolha da isotónica. |
| `index.html` monólito | Reescrever a UI a 20 dias do checkpoint C5 é risco operacional puro. Refactor incremental já em curso via `js/`. |
| `backtesting/strategies.py` estratégias todas | Cada estratégia corresponde a um relatório histórico citado em decisões (ex.: rejeição do odds cap). Removê-las tornaria as decisões não-reproduzíveis. |
