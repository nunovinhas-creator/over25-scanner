# Revisão Quantitativa — auditoria QuantCode (jul 2026)

Âmbito: todas as suposições estatísticas dos três módulos em produção. Cada secção termina num veredicto: **SÓLIDO**, **ACEITÁVEL COM RESSALVAS** ou **FRACO**.

---

## 1. Dixon-Coles (`models/math/poisson.py`)

### Implementação
- **Verosimilhança:** Poisson independente por equipa com correção `tau` nas quatro células de resultado baixo (0-0, 1-0, 0-1, 1-1) — fiel a Dixon & Coles (1997). A `_tau` está correcta célula a célula.
- **Decaimento temporal:** `exp(-xi·Δdias)` com `xi=0.0018` — o valor do paper original (metade-vida ≈ 385 dias). Com janela de treino de 2 épocas (`train_dc.py::N_SEASONS=2`), o peso da época anterior é ≈0.5 — razoável.
- **Identifiabilidade:** versão SLSQP usa restrição de igualdade `Σα=0`; a versão rápida (`fit_dixon_coles_fast`, L-BFGS-B) substitui por penalização L2 suave `1.0·(Σα)²`. Matematicamente isto é uma restrição *soft* — o óptimo pode ter `Σα≠0`, mas como um shift constante em α compensado por γ/β é quase não-identificável, o efeito nas probabilidades previstas é negligenciável. **Aceitável**, e o ganho de velocidade (~800×) viabiliza o re-treino semanal por 13 divisões.
- **Estabilidade numérica:** `log(λ+1e-300)`, `tau` clampado a `1e-9`, `rho` limitado a (−0.99, 0.99). Correcto.
- **Equipas não vistas:** fallback para força média da liga (α=β=0). Fail-safe razoável para promovidas no arranque de época; o Brier degrada-se mas não há crash.
- **Truncatura:** `max_goals=10` → massa perdida < 1e-6 para λ típicos (1–2.5). Correcto.

### Suposições e limites
- Poisson marginal ignora sobredispersão intra-jogo; o DC corrige apenas os resultados baixos. Para Over 2.5 (o alvo), a correção `rho` afecta directamente as células que definem Under — é o sítio certo para a correção. **SÓLIDO**.
- A grelha bivariada para BTTS+O2.5 (`build_dc_grid` + `extract_btts_over25_prob`) exclui correctamente (1,1) do evento conjunto (BTTS mas não Over 2.5). Verificado célula a célula. **SÓLIDO**.

**Veredicto: SÓLIDO.**

---

## 2. De-vig (`models/math/devig.py`)

- **Multiplicativo:** normalização proporcional exacta; é o método usado em produção para Over/Under.
- **Shin (1992):** fórmula de dois outcomes correcta, `z` resolvido por brentq com bracket válido (em `z=0` a soma é `√M>1`; em `z→1` tende a 0). Disponível mas não é o default.
- **Ressalva:** para mercados de 2 vias com margens pequenas (Pinnacle ~2-3%), multiplicativo vs Shin difere pouco; a escolha do multiplicativo como default é defensável pela simplicidade. O viés favorito-azarão importa mais no 1X2 — e o módulo Sharp 1X2 **não faz de-vig nenhum** (usa rácio de odds cruas B365/Pin), o que é imune à escolha do método.
- **Fallback de margem 5%** quando falta `odds_under` (`p = (1/odds)/1.05`): assume margem típica; marca `p_market_source="fallback"` para auditoria posterior. Boa prática — o registo distingue as duas proveniências.

**Veredicto: SÓLIDO.**

---

## 3. Calibração (`models/math/calibration.py` + `run_calibration.py`)

- **Protocolo:** LOEO-CV (leave-one-epoch-out) compara Platt vs isotónica em folds temporais; o método vencedor por Brier é re-ajustado no conjunto de treino completo e validado numa época held-out (2526). Isto é o desenho anti-leakage correcto.
- **Isotónica com guarda de amostra mínima** (fallback Platt com <30 amostras) — evita o overfitting clássico da isotónica em amostras pequenas.
- **Resultado FASE 4 (referência):** Brier calibrado 0.24168 vs mercado 0.24320; CLV IC 95% [−0.985%, +1.366%], N=83.

### Leitura honesta destes números
- A melhoria de Brier sobre o mercado é de **0.0015** — real mas minúscula, e obtida numa única época de validação. O IC do CLV inclui zero. **A hipótese "o modelo bate o fecho" ainda não foi demonstrada** — e é exactamente por isso que o sistema está (bem) em MODO OBSERVAÇÃO com gates. A infraestrutura de decisão é o que está sólido; o edge ainda não.
- `MODEL_WEIGHT=0.30` foi escolhido por grid {0.10, 0.15, 0.20, 0.30} no LOEO — a decisão de o congelar até nova validação completa é a atitude correcta contra tuning contínuo.

**Veredicto: protocolo SÓLIDO; evidência de edge INCONCLUSIVA (como o próprio repo documenta).**

---

## 4. Fórmula de EV — discrepância código vs documentação (CORRIGIDA NA DOC)

- **Código** (`transform.py`, ambas as funções): `ev_final = p_final × odds_over − 1`.
- **Documentação anterior** (CLAUDE.md, backend.md, data.md): `ev_final = p_final / p_market − 1`.

As duas fórmulas **não são equivalentes**: a do código é o EV real de apostar às odds brutas (inclui a margem do bookmaker — é o que se ganharia de facto); a da doc era a divergência modelo-vs-mercado sem vig (sempre mais optimista, porque `p_market_devig > 1/odds`). O gate de produção `MIN_EV=0.03` aplica-se à fórmula do **código**, que é a economicamente correcta para decidir apostar. A documentação foi corrigida para reflectir o código; o código não mudou. Consequência prática: o gate é *mais conservador* do que a doc sugeria — no bom sentido.

