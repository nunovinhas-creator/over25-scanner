# ⚽ Over 2.5 Scanner

Scanner profissional de apostas Over 2.5 baseado em dados reais da [API Bzzoiro](https://sports.bzzoiro.com).

## 🚀 Como usar

### Opção 1 — GitHub Pages (recomendado)
1. Faz fork deste repositório
2. Vai a **Settings → Pages → Source: main / root**
3. Acede a `https://teu-username.github.io/over25-scanner/`

### Opção 2 — Localmente
Abre o ficheiro `index.html` diretamente no browser. Não precisas de servidor.

---

## 📊 Sistema de scoring (0–100)

| Critério | Pontos |
|---|---|
| Prob. ML ≥ 70% | +35 |
| Prob. ML 60–69% | +25 |
| Prob. ML 50–59% | +15 |
| ML recomenda Over 2.5 | +15 |
| Odds a baixar (Shortening) | +20 |
| Odds a subir (Drifting) | −10 |
| Odds ≤ 1.55 | +15 |
| Odds 1.56–1.75 | +8 |
| Odds ≥ 2.20 | −5 |
| Alta confiança modelo | +5 |

**Semáforo:**
- 🟢 **Score ≥ 70** — Pick forte
- 🟡 **Score 50–69** — Pick moderado
- ⚪ **Score < 50** — Sem sinal claro

---

## 🔑 API Key

Regista-te gratuitamente em [sports.bzzoiro.com/register](https://sports.bzzoiro.com/register/) para obteres a tua API key.

---

## ⚙️ Funcionalidades

- Jogos das próximas 48h em tempo real
- Probabilidade ML Over 2.5 (CatBoost)
- Movimento de odds (Sharp money detection)
- Odds por bookmaker expandíveis
- Filtro por liga, score mínimo, tier
- Modo escuro automático
- 100% client-side — sem servidor, sem backend

---

## 📁 Estrutura

```
over25-scanner/
├── index.html      # App completa (single file)
└── README.md
```

---

*Dados fornecidos pela [Bzzoiro Sports Data API](https://sports.bzzoiro.com) — gratuita.*
