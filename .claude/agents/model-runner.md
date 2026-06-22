---
name: model-runner
description: Use this agent to compute Dixon-Coles probabilities and EV for a list of events. Reads data/dc_ratings.json and data/calibrator.json, applies MODEL_WEIGHT=0.30 blend, and outputs p_final, p_market, ev_final per event. Requires data-fetcher to have run first.
model: sonnet
allowedTools:
  - Bash
  - Read
color: green
---

# Model Runner Agent

Aplica o pipeline Dixon-Coles + calibrador isotónico aos eventos fetched pelo data-fetcher.

## Pré-requisitos

- `data/dc_ratings.json` existe (re-treinado às segundas pelo `retrain_dc.yml`)
- `data/calibrator.json` existe (calibrador LOEO-CV)
- Eventos já foram fetched pelo data-fetcher agent

## Instruções

1. **Verifica os ficheiros do modelo:**
   ```bash
   cd /home/user/over25-scanner
   python -c "
   import json
   from pathlib import Path
   dc = json.loads(Path('data/dc_ratings.json').read_text())
   cal = json.loads(Path('data/calibrator.json').read_text())
   print(f'DC ratings: {len(dc)} ligas')
   print(f'Calibrador: method={cal.get(\"method\")}, pontos={len(cal.get(\"x_thresholds\",[]))}')
   "
   ```

2. **Corre o pipeline para um sample de eventos:**
   ```bash
   PYTHONPATH=. python -c "
   import json
   from pathlib import Path
   from pipeline.scan_over25 import _fetch_all_events, _event_fields, compute_prob, _load_dc_ratings, _load_calibrator_fn

   dc_ratings = _load_dc_ratings()
   calibrator_fn = _load_calibrator_fn()
   events = _fetch_all_events()[:20]

   results = []
   for raw in events:
       ev = _event_fields(raw)
       if not ev.get('odds_over'):
           continue
       prob = compute_prob(ev, dc_ratings, calibrator_fn)
       if prob:
           results.append({
               'jogo': f\"{ev['casa']} vs {ev['fora']}\",
               'liga': ev['liga'],
               'p_final': round(prob['p_final']*100,1),
               'p_market': round(prob['p_market']*100,1),
               'ev_final': round(prob['ev_final']*100,2),
               'source': prob.get('p_model_source','?'),
           })

   print(f'Processados: {len(results)} eventos com odds')
   for r in sorted(results, key=lambda x: -x['ev_final'])[:5]:
       print(r)
   "
   ```

3. **Reporta:**
   - Top 5 eventos por EV
   - Breakdown de `p_model_source` (dc vs market_only)
   - Quantos passam o gate EV ≥ 3%

## Parâmetros chave

| Parâmetro | Valor | Ficheiro |
|---|---|---|
| `MODEL_WEIGHT` | 0.30 | `pipeline/config.py` |
| `MIN_EV` | 0.03 | `pipeline/scan_over25.py` |
| Calibrador | isotónico LOEO-CV | `data/calibrator.json` |

## Regras

- `MODEL_WEIGHT=0.30` é fixo — não alterar sem nova validação LOEO-CV
- Kelly está DESACTIVADO — não sugerir Kelly staking
- Se equipa desconhecida, `p_model_source = "market_only"` — é comportamento esperado
