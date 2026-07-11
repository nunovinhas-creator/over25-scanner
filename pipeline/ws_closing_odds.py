#!/usr/bin/env python3
"""
pipeline/ws_closing_odds.py
----------------------------
Captura closing odds em tempo real via WebSocket BSD (/ws/live/).

Motivação (backlog: CLV exacto): a janela live da BSD começa em KO−5min,
por isso o último frame de odds recebido antes do KO é a closing line —
mais exacto que o fetch REST pós-KO (+15min) do update_closing_odds.py,
que se mantém como fallback (ambos ignoram picks já preenchidos).

Fluxo:
1. Lê picks_1x2.json e picks.json → alvos com KO na janela
   [agora − GRACE, agora + WINDOW_MIN] e sem closing preenchido.
2. Sem alvos → exit 0 imediato (cron barato, zero chamadas API).
3. Liga ao WS, subscreve odds (best-effort, formatos múltiplos),
   acumula a última odd vista ANTES do KO por (event_id, market, outcome).
   Preferência de fonte: pinnacle > consensus > outro.
4. No fim (todos os KO passados ou deadline), grava:
   - Sharp 1X2: odds_fecho + clv = (odds_entrada/odds_fecho − 1)·100
   - Over 2.5:  odds_over_close + clv (mesma fórmula com odds_over)
5. Commit [skip ci] só se algo mudou.

Config por env (defaults seguros):
  BSD_API_KEY        — obrigatório
  BSD_WS_URL         — default wss://sports.bzzoiro.com/ws/live/
                       (aceita template com {event_id} → 1 ligação por evento)
  WS_WINDOW_MIN      — janela de KO à frente (default 40)
  WS_MAX_RUNTIME_MIN — tecto de execução (default 50)
  WS_GRACE_S         — margem após KO para congelar (default 120)
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_1X2_FILE = DATA_DIR / "picks_1x2.json"
PICKS_O25_FILE = DATA_DIR / "picks.json"

BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
WS_URL = os.environ.get("BSD_WS_URL", "wss://sports.bzzoiro.com/ws/live/")
WINDOW_MIN = float(os.environ.get("WS_WINDOW_MIN", "40"))
MAX_RUNTIME_MIN = float(os.environ.get("WS_MAX_RUNTIME_MIN", "50"))
GRACE_S = float(os.environ.get("WS_GRACE_S", "120"))

# Preferência da fonte da closing line (maior = melhor)
_SOURCE_RANK = {"pinnacle": 2, "consensus": 1}

SUBSCRIBE_CANDIDATES = [
    {"action": "subscribe", "type": "odds"},
    {"action": "subscribe", "type": "odds_book", "bookmaker_slug": "pinnacle"},
    {"type": "subscribe", "channel": "odds"},
    {"type": "subscribe", "channel": "odds_book", "bookmaker_slug": "pinnacle"},
]


# ── Pure helpers (testáveis sem rede) ───────────────────────────────────────────

def _parse_ko(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def _source_rank(slug: str | None) -> int:
    s = (slug or "").lower()
    if "pinnacle" in s:
        return _SOURCE_RANK["pinnacle"]
    if "consensus" in s:
        return _SOURCE_RANK["consensus"]
    return 0


def build_targets(picks_1x2: list[dict], picks_o25: list[dict], now: datetime) -> list[dict]:
    """
    Selecciona picks com KO em [now − GRACE, now + WINDOW_MIN] e sem
    closing preenchido. Devolve alvos normalizados:
      {kind, pick_id, event_id, ko, market, outcome, odds_entry}
    """
    targets: list[dict] = []
    lo = now.timestamp() - GRACE_S
    hi = now.timestamp() + WINDOW_MIN * 60

    for p in picks_1x2:
        if str(p.get("odds_fecho", "")).strip():
            continue
        out = str(p.get("outcome", "")).upper()
        if out not in ("HOME", "DRAW", "AWAY"):
            continue
        ko = _parse_ko(p.get("data", ""))
        eid = str(p.get("id", "")).split("_")[0]
        if not ko or not eid.isdigit() or not (lo <= ko.timestamp() <= hi):
            continue
        try:
            odds_entry = float(p.get("odds_entrada") or 0)
        except (TypeError, ValueError):
            odds_entry = 0.0
        targets.append({
            "kind": "sharp1x2", "pick_id": str(p.get("id", "")), "event_id": eid,
            "ko": ko, "market": "1x2", "outcome": out, "odds_entry": odds_entry,
        })

    for p in picks_o25:
        if str(p.get("odds_over_close", "")).strip():
            continue
        ko = _parse_ko(p.get("data", ""))
        eid = str(p.get("id", "")).split("_")[0]
        if not ko or not eid.isdigit() or not (lo <= ko.timestamp() <= hi):
            continue
        try:
            odds_entry = float(p.get("odds_over") or 0)
        except (TypeError, ValueError):
            odds_entry = 0.0
        targets.append({
            "kind": "over25", "pick_id": str(p.get("id", "")), "event_id": eid,
            "ko": ko, "market": "over_under_25", "outcome": "over",
            "odds_entry": odds_entry,
        })

    return targets


def extract_odds_rows(frame: object) -> list[dict]:
    """
    Extrai linhas de odds de um frame WS, defensivamente.

    Aceita: linha única (dict com market/outcome/decimal_odds), lista de
    linhas, ou envelope {type: 'odds'|'odds_book', ...} com payload em
    'data' / 'odds' / 'payload' / 'results' (ou no próprio envelope).
    Outros tipos de frame (event, livedata, ...) → [].
    """
    def _is_row(o: object) -> bool:
        return isinstance(o, dict) and "decimal_odds" in o and ("market" in o or "outcome" in o)

    if _is_row(frame):
        return [frame]  # type: ignore[list-item]
    if isinstance(frame, list):
        return [r for r in frame if _is_row(r)]
    if not isinstance(frame, dict):
        return []

    ftype = str(frame.get("type") or "")
    if ftype and ftype not in ("odds", "odds_book"):
        return []

    for key in ("data", "odds", "payload", "results"):
        inner = frame.get(key)
        if _is_row(inner):
            rows = [inner]
        elif isinstance(inner, list):
            rows = [r for r in inner if _is_row(r)]
        else:
            continue
        # event_id pode vir só no envelope
        env_eid = frame.get("event_id")
        if env_eid is not None:
            for r in rows:
                r.setdefault("event_id", env_eid)
        # slug pode vir só no envelope (frames odds_book)
        env_slug = frame.get("bookmaker_slug")
        if env_slug:
            for r in rows:
                r.setdefault("bookmaker_slug", env_slug)
        return rows
    return []


class ClosingTracker:
    """Última odd vista antes do KO por (event_id, market, outcome)."""

    def __init__(self, targets: list[dict]):
        self.targets = targets
        self.ko_by_event = {t["event_id"]: t["ko"] for t in targets}
        self.wanted = {(t["event_id"], t["market"], t["outcome"].lower()) for t in targets}
        # key → {odds, rank, seen_at}
        self.best: dict[tuple, dict] = {}

    def ingest(self, rows: list[dict], now: datetime) -> None:
        for r in rows:
            eid = str(r.get("event_id") or "")
            market = str(r.get("market") or "")
            outcome = str(r.get("outcome") or "").lower()
            key = (eid, market, outcome)
            if key not in self.wanted:
                continue
            ko = self.ko_by_event.get(eid)
            if ko and now.timestamp() > ko.timestamp():
                continue  # já é in-play — não é closing line
            try:
                odds = float(r.get("decimal_odds") or 0)
            except (TypeError, ValueError):
                continue
            if odds <= 1.0:
                continue
            rank = _source_rank(r.get("bookmaker_slug"))
            cur = self.best.get(key)
            # mesma fonte (ou melhor): observação mais recente ganha
            if cur is None or rank >= cur["rank"]:
                self.best[key] = {"odds": odds, "rank": rank, "seen_at": now.isoformat()}

    def closing_for(self, t: dict) -> float | None:
        rec = self.best.get((t["event_id"], t["market"], t["outcome"].lower()))
        return rec["odds"] if rec else None


def apply_closing(picks: list[dict], targets: list[dict], tracker: ClosingTracker,
                  kind: str, odds_field: str) -> int:
    """Escreve closing + clv nos picks do tipo `kind`. Devolve nº actualizados."""
    by_id = {t["pick_id"]: t for t in targets if t["kind"] == kind}
    updated = 0
    for p in picks:
        t = by_id.get(str(p.get("id", "")))
        if not t:
            continue
        closing = tracker.closing_for(t)
        if closing is None:
            continue
        p[odds_field] = closing
        if t["odds_entry"] > 0:
            p["clv"] = round((t["odds_entry"] / closing - 1) * 100, 4)
        p["closing_source"] = "ws"
        updated += 1
    return updated


# ── I/O ─────────────────────────────────────────────────────────────────────────

def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save(path: Path, data: list[dict]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _git_commit(files: list[str], msg: str) -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=True)
        subprocess.run(["git", "add"] + files, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print(f"Commit: {msg}")
        else:
            print("Sem alterações para commitar.")
    except subprocess.CalledProcessError as exc:
        print(f"git falhou: {exc}", file=sys.stderr)


# ── WS listener ─────────────────────────────────────────────────────────────────

async def _listen(targets: list[dict], tracker: ClosingTracker) -> None:
    import websockets

    deadline_ts = min(
        max(t["ko"].timestamp() for t in targets) + GRACE_S,
        datetime.now(timezone.utc).timestamp() + MAX_RUNTIME_MIN * 60,
    )

    # URL template com {event_id} → uma ligação por evento; senão feed global
    if "{event_id}" in WS_URL:
        urls = [WS_URL.format(event_id=t) for t in sorted({t["event_id"] for t in targets})]
    else:
        sep = "&" if "?" in WS_URL else "?"
        urls = [f"{WS_URL}{sep}token={BSD_API_KEY}"]

    headers = {"Authorization": f"Token {BSD_API_KEY}"}

    async def _one(url: str) -> None:
        backoff = 2.0
        while datetime.now(timezone.utc).timestamp() < deadline_ts:
            try:
                async with websockets.connect(
                    url, additional_headers=headers, open_timeout=20, close_timeout=5
                ) as ws:
                    print(f"WS ligado: {url.replace(BSD_API_KEY, '***')}")
                    backoff = 2.0
                    for cand in SUBSCRIBE_CANDIDATES:
                        try:
                            await ws.send(json.dumps(cand))
                        except Exception:
                            break
                    while True:
                        now = datetime.now(timezone.utc)
                        remaining = deadline_ts - now.timestamp()
                        if remaining <= 0:
                            return
                        try:
                            frame = await asyncio.wait_for(ws.recv(), timeout=min(30.0, remaining))
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(frame, bytes):
                            continue
                        try:
                            obj = json.loads(frame)
                        except Exception:
                            continue
                        tracker.ingest(extract_odds_rows(obj), datetime.now(timezone.utc))
            except Exception as exc:
                remaining = deadline_ts - datetime.now(timezone.utc).timestamp()
                if remaining <= 0:
                    return
                print(f"WS erro ({type(exc).__name__}: {exc}) — retry em {backoff:.0f}s", file=sys.stderr)
                await asyncio.sleep(min(backoff, remaining))
                backoff = min(backoff * 2, 60.0)

    await asyncio.gather(*(_one(u) for u in urls))


def main() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — abortar", file=sys.stderr)
        sys.exit(0)

    picks_1x2 = _load(PICKS_1X2_FILE)
    picks_o25 = _load(PICKS_O25_FILE)
    now = datetime.now(timezone.utc)

    targets = build_targets(picks_1x2, picks_o25, now)
    if not targets:
        print("Sem picks com KO na janela — nada a fazer.")
        return

    print(f"{len(targets)} alvo(s) na janela de {WINDOW_MIN:.0f} min:")
    for t in targets:
        print(f"  {t['kind']} {t['pick_id']} — KO {t['ko'].isoformat()} ({t['market']}/{t['outcome']})")

    tracker = ClosingTracker(targets)
    asyncio.run(_listen(targets, tracker))

    n_sharp = apply_closing(picks_1x2, targets, tracker, "sharp1x2", "odds_fecho")
    n_o25 = apply_closing(picks_o25, targets, tracker, "over25", "odds_over_close")

    files: list[str] = []
    if n_sharp:
        _save(PICKS_1X2_FILE, picks_1x2)
        files.append(str(PICKS_1X2_FILE))
    if n_o25:
        _save(PICKS_O25_FILE, picks_o25)
        files.append(str(PICKS_O25_FILE))

    print(f"Closing via WS: {n_sharp} Sharp 1X2 | {n_o25} Over 2.5 actualizados")
    missing = [t["pick_id"] for t in targets if tracker.closing_for(t) is None]
    if missing:
        print(f"Sem closing capturada (fallback REST tratará): {missing}")

    if files:
        ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        _git_commit(files, f"ws-closing-odds {ts} [skip ci]")


if __name__ == "__main__":
    main()
