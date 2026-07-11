#!/usr/bin/env python3
"""
scripts/probe_bsd_ws.py
------------------------
Diagnóstico do WebSocket BSD (/ws/live/): auth, formato de subscrição e
estrutura dos frames (odds, odds_book, event, livedata).

Corre via GitHub Actions (workflow_dispatch: probe_bsd_ws.yml) com
BSD_API_KEY secret. O objectivo é confirmar o protocolo antes de confiar
no listener de produção (pipeline/ws_closing_odds.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import requests

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
BASE = "https://sports.bzzoiro.com"
WS_BASE = "wss://sports.bzzoiro.com"

LISTEN_SECONDS = 45          # tempo de escuta por variante de ligação
MAX_FRAMES_LOGGED = 12       # frames impressos por variante (truncados)
FRAME_TRUNC = 900            # chars por frame no log

# Candidatos de URL (com e sem token na query)
URL_VARIANTS = [
    "/ws/live/",
    "/ws/live/?token={key}",
]

# Candidatos de mensagem de subscrição (enviados em sequência)
SUBSCRIBE_CANDIDATES = [
    {"action": "subscribe", "type": "odds"},
    {"action": "subscribe", "type": "odds_book", "bookmaker_slug": "pinnacle"},
    {"type": "subscribe", "channel": "odds"},
    {"type": "subscribe", "channel": "odds_book", "bookmaker_slug": "pinnacle"},
    {"subscribe": "odds_book", "bookmaker_slug": "pinnacle"},
    {"action": "subscribe", "event_id": "{event_id}"},
    {"action": "subscribe", "event_id": "{event_id}", "bookmaker_slug": "pinnacle"},
]


def _rest_get(path: str) -> object:
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}
    r = requests.get(BASE + path, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def _strip_html(html: str) -> str:
    """Remove tags/scripts/styles de forma rudimentar para ler docs HTML."""
    import re
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", "\n", html)
    lines = [ln.strip() for ln in html.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _print_docs_hints() -> None:
    """Tenta obter documentação pública do WS (llms.txt e páginas de docs)."""
    print("\n[0] Documentação pública — referências a WebSocket")
    for path in ("/llms.txt", "/api/docs/websocket/", "/websocket/", "/docs/"):
        try:
            r = requests.get(BASE + path, timeout=20)
            if r.status_code != 200:
                print(f"  {path} → HTTP {r.status_code}")
                continue
            text = r.text
            lines = [
                ln.strip() for ln in text.splitlines()
                if any(k in ln.lower() for k in ("ws", "websocket", "subscribe", "frame", "token"))
            ]
            print(f"  {path} → {len(text)} chars; linhas relevantes ({len(lines)}):")
            for ln in lines[:60]:
                print(f"    | {ln[:200]}")
        except Exception as exc:
            print(f"  {path} → erro: {exc}")

    # Página de protocolo completa — texto integral (sem HTML) para análise
    print("\n[0b] /docs/websocket/ — protocolo completo (texto)")
    try:
        r = requests.get(BASE + "/docs/websocket/", timeout=20)
        print(f"  HTTP {r.status_code}, {len(r.text)} chars")
        if r.status_code == 200:
            text = _strip_html(r.text)
            print(text[:12000])
    except Exception as exc:
        print(f"  erro: {exc}")


def _pick_target_event() -> str:
    """Evento live se existir; senão o próximo notstarted de hoje."""
    try:
        d = _rest_get("/api/v2/events/live/")
        evs = d.get("events") if isinstance(d, dict) else d
        if evs:
            eid = str(evs[0].get("id"))
            print(f"  Evento live escolhido: {eid} ({evs[0].get('home_team')} vs {evs[0].get('away_team')})")
            return eid
    except Exception as exc:
        print(f"  /events/live/ erro: {exc}")
    try:
        d = _rest_get("/api/v2/events/?status=notstarted&limit=1")
        evs = d if isinstance(d, list) else (d.get("results") or [])
        if evs:
            eid = str(evs[0].get("id"))
            print(f"  Sem live — próximo evento: {eid} ({evs[0].get('home_team')} vs {evs[0].get('away_team')})")
            return eid
    except Exception as exc:
        print(f"  /events/ erro: {exc}")
    return ""


async def _probe_variant(url: str, headers: dict, event_id: str, label: str) -> None:
    import websockets

    print(f"\n[{label}] connect: {url.replace(BSD_API_KEY, '***')}")
    try:
        async with websockets.connect(
            url, additional_headers=headers, open_timeout=15, close_timeout=5
        ) as ws:
            print("  ✅ ligado")

            # Enviar candidatos de subscrição (best-effort)
            for cand in SUBSCRIBE_CANDIDATES:
                msg = json.dumps(cand).replace("{event_id}", event_id or "0")
                try:
                    await ws.send(msg)
                    print(f"  → sent: {msg}")
                except Exception as exc:
                    print(f"  → send falhou ({msg}): {exc}")
                await asyncio.sleep(0.3)

            # Escutar frames
            n = 0
            loop = asyncio.get_event_loop()
            deadline = loop.time() + LISTEN_SECONDS
            while loop.time() < deadline and n < MAX_FRAMES_LOGGED:
                try:
                    frame = await asyncio.wait_for(
                        ws.recv(), timeout=max(1.0, deadline - loop.time())
                    )
                except asyncio.TimeoutError:
                    break
                n += 1
                if isinstance(frame, bytes):
                    print(f"  ← [binary {len(frame)} bytes]")
                    continue
                print(f"  ← frame {n}: {frame[:FRAME_TRUNC]}")
                # Resumo de chaves para frames JSON grandes
                try:
                    obj = json.loads(frame)
                    if isinstance(obj, dict):
                        print(f"     keys: {sorted(obj.keys())} | type={obj.get('type')!r}")
                except Exception:
                    pass
            if n == 0:
                print(f"  (nenhum frame em {LISTEN_SECONDS}s — pode não haver jogos live)")
    except Exception as exc:
        print(f"  ✗ falhou: {type(exc).__name__}: {exc}")


async def _amain() -> None:
    event_id = _pick_target_event()

    variants = []
    for tmpl in URL_VARIANTS:
        url = WS_BASE + tmpl.format(key=BSD_API_KEY)
        # sem header e com header Authorization
        variants.append((url, {}, f"A:{tmpl}"))
        variants.append((url, {"Authorization": f"Token {BSD_API_KEY}"}, f"B:{tmpl}+hdr"))
    # variante por-evento (caso o servidor use path scoping)
    if event_id:
        variants.append((f"{WS_BASE}/ws/live/{event_id}/", {"Authorization": f"Token {BSD_API_KEY}"}, "C:/ws/live/{id}/+hdr"))

    for url, headers, label in variants:
        await _probe_variant(url, headers, event_id, label)


def main() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — abortar", file=sys.stderr)
        sys.exit(1)
    print("=" * 60)
    print("BSD WEBSOCKET PROBE")
    print("=" * 60)
    _print_docs_hints()
    print("\n[1] Selecção de evento alvo")
    asyncio.run(_amain())
    print("\nFIM DO PROBE")


if __name__ == "__main__":
    main()
