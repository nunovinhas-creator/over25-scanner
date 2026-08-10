# Sharp 1X2 — estratégias legacy sem evidência válida (Bloco G)

> Conclusão de investigação, 10 ago 2026. Regista por que as quatro estratégias 1X2
> exibidas no dashboard (incluindo "1X2 WATCH+HOME +44% ROI / WR 82%" e "1X2 HOME
> +31% ROI / WR 76%") **não são evidência válida para o pipeline actual**. Os números
> não são apagados — ficam registados aqui como pertencentes ao sistema legacy, com
> as datas, para que uma sessão futura não os volte a tratar como prova de edge do
> sistema em produção.

## Resumo do diagnóstico

- `data/picks_1x2.json` tem 352 registos. **Todos** foram gerados pelo auto-log
  client-side `autoLogSharp1x2()` (`index.html`), que parou de correr sozinho a
  **20/06/2026**.
- O pipeline actual (`pipeline/scan_sharp1x2.py`, cron 30 min desde então) **nunca
  escreveu um único pick neste ficheiro**. Amostra real do sistema actual sobre estes
  352 registos: **zero**.
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
legacy acima não contam para essa contagem.
