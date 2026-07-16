# Dead Code — candidatos a remoção

> Registo de código confirmado como não utilizado, para uma futura sessão de limpeza.
> **Não remover nesta sessão** — apenas registo com evidência. Cada entrada indica como
> foi confirmado e quando.

---

## `js/api-client.js`

- **O quê:** `APIClient` completo (retry exponencial, timeout, cache 60s, dedup de pedidos).
- **Porque é dead code:** o `<script src="js/api-client.js">` carrega **depois** do script
  inline principal (linha ~6065 do `index.html`), e `loadAll()` nunca o invoca — usa o `get()`
  inline (linhas ~1340+) para todos os fetches à BSD API. `CONFIG.API.BSD_BASE` (definido em
  `js/config.js`) e o `BASE` inline duplicam a mesma constante sem nunca convergir.
- **Evidência:** auditoria do frontend de 16 jul 2026 (diagnóstico do "Failed to fetch" /
  lentidão), secção 2.3: "`js/api-client.js` é código morto — existe um APIClient completo...
  mas é carregado... depois do script inline — e `loadAll()` nunca o usa."
- **Confirmado em:** 16 jul 2026.

## `CONFIG.LEAGUE_NAMES` (`js/config.js`)

- **O quê:** mapeamento `league_id → nome` com um conjunto de IDs completamente diferente do
  usado em produção (ex.: `6: 'Liga Portugal'`, `7: 'La Liga'`, `9: 'Serie A'` — não bate
  certo com os IDs reais da BSD API nem com `BSD_LEAGUE_ID_MAP`).
- **Porque é dead code:** zero referências a `CONFIG.LEAGUE_NAMES` em `index.html` ou em
  qualquer ficheiro `js/*.js` (confirmado por `grep`). O mapeamento realmente usado pela
  whitelist e pelo Sharp 1X2 é o `const BSD_LEAGUE_ID_MAP` inline no `index.html`
  (10 ligas, IDs 1/2/3/4/5/6/10/12/14/38 — o mesmo mapa espelhado em
  `pipeline/scan_common.py`).
- **Risco se não for removido:** um IDE/linter futuro pode assumir que `CONFIG.LEAGUE_NAMES`
  é a fonte de verdade e "corrigir" o `BSD_LEAGUE_ID_MAP` para bater certo com ele — o que
  quebraria a whitelist de produção.
- **Evidência:** diagnóstico da aba Sharp 1X2 a zero jogos, 16 jul 2026 — `grep -n
  "CONFIG.LEAGUE_NAMES\|CONFIG\.LEAGUE" index.html js/*.js` devolveu zero resultados.
- **Confirmado em:** 16 jul 2026.

---

## Como adicionar uma entrada

1. Confirmar com evidência reproduzível (grep de zero referências, ou trace de execução
   mostrando que o código nunca corre).
2. Registar aqui: ficheiro, o que é, porque é dead code, evidência exata, data.
3. **Não remover no mesmo commit do diagnóstico** — a remoção é uma tarefa própria, com o
   seu próprio branch/PR/CI verde, para poder ser revertida isoladamente se algo depender
   do código de forma não óbvia.
