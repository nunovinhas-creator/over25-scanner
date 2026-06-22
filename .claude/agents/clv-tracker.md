---
name: clv-tracker
description: Use this agent to compute CLV and ROI metrics across all 3 production modules. Reads data/picks.json, data/picks_1x2.json, and data/picks_btts_over25.json, applies models/metrics/roi_metrics.py, and reports CLV rolling-30, ROI, profit factor, and drawdown per module.
model: haiku
allowedTools:
  - Bash
  - Read
color: yellow
---

# CLV Tracker Agent

Calcula métricas de CLV e ROI para os 3 módulos em produção usando `models/metrics/roi_metrics.py`.

## Instruções

1. **Lê os picks dos 3 módulos:**
   ```bash
   cd /home/user/over25-scanner
   python -c "
   import json
   from pathlib import Path
   for f in ['data/picks.json','data/picks_1x2.json','data/picks_btts_over25.json']:
       picks = json.loads(Path(f).read_text()) if Path(f).exists() else []
       settled = [p for p in picks if p.get('resultado_outcome') in ('WIN','LOSS') or p.get('resultado_btts_over25') in ('WIN','LOSS')]
       flagged = [p for p in picks if p.get('data_quality_flag')]
       clean = [p for p in picks if not p.get('data_quality_flag')]
       print(f'{f}: total={len(picks)}, settled={len(settled)}, flagged={len(flagged)}, clean={len(clean)}')
   "
   ```

2. **Calcula CLV rolling-30 e ROI:**
   ```bash
   PYTHONPATH=. python -c "
   import json, sys
   from pathlib import Path

   modules = {
       'Over 2.5':    ('data/picks.json',            'resultado_outcome',        'clv'),
       'Sharp 1X2':   ('data/picks_1x2.json',        'resultado_outcome',        'clv'),
       'BTTS+O2.5':   ('data/picks_btts_over25.json', 'resultado_btts_over25',   'clv_btts_over25'),
   }

   for name, (path, res_field, clv_field) in modules.items():
       if not Path(path).exists():
           print(f'{name}: ficheiro não encontrado')
           continue
       picks = json.loads(Path(path).read_text())
       clean = [p for p in picks if not p.get('data_quality_flag')]
       settled = [p for p in clean if p.get(res_field) in ('WIN','LOSS')]
       with_clv = [p for p in clean if p.get(clv_field) is not None]
       roll30 = sorted(with_clv, key=lambda p: p.get('scanned_at',''), reverse=True)[:30]
       clv_avg = sum(float(p[clv_field]) for p in roll30) / len(roll30) if roll30 else None
       wins = sum(1 for p in settled if p.get(res_field)=='WIN')
       wr = wins/len(settled)*100 if settled else None
       print(f'{name}:')
       print(f'  clean picks: {len(clean)} | settled: {len(settled)} | WR: {wr:.1f}%' if wr else f'  clean picks: {len(clean)} | settled: {len(settled)}')
       print(f'  CLV rolling-30: {clv_avg*100:+.2f}% (n={len(roll30)})' if clv_avg is not None else f'  CLV rolling-30: — (n={len(roll30)})')
   "
   ```

3. **Verifica gates de activação:**
   ```bash
   PYTHONPATH=. python -c "
   # Gates de activação para apostas reais:
   # Over 2.5:  CLV rolling-30 > +1% com n >= 300 settled
   # Sharp 1X2: CLV rolling-30 > +1% com n >= 200 settled
   # BTTS+O2.5: CLV rolling-30 > +5% com n >= 100 settled
   print('Gate Over 2.5:  CLV > +1% e n >= 300 settled')
   print('Gate Sharp 1X2: CLV > +1% e n >= 200 settled')
   print('Gate BTTS+O2.5: CLV > +5% e n >= 100 settled')
   "
   ```

## Output esperado

```
Over 2.5:
  clean picks: N | settled: N | WR: X%
  CLV rolling-30: +X.XX% (n=30)
Sharp 1X2:
  clean picks: N | settled: N | WR: X%
  CLV rolling-30: +X.XX% (n=30)
BTTS+O2.5:
  clean picks: N | settled: N
  CLV rolling-30: +X.XX% (n=N)
```

## Regras

- Picks com `data_quality_flag` não-vazio são SEMPRE excluídos dos KPIs
- Picks anteriores a 17 jun 2026 (Over 2.5 / Sharp) e 21 jun 2026 (BTTS) têm `data_quality_flag`
- CLV rolling-30 = média dos últimos 30 picks com CLV calculado, ordenados por `scanned_at` desc
