#!/usr/bin/env python3
"""
scripts/probe_bsd_markets.py
-----------------------------
Diagnóstico: quais mercados de odds a BSD API suporta?
Usado para determinar se existe um market BTTS ou BTTS+Over 2.5.

Corre via GitHub Actions (workflow_dispatch) com BSD_API_KEY secret.
"""

from __future__ import annotations

import json
import os
import sys
import requests

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
BASE = "https://sports.bzzoiro.com"

CANDIDATE_MARKETS = [
    # Candidatos BTTS e combinados — nomes típicos na indústria
    "btts", "gg", "both_teams_score", "both_teams_to_score",
    "btts_over25", "over_25_btts", "gg_over25",
    "goal_goal", "bts",
    # Outros mercados que podem existir
    "double_chance", "asian_handicap", "draw_no_bet",
    "correct_score", "half_time",
    "over_under_15", "over_under_35", "over_under_45",
]


def _get(path: str) -> object:
    url = path if path.startswith("http") else BASE + path
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — abortar", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("BSD MARKETS PROBE")
    print("=" * 60)

    # 1. Estrutura genérica — qualquer odds sem filtro de market
    print("\n[1] GET /api/v2/odds/?limit=5 (sem filtro de market)")
    data = _get("/api/v2/odds/?limit=5")
    results = data if isinstance(data, list) else (data.get("results") or data.get("data") or [])
    print(f"  N resultados: {len(results)}")
    if results:
        print("  Primeiro resultado:")
        print(json.dumps(results[0], indent=4, ensure_ascii=False))
        all_keys = set()
        for r in results:
            all_keys.update(r.keys())
        print(f"\n  Campos únicos (todos os {len(results)} resultados): {sorted(all_keys)}")
        markets_seen = {r.get("market") or r.get("market_type") or r.get("market_name") for r in results}
        print(f"  Valores de 'market'/'market_type' encontrados: {markets_seen}")

    # 2. Tentar cada market candidato
    print("\n[2] Probe de markets candidatos BTTS")
    found = []
    for market in CANDIDATE_MARKETS:
        try:
            d = _get(f"/api/v2/odds/?market={market}&limit=1")
            res = d if isinstance(d, list) else (d.get("results") or d.get("data") or [])
            if res:
                print(f"  ✅ market={market!r} → {len(res)} resultado(s)")
                print(f"     Primeiro: {json.dumps(res[0], indent=6, ensure_ascii=False)[:300]}")
                found.append(market)
            else:
                print(f"  ❌ market={market!r} → vazio")
        except Exception as exc:
            print(f"  ✗  market={market!r} → erro: {exc}")

    # 2b. Bookmakers activos — confirmar o slug da entrada de consenso
    print("\n[2b] GET /api/v2/bookmakers/ — slugs disponíveis")
    try:
        d = _get("/api/v2/bookmakers/")
        bks = d if isinstance(d, list) else (d.get("results") or d.get("data") or [])
        for bk in bks:
            print(f"  slug={bk.get('slug')!r}  name={bk.get('name')!r}")
        consensus = [bk.get("slug") for bk in bks if "consensus" in (bk.get("slug") or "")]
        print(f"  → slugs de consenso: {consensus or 'NENHUM'}")
    except Exception as exc:
        print(f"  Erro: {exc}")

    # 3. Listar todos os markets disponíveis via endpoint sem filtro (paginado)
    print("\n[3] Scan de markets únicos nos primeiros 200 resultados")
    try:
        d = _get("/api/v2/odds/?limit=200")
        res = d if isinstance(d, list) else (d.get("results") or d.get("data") or [])
        market_vals = set()
        for r in res:
            for field in ("market", "market_type", "market_name"):
                v = r.get(field)
                if v:
                    market_vals.add(str(v))
        print(f"  Markets únicos encontrados ({len(market_vals)}): {sorted(market_vals)}")
    except Exception as exc:
        print(f"  Erro: {exc}")

    print("\n" + "=" * 60)
    print("SUMÁRIO")
    print("=" * 60)
    if found:
        print(f"Markets BTTS disponíveis: {found}")
    else:
        print("Nenhum market BTTS/GG encontrado na BSD API.")
        print("Módulo BTTS+Over 2.5 fica em modo observação sem CLV de mercado.")


if __name__ == "__main__":
    main()