**Veredicto: código SÓLIDO; doc estava errada (corrigida).**

---

## 5. Sinal Sharp 1X2 (`div_b365_pin`)

- Sinal: `b365/pinnacle − 1 > 3%` por outcome, com gates de liga/timing/outcome. Racional: quando o book recreativo está lento a acompanhar a Pinnacle, a odd B365 tem valor residual.
- **Evidência:** 21.087 jogos históricos, ROI +2.46% em 3.731 apostas; walk-forward em duas rondas com CLV simulado positivo em ambas (+2.50%, +2.49%) mas ROI de sinal contrário na ronda 2 (−10.10%). A leitura correcta — CLV estável, ROI ruidoso — está feita e justifica o gate por CLV e não por ROI.
- **Ressalva metodológica:** o CLV do módulo usa `div_b365_pin` como proxy (não é linha de fecho); o CLV exacto exige `odds_fecho` pós-KO (`update_closing_odds.py`, xfail activo até haver n suficiente). O repo documenta isto correctamente e não mistura os dois.
- **Bug de delivery corrigido:** o alerta TG identificava sempre `HOME` como outcome (ver `KILL_LEDGER.md` §4).

**Veredicto: ACEITÁVEL COM RESSALVAS** (proxy de CLV declarado; closing real em curso).

---

## 6. Gate BTTS+O2.5 — "CLV" contra produto de marginais

O gate é `p_dc_conjunta / (p_btts_market × p_over25_market) − 1 ≥ 5%`.

**Ressalva estatística importante:** o denominador assume **independência entre BTTS e Over 2.5**, que é falsa — os dois eventos são fortemente correlacionados positivamente. O produto das marginais *subestima* a probabilidade conjunta de mercado, logo o rácio tem um viés positivo estrutural: parte dos "+5%" é correlação, não edge. Dois atenuantes:
1. O threshold de +5% (vs +1% dos outros módulos) absorve parte desse viés;
2. O viés é aproximadamente constante entre jogos com λ semelhantes, portanto o *ranking* de picks é menos afectado que o nível.

Mas a consequência é clara: **o "CLV ≥ 5%" do módulo 3 não é comparável ao CLV dos módulos 1-2** e não deve ser lido como edge de 5% vs mercado. Não existe odd de mercado para o evento conjunto na BSD; enquanto assim for, a validação real do módulo é o win-rate settled vs `p_dc_conjunta` (calibração), não o pseudo-CLV. Recomendação registada no roadmap: renomear o campo para `joint_ratio` ou validar por Brier da conjunta quando houver n≥100 settled. Nenhuma mudança de gate aplicada agora — mudar o gate a meio da observação invalidaria a experiência.

**Veredicto: FRACO como "CLV", ACEITÁVEL como gate de selecção pré-registado — desde que não seja interpretado como edge.**

---

## 7. Motor de backtest (`backtesting/engine.py`)

- Determinístico, ordenado cronologicamente, flat staking por defeito. Correcto.
- **Armadilha documentada:** `won = outcome == "WIN"` — qualquer resultado não-WIN (incluindo picks *não settled*, string vazia) conta como LOSS. Os callers actuais filtram para settled antes de chamar, mas o motor em si é fail-dangerous. Recomendação (roadmap): filtrar `result_over25 ∈ {WIN, LOSS}` dentro de `run()` como `roi_metrics._resolved_mask` já faz.
- Duplica `kelly_full` de `models/math/kelly.py` (consistente entre si; consolidar quando o Kelly sair de DEPRECATE).

**Veredicto: ACEITÁVEL COM RESSALVAS.**

---

## 8. Qualidade e integridade de dados

- **Fail-closed na whitelist** (`BSD_LEAGUE_ID_MAP` → '' → rejeição): correcto e testado.
- **`data_quality_flag`** e datas de observação efectiva: mecanismo anti-inflação de KPIs bem desenhado.
- **Corrigido nesta auditoria:** `movement` em falta era registado como `SHORTENING` (fail-open, fabricação de dados) — agora `UNKNOWN` (ver `KILL_LEDGER.md` §3). O gate (rejeitar DRIFTING) não mudou.
- **`previous_decimal_odds` ≠ closing line:** correctamente documentado e respeitado no código.
- **Crescimento sem limite de `rejected_picks.json`** (4.791 entradas, 1.8 MB): não é um problema estatístico mas será operacional; item de roadmap (retention de ~90 dias chega para análise de gates).

---

## 9. Risco de overfitting — avaliação global

| Vector | Exposição | Mitigação existente |
|---|---|---|
| Tuning de MODEL_WEIGHT | Baixa | Congelado por decisão; grid pequeno; LOEO |
| Selecção de estratégia de filtros | Média | Relatórios por estratégia; decisão de produção usa apenas EV≥3% baseline |
| Gate BTTS+O2.5 | Média | Threshold fixado antes dos dados live; mas ver §6 |
| Comparações múltiplas entre 3 módulos | Baixa-média | 3 hipóteses pré-registadas com gates distintos — aceitável; não adicionar módulos (ver `PRODUCT_SCOPE.md`) |
| Isotónica em amostra pequena | Baixa | Guarda de 30 amostras + LOEO |

**Conclusão global:** a matemática está certa e o protocolo de validação é acima da média para este domínio. O ponto fraco não é o código — é que a evidência de edge ainda é inconclusiva, e a única resposta certa é a que o sistema já pratica: acumular n em observação e decidir nos checkpoints.
