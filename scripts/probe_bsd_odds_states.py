#!/usr/bin/env python3
"""
scripts/probe_bsd_odds_states.py
----------------------------------
Diagnóstico one-off: que estados de mercado a BSD API expõe realmente nos
seus payloads de odds, e existe algum campo de status dedicado (para além
da odd numérica)?

Contexto: classify_odds() (pipeline/scan_common.py) e classifyOdds()
(index.html) tratam duas coisas como sinónimo de "sem preço negociável":
  1. a odd numérica <= MIN_VALID_ODDS (1.01) — a sentinela 1.00 confirmada
     empiricamente no issue #127 (payloads de produção com "Odd 1.00 ·
     Prob Over 100%");
  2. um eventual `market_status`/`*_status` explícito da BSD — nunca
     confirmado numa resposta real (ver _SUSPENDED_STATUS_TOKENS em
     scan_common.py, mantido defensivamente).

Este script tenta confirmar (2) e capturar exemplos reais de payload para
os 4 estados observáveis pedidos: mercado activo, suspenso (golo/VAR em
curso), intervalo, jogo terminado. Não gera picks — o whitelist de ligas
não se aplica aqui, isto é diagnóstico de API.

Corre via GitHub Actions (workflow_dispatch, `probe_bsd_odds_states.yml`)
com o secret BSD_API_KEY. Fail-closed: sem API key ou sem rede, aborta
sem inventar nada — nunca escrever um payload de exemplo que não veio de
uma resposta real da API (ver docs/odds_validation.md).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
BASE = "https://sports.bzzoiro.com"
TIMEOUT = 30

# Buckets de estado que queremos exemplificar — best-effort, depende do que
# estiver a decorrer no momento em que o script corre.
BUCKET_NOTSTARTED = "notstarted"
BUCKET_INPLAY = "inplay"
BUCKET_HALFTIME = "halftime"
BUCKET_FINISHED = "finished"
BUCKET_OTHER = "other"

_STATUS_BUCKETS = {
    BUCKET_NOTSTARTED: {"notstarted", "not_started", "scheduled", "upcoming", "tbd"},
    BUCKET_HALFTIME: {"halftime", "ht", "break"},
    BUCKET_FINISHED: {"finished", "ended", "completed", "closed", "ft", "aet", "afterpenalties"},
}

MAX_SAMPLES_PER_BUCKET = 3


def _get(path: str) -> object:
    url = path if path.startswith("http") else BASE + path
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    r = requests.get(url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _extract_events(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "results", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
    return []


_NORMALIZED_STATUS_BUCKETS = {
    bucket: {t.replace("_", "") for t in tokens} for bucket, tokens in _STATUS_BUCKETS.items()
}


def _bucket_for(ev: dict) -> str:
    status = str(ev.get("status") or "").lower().replace("_", "").replace(" ", "").replace("-", "")
    minute = ev.get("current_minute")
    for bucket, tokens in _NORMALIZED_STATUS_BUCKETS.items():
        if status in tokens:
            return bucket
    if status in {"inplay", "live", "1sthalf", "2ndhalf", "1h", "2h", "playing", "live1h", "live2h"}:
        return BUCKET_INPLAY
    if isinstance(minute, (int, float)) and minute > 0:
        return BUCKET_INPLAY
    return BUCKET_OTHER


def _anonymize(ev: dict) -> dict:
    """Remove nomes de equipas/ligas do exemplo antes de este sair para stdout
    de um workflow público — mantém a estrutura e os valores relevantes
    (odds, status) intactos, que é o que importa para o diagnóstico."""
    clone = dict(ev)
    for key in ("home_team", "away_team", "home", "away", "league_name", "league"):
        if key in clone:
            clone[key] = f"<{key}>"
    return clone


def _probe_event_odds(ev_id) -> dict | None:
    try:
        return _get(f"/api/v2/events/{ev_id}/odds/")
    except Exception as exc:  # noqa: BLE001
        print(f"    ✗ odds ev={ev_id} → erro: {exc}")
        return None


def main() -> None:
    if not BSD_API_KEY:
        print(
            "BSD_API_KEY não definido — abortar sem inventar payloads. "
            "Correr via workflow_dispatch (probe_bsd_odds_states.yml) com o secret configurado.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("=" * 70)
    print("BSD ODDS STATES PROBE")
    print(f"ts={datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)

    today = datetime.now(timezone.utc).date().isoformat()
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    date_q = f"date_from={today}&date_to={tomorrow}&limit=200"

    print(f"\n[1] GET /api/v2/events/?{date_q}")
    try:
        payload = _get(f"/api/v2/events/?{date_q}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ falhou: {exc}", file=sys.stderr)
        sys.exit(1)

    events = _extract_events(payload)
    print(f"  N eventos na janela: {len(events)}")

    buckets: dict[str, list[dict]] = {
        BUCKET_NOTSTARTED: [], BUCKET_INPLAY: [], BUCKET_HALFTIME: [],
        BUCKET_FINISHED: [], BUCKET_OTHER: [],
    }
    for ev in events:
        buckets[_bucket_for(ev)].append(ev)

    for bucket, evs in buckets.items():
        print(f"  bucket={bucket}: {len(evs)} eventos")

    captured: dict[str, list[dict]] = {}
    status_field_candidates: set[str] = set()

    for bucket in (BUCKET_NOTSTARTED, BUCKET_INPLAY, BUCKET_HALFTIME, BUCKET_FINISHED):
        sample = buckets[bucket][:MAX_SAMPLES_PER_BUCKET]
        if not sample:
            print(f"\n[2] bucket={bucket}: SEM eventos disponíveis nesta sessão — não capturado.")
            continue
        print(f"\n[2] bucket={bucket}: a sondar {len(sample)} evento(s)")
        captured[bucket] = []
        for ev in sample:
            ev_id = ev.get("id")
            odds = _probe_event_odds(ev_id)
            if odds is None:
                continue
            odds_block = odds.get("odds") if isinstance(odds, dict) else None
            if isinstance(odds_block, dict):
                status_field_candidates.update(
                    k for k in odds_block.keys() if "status" in k.lower()
                )
            if isinstance(odds, dict):
                status_field_candidates.update(
                    k for k in odds.keys() if "status" in k.lower()
                )
            example = {
                "event": _anonymize(ev),
                "odds_response": odds,
            }
            captured[bucket].append(example)
            over_odds = (odds_block or {}).get("over_25_goals") if isinstance(odds_block, dict) else None
            print(f"    ev={ev_id} status={ev.get('status')!r} period={ev.get('period')!r} "
                  f"minute={ev.get('current_minute')!r} over_25_goals={over_odds!r}")
            print(f"    payload: {json.dumps(example, ensure_ascii=False)[:1200]}")

    print("\n" + "=" * 70)
    print("SUMÁRIO")
    print("=" * 70)
    print(f"Campos com 'status' encontrados nos payloads de odds: {sorted(status_field_candidates) or 'NENHUM'}")
    for bucket in (BUCKET_NOTSTARTED, BUCKET_INPLAY, BUCKET_HALFTIME, BUCKET_FINISHED):
        n = len(captured.get(bucket, []))
        print(f"  {bucket}: {n} exemplo(s) capturado(s)")
    missing = [b for b in (BUCKET_NOTSTARTED, BUCKET_INPLAY, BUCKET_HALFTIME, BUCKET_FINISHED) if not captured.get(b)]
    if missing:
        print(f"\nEstados NÃO capturados nesta corrida: {missing}")
        print("Nunca inventar payloads para estes — recorrer o script mais tarde numa janela")
        print("com jogos nesse estado (ex.: correr perto do intervalo de uma ronda europeia).")
    print(
        "\nNota: 'suspenso (golo/VAR)' não tem bucket de status dedicado conhecido — "
        "sondar dentro de 'inplay' e procurar over_25_goals <= 1.01 (sentinela) nos "
        "exemplos acima é o único sinal disponível até se confirmar um campo de status."
    )


if __name__ == "__main__":
    main()
