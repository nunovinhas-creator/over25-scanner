#!/usr/bin/env python3
"""
scripts/probe_bsd_closing_odds.py
----------------------------------
Diagnóstico: a BSD API devolve odds Pinnacle pós-KO para eventos settled?

Testa se GET /api/v2/odds/?market=1x2&event_id=<id> devolve resultados
para event_ids de picks já settled em data/picks_1x2.json.

Responde à questão crítica antes de implementar fetch_closing_odds().

Corre via GitHub Actions (workflow_dispatch) com BSD_API_KEY secret.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
BASE = "https://sports.bzzoiro.com"
ROOT = Path(__file__).resolve().parent.parent
PICKS_FILE = ROOT / "data" / "picks_1x2.json"


def _get(path: str, params: dict | None = None) -> object:
    url = path if path.startswith("http") else BASE + path
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()


def main() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — abortar", file=sys.stderr)
        sys.exit(1)

    print("=" * 60)
    print("BSD CLOSING ODDS PROBE")
    print("=" * 60)

    # Carrega picks settled para obter event_ids reais
    picks: list[dict] = []
    if PICKS_FILE.exists():
        picks = json.loads(PICKS_FILE.read_text(encoding="utf-8"))

    settled = [
        p for p in picks
        if p.get("resultado_outcome") in ("WIN", "LOSS")
        and not p.get("data_quality_flag")
    ]
    if not settled:
        # Fallback: usa picks com data_quality_flag (para testar o endpoint)
        settled = [p for p in picks if p.get("resultado_outcome") in ("WIN", "LOSS")]

    sample = settled[-5:] if len(settled) >= 5 else settled
    print(f"\nPicks settled disponíveis: {len(settled)} (a testar {len(sample)})")

    print("\n[1] Odds por event_id (método principal):")
    found_pinnacle = 0

    for pick in sample:
        pick_id = str(pick.get("id", ""))
        event_id = pick_id.split("_")[0]
        outcome = str(pick.get("outcome", "HOME")).upper()
        ko = pick.get("data", "?")

        print(f"\n  {pick.get('casa')} vs {pick.get('fora')} | KO: {ko}")
        print(f"  pick_id={pick_id} | event_id={event_id} | outcome={outcome}")

        try:
            data = _get("/api/v2/odds/", {"market": "1x2", "event_id": event_id, "limit": 50})
            results = data if isinstance(data, list) else (
                data.get("results") or data.get("data") or []
            )
            print(f"  → {len(results)} registos devolvidos")

            if results:
                by_book: dict[str, dict] = {}
                for r in results:
                    slug = r.get("bookmaker_slug", "?")
                    raw_out = (r.get("outcome") or "").upper()
                    odds = r.get("decimal_odds")
                    by_book.setdefault(slug, {})[raw_out] = odds

                books = list(by_book.keys())
                print(f"  → Bookmakers: {books}")
                if "pinnacle" in by_book:
                    print(f"  ✅ PINNACLE encontrada: {by_book['pinnacle']}")
                    found_pinnacle += 1
                else:
                    print(f"  ❌ Pinnacle ausente (books disponíveis: {books[:5]})")
            else:
                print(f"  ❌ Nenhuma odd devolvida para event_id={event_id}")

        except Exception as exc:
            print(f"  ✗ Erro: {exc}")

    print("\n[2] Teste de eventos com status=finished:")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    for status in ("finished", "ended", "completed"):
        try:
            data = _get("/api/v2/events/", {"status": status, "date_from": yesterday, "limit": 3})
            results = data if isinstance(data, list) else (
                data.get("results") or data.get("data") or []
            )
            print(f"  status={status!r} → {len(results)} eventos")
            if results:
                ev = results[0]
                print(f"    Exemplo: {ev.get('home_team','?')} vs {ev.get('away_team','?')}")
                print(f"    Campos: {list(ev.keys())[:8]}")
                break
        except Exception as exc:
            print(f"  status={status!r} → erro: {exc}")

    print("\n[3] Odds sem filtro de event_id (para confirmar formato):")
    try:
        data = _get("/api/v2/odds/", {"market": "1x2", "bookmaker_slug": "pinnacle", "limit": 3})
        results = data if isinstance(data, list) else (data.get("results") or data.get("data") or [])
        print(f"  {len(results)} resultados Pinnacle 1x2 actuais")
        if results:
            r0 = results[0]
            print(f"  Campos disponíveis: {list(r0.keys())}")
            print(f"  Exemplo: event_id={r0.get('event_id')}, outcome={r0.get('outcome')}, odds={r0.get('decimal_odds')}")
    except Exception as exc:
        print(f"  Erro: {exc}")

    print("\n" + "=" * 60)
    print("CONCLUSÃO")
    print("=" * 60)
    if found_pinnacle > 0:
        print(f"✅ BSD API devolve odds Pinnacle para {found_pinnacle}/{len(sample)} eventos settled")
        print("   → fetch_closing_odds() VIÁVEL — implementação pode avançar")
    else:
        print("❌ BSD API NÃO devolve odds Pinnacle para eventos settled")
        print("   Alternativas para CLV Sharp 1X2:")
        print("   1. Usar football-data.co.uk PSH/PSD/PSA (Pinnacle closing, semanal)")
        print("   2. Aceitar CLV proxy = div_b365_pin actual (sem closing line)")
        print("   3. Contratar acesso a Pinnacle closing odds via provider dedicado")


if __name__ == "__main__":
    main()
