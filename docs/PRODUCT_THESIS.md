# Tese de Produto — canónica

> Documento produzido pela auditoria QuantCode (jul 2026). Toda a documentação e todo o código devem alinhar-se com esta tese. Alterações à tese exigem nova auditoria.

## Tese escolhida

**Laboratório pessoal de validação de sinais de apostas, disciplinado por CLV (Closing Line Value).**

O produto não é um bot de apostas, não é um serviço de tips, não é uma plataforma comercial. É um **sistema de investigação quantitativa de operador único** cujo objectivo é responder, com rigor estatístico e sem dinheiro em risco, a uma única pergunta:

> *"Algum dos meus sinais (Over 2.5 DC+calibração, Sharp 1X2 por divergência, BTTS+O2.5 bivariado) bate consistentemente a linha de fecho do mercado?"*

Tudo no repositório existe para servir esse ciclo: **gerar picks automaticamente → registá-los imutavelmente → medir CLV rolling → decidir nos checkpoints C3/C4/C5 se algum módulo passa a apostas reais.**

### Porque é que esta tese é a correcta

1. **É o que o sistema já faz de facto.** Os três módulos estão em MODO OBSERVAÇÃO com gates de activação explícitos (CLV rolling-30 > +1%/+5% com n mínimo). Não há staking, não há bankroll, Kelly está desactivado por decisão permanente. O produto real é a *experiência*, não a aposta.
2. **É cientificamente honesta.** A validação FASE 4 mostrou Brier calibrado apenas marginalmente melhor que o mercado (0.24168 vs 0.24320) e CLV com IC 95% que inclui zero ([−0.985%, +1.366%], N=83). Um "bot de apostas" com esta evidência seria fraude estatística; um *laboratório que ainda não rejeitou a hipótese nula* é exactamente o que os dados suportam.
3. **O CLV é a métrica certa.** ROI de curto prazo tem variância enorme; CLV vs Pinnacle é o proxy padrão da indústria para edge real e converge com n muito menor. Toda a governança do repo (data_quality_flag, gates, checkpoints) já está construída à volta disto.
4. **Um operador, custo zero.** GitHub Actions + BSD API + Telegram + GitHub Pages. Sem servidores, sem build, gerível 100% via browser/mobile. A tese comercial exigiria infraestrutura e obrigações que o autor explicitamente não quer.

### Utilizador e valor

- **Quem beneficia:** o autor (nunovinhas-creator), como único operador e decisor.
- **Quem pagaria:** ninguém, por design — o retorno esperado é a decisão informada de apostar (ou não) capital próprio em agosto de 2026 (checkpoint C5). O valor de um falso positivo evitado (não apostar num sinal sem edge) é directamente mensurável em bankroll não perdido.

## Teses rejeitadas

Foram geradas e atacadas cinco teses concorrentes:

### 1. "Bot de apostas automático" — REJEITADA
Implicaria staking automático, gestão de bankroll, execução em bookmakers. Contradiz três decisões permanentes (Kelly DESACTIVADO, MODO OBSERVAÇÃO, checkpoints obrigatórios) e a evidência estatística actual (CLV IC inclui zero). Adoptar esta tese hoje seria overbetting sobre edge não provado — o erro exacto que a governança do repo existe para impedir.

### 2. "Motor de probabilidades de futebol open-source" (biblioteca) — REJEITADA
O código DC/Shin/calibração é genérico e reutilizável, mas empacotá-lo como biblioteca exigiria API pública estável, versionamento semântico, documentação de biblioteca e abdicar do acoplamento à BSD API e ao formato picks.json. Já existem alternativas maduras (penaltyblog, footballprediction). O diferencial deste repo não é o modelo — é o *harness de validação ao vivo*. A tese biblioteca destruiria o diferencial para competir onde o repo é mais fraco.

### 3. "Serviço de tips/alertas Telegram" (comercial) — REJEITADA
Vender sinais não validados é ética e comercialmente indefensável; vender sinais validados exigiria primeiro a validação — ou seja, exigiria a tese escolhida como pré-requisito. Além disso: chat_id pessoal hardcoded, uma subscrição BSD, zero infra multi-utilizador. Custo de oportunidade alto, valor presente nulo.

### 4. "Scanner de ineficiências multi-mercado" (expansão horizontal) — REJEITADA
Adicionar Asian Handicap, Correct Score, Under, cartões, cantos, etc. multiplicaria a superfície de código e as comparações múltiplas (p-hacking estrutural: com 20 mercados, algum terá CLV positivo por acaso). A força do sistema actual é a disciplina de 3 hipóteses pré-registadas com gates definidos *antes* dos dados. Mais mercados = menos ciência.

### 5. "Plataforma de investigação/backtesting para terceiros" — REJEITADA
O backtesting walk-forward é sólido, mas transformá-lo em plataforma multi-utilizador exigiria abstração de fontes de dados, UI de configuração e suporte. Nenhum destes esforços aproxima o autor da decisão C5. Sem procura comprovada, é gold-plating.

## Consequências operacionais da tese

1. **Nenhuma feature nova entra sem servir o ciclo pick → registo → CLV → decisão.** (Ver `PRODUCT_SCOPE.md`.)
2. **Integridade de dados > funcionalidade.** Um campo mal registado (ex.: `movimento` inventado por default) corrompe a experiência; é bug crítico mesmo que a UI funcione.
3. **Os módulos experimentais (Elo, Skellam) só se justificam como candidatos a sinais futuros pré-registados** — Skellam está no backlog (2º sinal 1X2), Elo não está em nenhum plano e foi removido (ver `KILL_LEDGER.md`).
4. **A decisão C5 (31 jul 2026) é o milestone do produto.** O roadmap orienta-se a garantir que nessa data existem n suficientes e CLV fiável por módulo.
