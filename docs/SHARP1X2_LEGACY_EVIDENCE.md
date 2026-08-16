# Sharp 1X2 — estratégias legacy sem evidência válida (Bloco G)

> Conclusão de investigação, 10 ago 2026, **corrigida em 16 ago 2026 (Bloco N)** — a
> causa da ausência de picks do pipeline actual estava mal atribuída (ver secção
> "Correcção — 16 ago 2026" abaixo). Regista por que as quatro estratégias 1X2
> exibidas no dashboard (incluindo "1X2 WATCH+HOME +44% ROI / WR 82%" e "1X2 HOME
> +31% ROI / WR 76%") **não são evidência válida para o pipeline actual**. Os números
> não são apagados — ficam registados aqui como pertencentes ao sistema legacy, com
> as datas, para que uma sessão futura não os volte a tratar como prova de edge do
> sistema em produção.

## Correcção — 16 ago 2026 (Bloco N)

A versão original desta conclusão (10 ago 2026) atribuía a ausência de picks do
pipeline actual, implicitamente, a gates demasiado restritivos. **Não era essa a
causa.** Investigação posterior (sessão live-scanner-backend-autonomous, 16 ago
2026) apurou:

- `data/picks_1x2.json` tem hoje **354 registos**: **352 legacy** (auto-log
  client-side, ver abaixo) + **2 do pipeline actual** (`fonte="auto-scan"`).
- **O pipeline nunca gerou um único pick antes de 14/08/2026** — não é que tenha
  parado nessa data, é que **nunca chegou a produzir** até aí. Os 2 primeiros picks
  `auto-scan` de sempre neste ficheiro foram gravados a 14/08/2026.
- A causa **não foram os gates**: foi um **bug de paginação silencioso** em
  `pipeline/scan_sharp1x2.py::_fetch_all_events()` — o cursor `next` da resposta de
  `/api/v2/events/` era descartado (`events, _ = _get_list(...)`), pelo que o scan só
  via a 1ª página (≤200 eventos) do dia, uma fatia arbitrária do calendário europeu
  completo. **Sem qualquer sinal de erro nos logs.** Corrigido no
  [PR #154](https://github.com/nunovinhas-creator/over25-scanner/pull/154),
  10/08/2026 — o primeiro pick real apareceu 4 dias depois.
- **Os gates (`apply_sharp1x2_gates()`) não foram alterados nesse período.** O
  achado abaixo ("0/352 legacy passam os gates de hoje") continua válido e é uma
  análise **separada** — testa se os registos *legacy* (auto-log do browser)
  passariam os gates actuais se fossem reavaliados hoje; não explica por que o
  *pipeline* esteve silencioso.
- Dois picks (n=2) não chegam perto de reabrir esta conclusão — ver "Para reabrir
  esta conclusão" no fim deste documento. O texto abaixo, anterior a este bloco,
  mantém-se tal como escrito a 10 ago 2026, com a correcção desta secção sobreposta.

## Resumo do diagnóstico (10 ago 2026 — ver correcção acima)

- `data/picks_1x2.json` tinha, nesta investigação de 10 ago 2026, 352 registos —
  **todos** gerados pelo auto-log client-side `autoLogSharp1x2()` (`index.html`),
  que parou de correr sozinho a **20/06/2026**.
- **341 dos 352 (97%)** têm `data_quality_flag="pre_bugfix_liga_vazia"` — o campo
  `liga` nunca foi gravado nesses registos. Não é possível saber de que competição
  eram 97% dos registos.
- A whitelist activa quando o auto-log parou tinha **12 ligas** (incluía Bundesliga 2
  e Serie B); a whitelist actual (`WHITELIST` em `pipeline/scan_common.py`) tem
  **10 ligas**.
- O auto-log legacy só bloqueava a gravação por `liga_fora_whitelist` — todos os
  outros motivos de rejeição (DRAW, HOME N1, timing fora de 0–6h, divergência <3%)
  eram gravados na mesma, só com o motivo anotado em `gate_blocked_reason`. O
  ficheiro contém **"tudo o que foi visto", não "picks aprovados"**.
- O pipeline actual (`apply_sharp1x2_gates()` em `pipeline/scan_sharp1x2.py`) é um
  filtro real: um registo que falha qualquer gate vai para
  `rejected_picks_1x2.json` e **nunca** entra em `picks_1x2.json`.
- As labels STEAM/SHARP/WATCH do auto-log legacy vinham de um score de movimento
  Pinnacle (limiar mínimo de 8 pontos, com bónus por timing e confirmação
  multi-book) que **já não existe** no pipeline actual — este avalia
  `div_b365_pin ≥ 3%` directamente, sem esse pré-filtro nem essas labels.
- Ao correr `apply_sharp1x2_gates()` (gates de hoje) contra os 352 registos legacy,
  usando apenas os campos existentes em cada registo: **0/352 passam**. Do
  subconjunto WATCH+HOME (245 dos 352 registos, 69,6% do total): **0/245 passam**.
  (Análise sobre os registos *legacy* — ver "Correcção — 16 ago 2026" acima para o
  que isto não explica.)

## Estratégias marcadas SEM EVIDÊNCIA VÁLIDA

| Estratégia (legacy) | Amostra citada | Sistema de origem | Estado |
|---|---|---|---|
| 1X2 WATCH+HOME | 245 de 352 · +44% ROI · WR 82% | auto-log client-side, parou 20/06/2026 | SEM EVIDÊNCIA VÁLIDA |
| 1X2 HOME | +31% ROI · WR 76% | auto-log client-side, parou 20/06/2026 | SEM EVIDÊNCIA VÁLIDA |

As restantes duas variantes 1X2 mostradas no mesmo dashboard partilham a mesma
proveniência de dados (`data/picks_1x2.json`, auto-log legacy) e ficam abrangidas
pela mesma marcação — os seus números não foram reapurados nesta investigação e por
isso não são repetidos aqui.

## Over 2.5 não é afectado

Esta marcação é específica dos módulos 1X2. As estratégias Over 2.5
(`backtesting/strategies.py`, secção "Estratégias — Backtesting Over 2.5" do
README) vêm de `data/picks.json`, escrito pelo pipeline actual
(`pipeline/scan_over25.py`). Não partilham a proveniência nem os problemas de dados
descritos acima.

## Para reabrir esta conclusão

Só faz sentido tratar uma estratégia 1X2 como evidência válida quando houver `n`
settled suficiente gerado pelo `pipeline/scan_sharp1x2.py` actual (picks que passam
`apply_sharp1x2_gates()` e ficam em `data/picks_1x2.json`) — ver gate de activação em
`.claude/rules/cycles.md` (CLV rolling-30 > +1%, n ≥ 200 settled). Até lá, os números
legacy acima não contam para essa contagem. Os 2 picks `auto-scan` gerados desde a
correcção do bug de paginação (14/08/2026) são o início dessa contagem, não o fim —
n=2 está muito longe de n≥200.
