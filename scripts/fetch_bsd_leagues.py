#!/usr/bin/env python3
"""
scripts/fetch_bsd_leagues.py
-----------------------------
TAREFA 1 — Lista completa de ligas BSD + identificação das que faltam no mapa.
TAREFA 2 — Verifica acesso ao endpoint de odds.

Uso: BSD_API_KEY=<key> python scripts/fetch_bsd_leagues.py
"""
from __future__ import annotations
import os, sys, json
import urllib.request, urllib.error

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
if not BSD_API_KEY:
    print("ERRO: BSD_API_KEY não definido.", file=sys.stderr)
    sys.exit(1)

BASE = "https://sports.bzzoiro.com"
HEADERS = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}

NEED = {
    "Championship", "Bundesliga 2", "Serie B",
    "La Liga 2", "Primeira Liga", "Belgian Pro League",
}

def _get(path: str) -> tuple[int, object]:
    req = urllib.request.Request(BASE + path, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.reason}
    except Exception as exc:
        return 0, {"error": str(exc)}


# ── TAREFA 1 ──────────────────────────────────────────────────────────────────
print("=" * 60)
print("TAREFA 1 — BSD /api/v2/leagues/")
print("=" * 60)

status, payload = _get("/api/v2/leagues/?limit=200")
print(f"HTTP {status}")

if status != 200:
    print(f"Erro: {payload}")
else:
    leagues = payload if isinstance(payload, list) else (
        payload.get("results") or payload.get("data") or []
    )
    print(f"Total de ligas: {len(leagues)}\n")

    print(f"{'ID':<6} {'Name':<35} {'Country'}")
    print("-" * 60)
    found: dict[str, int] = {}
    for lg in sorted(leagues, key=lambda x: x.get("id", 0)):
        lid = lg.get("id", "?")
        name = lg.get("name") or lg.get("league_name") or "?"
        country = lg.get("country") or lg.get("country_name") or ""
        print(f"{lid:<6} {name:<35} {country}")
        if name in NEED:
            found[name] = lid

    print()
    print("── Ligas em falta no BSD_LEAGUE_ID_MAP ──")
    for league in sorted(NEED):
        lid = found.get(league)
        if lid:
            print(f"  ✅ {league:<30} id={lid}")
        else:
            print(f"  ❌ {league:<30} (não encontrada — verificar nome exacto)")


# ── TAREFA 2 ──────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("TAREFA 2 — BSD /api/v2/odds/?limit=1")
print("=" * 60)

status2, payload2 = _get("/api/v2/odds/?limit=1")
print(f"HTTP {status2}")

if status2 == 200:
    records = payload2 if isinstance(payload2, list) else (
        payload2.get("results") or payload2.get("data") or [payload2]
    )
    if records:
        sample = records[0]
        print("Campos disponíveis:", list(sample.keys()))
        print("Exemplo:")
        for k, v in sample.items():
            print(f"  {k}: {v!r}")
    else:
        print("Resposta vazia (sem registos).")
elif status2 == 403:
    print("403 — addon não ativo (odds endpoint bloqueado no plano actual).")
else:
    print(f"Erro {status2}: {payload2}")
