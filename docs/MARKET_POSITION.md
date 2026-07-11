# Posição de Mercado — auditoria QuantCode (jul 2026)

Comparação com o panorama de ferramentas de modelação/valor em apostas de futebol. Como a tese é um **laboratório pessoal** ([`PRODUCT_THESIS.md`](PRODUCT_THESIS.md)), "mercado" aqui significa: *que alternativas existiam para o mesmo objectivo, e o que este repo faz melhor ou pior*.

## Panorama comparável

| Categoria | Exemplos | O que fazem |
|---|---|---|
| Bibliotecas open-source de modelos de futebol | `penaltyblog` (Dixon-Coles, Bayesian), `footballmodelling`, implementações académicas de DC/Karlis-Ntzoufras | Fitting e previsão; sem pipeline live, sem registo de picks, sem CLV |
| Ferramentas de value betting comerciais | RebelBetting, Trademate Sports, OddsJam | Detecção de +EV vs linha sharp em tempo real; caixa negra, subscrição mensal, sem modelo próprio do utilizador |
| Serviços de tips | inúmeros | Sinais sem metodologia auditável; incentivos desalinhados |
| Plataformas de dados/backtesting | football-data.co.uk (dados), Betfair historical, ProphitBet e afins | Dados e simulação offline; sem execução live nem tracking de fecho |

## Onde este repositório é mais forte

1. **Ciclo fechado live com registo imutável.** Nenhuma biblioteca open-source combina: modelo próprio re-treinado semanalmente + scan de odds a cada 30 min + registo git append-only de picks *e rejeitados* + proxy de CLV por pick. As ferramentas comerciais fazem o scan mas não deixam auditar nada.
2. **Disciplina de validação acima da norma do domínio.** LOEO-CV para o calibrador, walk-forward temporal, `data_quality_flag` anti-inflação de KPIs, gates de activação pré-registados com checkpoints datados. A maioria dos projectos amadores publica ROI in-sample; este repo documenta um CLV cujo IC inclui zero — honestidade rara.
3. **Custo marginal zero e operação mobile-only.** GitHub Actions + Pages + Telegram: sem servidor, sem subscrição além da BSD API. As alternativas comerciais custam €50–150/mês.
4. **Transparência de decisão.** `decisions.md` com critérios de reversão explícitos (Kelly off, DRAW suspenso, odds cap rejeitado com a evidência citada). É um audit trail de decisões que nem produtos comerciais têm.

## Onde é mais fraco

1. **Uma única fonte de odds (BSD).** RebelBetting/OddsJam agregam dezenas de books; aqui a divergência Sharp 1X2 depende de dois slugs (pinnacle, bet365) de um agregador só. Falha ou mudança de schema da BSD pára tudo — mitigado por fail-safe, não por redundância.
2. **Sem odds de fecho verdadeiras (ainda).** O CLV exacto depende de `update_closing_odds.py` pós-KO; o WebSocket da BSD foi rejeitado por custo. Ferramentas comerciais têm closing lines nativas.
3. **Modelo simples.** DC com xi fixo e 2 épocas de treino está aquém do estado da arte académico (modelos hierárquicos bayesianos, ratings dinâmicos). Aceitável: o objectivo não é o melhor modelo, é saber se *este* modelo bate o fecho.
4. **Bus factor = 1 e UI monolítica.** Sem valor para terceiros no estado actual — coerente com a tese, mas limita qualquer pivô futuro.

## O que diferencia (e deve ser protegido)

- O **harness de validação** (gates + checkpoints + registo imutável) é o activo. Modelos podem trocar-se; o harness é o que transforma sinais em conhecimento.
- A **regra de dados sintéticos** e o `data_quality_flag` — nunca copiar a prática comum de "limpar" histórico para melhorar KPIs.

## O que nunca copiar das alternativas

- **Marketing de ROI sem IC nem n** (padrão dos serviços de tips).
- **Staking automático antes de edge provado** (padrão das ferramentas de arbitragem "auto-bet") — contraria a decisão Kelly-off.
- **Multiplicação de mercados para gerar volume de sinais** — ver rejeição da tese 4 em `PRODUCT_THESIS.md` e o scope fechado em `PRODUCT_SCOPE.md`.
