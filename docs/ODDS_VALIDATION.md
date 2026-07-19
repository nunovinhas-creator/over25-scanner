# Validação de Odds — classify_odds() / classifyOdds()

> Sessão: `claude/odds-validation-hardening`. Corrige duas fragilidades deixadas
> pelo PR #129 (issue #127 — "Prob Over 100% · Odd 1.00"): duplicação de lógica
> Python/JS sem rede de segurança, e estados da BSD API nunca confirmados
> empiricamente. Ver `.claude/rules/testing.md` e `.claude/rules/cycles.md`.

---

## Ponto 1 — Fonte única de verdade para a validação de odds

### Decisão: **Opção (b)** — duas implementações, uma spec de testes partilhada

A opção preferida do enunciado ("Python é o único juiz; o pipeline grava
`odds_status` no JSON e o JS só lê e renderiza") **não é viável** neste
repositório, e não por preferência de design — por arquitectura:

- `loadLive()` em `index.html` (separador Live) faz **fetch directo à BSD API
  a partir do browser**, num `setInterval` de 30s, e cobre potencialmente
  qualquer jogo do dia (não só os das 10 ligas whitelisted — o filtro de liga
  só se aplica à geração de picks, não ao separador Live, que é diagnóstico).
- `pipeline/scan_live.py` corre em paralelo, **server-side**, no GitHub
  Actions (`live_scanner.yml`), com o seu próprio polling e o seu próprio
  `classify_odds()`.
- Estes são **dois processos independentes**, sem partilha de estado: o
  browser do utilizador recebe respostas da BSD que o processo Python nunca
  vê (e vice-versa). Não há um backend próprio neste projecto — é
  deliberadamente 100% browser/mobile (ver `CLAUDE.md`) — para introduzir "o
  Python é o único juiz" seria preciso adicionar um serviço proxy server-side
  só para reclassificar odds já recebidas pelo browser, o que contradiz a
  regra do projecto de não ter backend próprio e não resolve nada (o browser
  continuaria a precisar de classificar as odds que ele próprio recebeu antes
  de as poder mostrar).

Por isso a via escolhida foi a alternativa (b): **manter as duas
implementações, mas eliminar a possibilidade de divergirem silenciosamente**.

### O que foi feito

1. **`tests/fixtures/odds_classification_spec.json`** — fonte única de verdade
   dos casos de teste (input → classificação esperada). 18 casos: odds válidas,
   a sentinela `1.00`, o piso `1.01`, odds ausentes/não-numéricas, e os tokens
   de `market_status` suspenso (`suspended`, `stopped`, `closed`, `paused`,
   `off`, `inactive`, incluindo variantes de maiúsculas/hífen).
2. **`tests/pipeline/test_scan_common.py`** — carrega a spec e parametriza
   `test_classify_odds` a partir dela (Python, `pipeline/scan_common.py`).
3. **`tests/js/test_classify_odds.mjs`** — **não reimplementa a lógica em JS**.
   Extrai o bloco literal de `classifyOdds()` de `index.html` (delimitado pelos
   marcadores `// ODDS_CLASSIFICATION_JS:START` / `:END`), corre-o num sandbox
   Node (`vm`) e valida-o contra a mesma spec JSON. Sem dependências externas.
4. **CI**: novo job `odds-classification-js` em
   `.github/workflows/data_quality.yml` (corre em cada push/PR para `main`,
   `actions/setup-node@v4`, sem instalação de pacotes).
5. Comentários em `pipeline/scan_common.py` (docstring de `classify_odds`) e
   em `index.html` (bloco `classifyOdds`) documentam a decisão inline, com
   referência a este ficheiro.

### Garantia

Qualquer alteração futura a uma das duas implementações que a faça divergir
do comportamento esperado — **incluindo alterações que não tocam na spec** —
parte pelo menos um dos dois testes, porque ambos correm o código de produção
real (não uma cópia à mão) contra os mesmos casos. Adicionar um caso novo
exige editar apenas a spec; os dois testes recolhem-no automaticamente.

Não havia framework de testes JS neste repositório (o frontend é
propositadamente sem build step — ver `CLAUDE.md`). `tests/js/test_classify_odds.mjs`
usa só módulos nativos do Node (`fs`, `vm`, `assert`) para não introduzir
`package.json`/`node_modules` num projecto que não tem nenhum.

---

## Ponto 2 — Estados da API BSD: confirmados vs por confirmar

### Estado desta sessão: **sem rede + sem `BSD_API_KEY`**

Esta sessão (Claude Code on the web) corre num ambiente com acesso de rede
restrito a um conjunto fixo de domínios (GitHub, PyPI, npm, Anthropic — ver
`/root/.ccr/README.md`). Uma tentativa de contactar `sports.bzzoiro.com`
nesta sessão foi **recusada pelo proxy** (`connect_rejected`, `gateway
answered 403 to CONNECT — policy denial`), e não existe `BSD_API_KEY` no
ambiente. Isto é mais restritivo do que o cenário "sem jogos live" previsto
no enunciado — aqui não há rede nenhuma para a BSD, nem sequer para jogos
pré-KO ou terminados.

**Por isso: nenhum payload novo foi capturado nesta sessão.** Em conformidade
com a regra "nunca inventar payloads" (`.claude/rules/testing.md` §3, dados
sintéticos só em `tests/` e sempre marcados), este documento não contém
nenhum exemplo fabricado.

### O que já estava confirmado (issue #127, antes desta sessão)

| Estado | Sinal confirmado | Fonte |
|---|---|---|
| Mercado suspenso (golo/VAR) | `over_25_goals == 1.00` (sentinela) nos logs de produção `ODDS_STATUS=SUSPENDED` | Issue #127, PR #129 |
| Campo de status dedicado (`over_25_goals_status` / `status` no envelope de odds) | **Não confirmado** — mantido defensivamente em `_SUSPENDED_STATUS_TOKENS`, nunca visto numa resposta real | `pipeline/scan_common.py` (comentário junto à constante) |

### O que fica por confirmar

| Estado | Capturado nesta sessão? | Motivo |
|---|---|---|
| Mercado activo (odd normal) | ❌ | Sem rede/API key |
| Mercado suspenso (golo/VAR) | ❌ (só o valor numérico estava confirmado antes; payload completo nunca capturado) | idem |
| Intervalo (halftime) | ❌ | idem |
| Jogo terminado | ❌ | idem |
| Campo de status dedicado da BSD | ❌ | idem |

### Como recorrer a sondagem

`scripts/probe_bsd_odds_states.py` está pronto e tem cobertura de testes para
tudo o que **não** depende de rede/API key: `_bucket_for` (classificação por
estado), `_anonymize` (remoção de nomes de equipas/liga) e o fail-closed de
`main()` sem `BSD_API_KEY` — ver `tests/scripts/test_probe_bsd_odds_states.py`
(16 casos sintéticos, nenhum payload real). A parte que fala com a BSD em si
não tem — nem pode ter — teste automático nesta sessão.

Para capturar os payloads reais:

1. Correr o workflow `probe_bsd_odds_states.yml` via `workflow_dispatch`
   (branch/mobile-friendly — botão "Run workflow" no GitHub Actions), de
   preferência durante uma janela com jogos a decorrer (fins de tarde/noite
   CET com ligas europeias em curso) para maximizar a hipótese de apanhar os
   4 buckets, incluindo golo/VAR em curso.
2. Ler os logs do job — o script imprime, por bucket
   (`notstarted`/`inplay`/`halftime`/`finished`), até 3 exemplos anonimizados
   (nomes de equipas/liga substituídos por `<home_team>` etc., odds e status
   mantidos intactos) e um sumário dos campos com `status` no nome
   encontrados nos payloads de odds.
3. Se `inplay` aparecer com `over_25_goals <= 1.01` num payload, isso é
   evidência directa (não só inferida do log de produção) da sentinela.
4. Actualizar este documento com a tabela preenchida e, se aparecer um campo
   de status dedicado, promovê-lo a sinal primário em `classify_odds()` /
   `classifyOdds()` (o piso `MIN_VALID_ODDS` mantém-se sempre como fallback
   defensivo — nunca remover).
5. Adicionar os payloads reais capturados (anonimizados) como casos novos em
   `tests/fixtures/odds_classification_spec.json`, marcados com a data da
   captura.
