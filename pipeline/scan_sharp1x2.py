#!/usr/bin/env python3
"""
pipeline/scan_sharp1x2.py
-------------------------
Scanner automático Sharp 1X2 — corre a cada 30 min em GitHub Actions.
Usa BSD Sports API (BSD_API_KEY) via pipeline.extract.fetch_bsd_events.
Calcula div_b365_pin por outcome, aplica gates, alerta via Telegram.

Uso: python -m pipeline.scan_sharp1x2
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks_1x2.json"
REJECTED_FILE = DATA_DIR / "rejected_picks_1x2.json"

# ── env ────────────────────────────────────────────────────────────────────────
BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1352687611")

# ── constants ──────────────────────────────────────────────────────────────────
MAX_TIMING_H = 6.0
MIN_TIMING_H = 0.0
DIV_MIN = 0.03
ODDS_UPDATE_THRESHOLD = 0.03

WHITELIST = {
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2",
    # Bundesliga 2 e Serie B ausentes da BSD API — não geram picks em produção.
}

# Mapa defensivo BSD league_id → nome canónico.
# Fail-closed: ID desconhecido → '' → WHITELIST rejeita.
BSD_LEAGUE_ID_MAP: dict[int, str] = {
    # Nomes canónicos (whitelist) — têm prioridade sobre league_name da BSD.
    # BSD devolve nomes diferentes: id=2→"Liga Portugal Betclic", id=14→"Pro League", id=38→"Segunda División".
    1: "Premier League", 2: "Primeira Liga", 3: "La Liga", 4: "Serie A",
    5: "Bundesliga", 6: "Ligue 1", 10: "Eredivisie",
    12: "Championship", 14: "Belgian Pro League", 38: "La Liga 2",
    # Bundesliga 2 e Serie B: ausentes da BSD (65 ligas disponíveis, nenhuma corresponde).
}


# ── Gates — espelho fiel de _applySharp1x2Gates() em index.html ────────────────

def apply_sharp1x2_gates(out: str, liga: str, div: float | None, timing_h: float) -> str:
    """
    Porta Python de _applySharp1x2Gates() (JS, index.html).
    Devolve '' se aprovado, ou o nome do motivo de rejeição.
    """
    _out = (out or "").upper()
    _liga = (liga or "").strip()
    is_n1 = _liga == "Eredivisie"

    if not _liga or _liga not in WHITELIST:
        return "liga_fora_whitelist"
    if _out == "DRAW" and is_n1 and div is not None and div >= DIV_MIN:
        return "draw_observacao_n1"
    if _out == "DRAW":
        return "draw_suspenso"
    if _out == "HOME" and is_n1:
        return "n1_home_negativo"
    if not (MIN_TIMING_H <= timing_h <= MAX_TIMING_H):
        return "timing_apos_6h"
    if div is None or div < DIV_MIN:
        return "div_baixa"
    return ""


# ── BSD fetch ───────────────────────────────────────────────────────────────────

def _fetch_all_events() -> list[dict]:
    """Busca eventos BSD para hoje+amanhã e faz join com odds 1X2."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    base = "https://sports.bzzoiro.com"
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}

    def _get_list(path: str) -> list:
        try:
            url = path if path.startswith("http") else base + path
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list):
                return payload, None
            return payload.get("results") or payload.get("data") or [], payload.get("next")
        except Exception as exc:
            print(f"BSD fetch {path}: {exc}", file=sys.stderr)
            return [], None

    events, _ = _get_list(
        f"/api/v2/events/?status=notstarted&date_from={today}&date_to={tomorrow}&limit=200"
    )

    # Fetch 1X2 odds (paginated)
    odds_1x2: list[dict] = []
    next_url: str | None = (
        f"/api/v2/odds/?market=1x2&limit=200&updated_after={today}T00:00:00Z"
    )
    for _ in range(10):
        if not next_url:
            break
        page, next_url = _get_list(next_url)
        odds_1x2.extend(page)
        if not page:
            break

    # Group 1X2 odds by event_id → outcome → bookmaker
    pins: dict[str, dict[str, float]] = {}   # eid → {HOME: x, DRAW: y, AWAY: z}
    b365s: dict[str, dict[str, float]] = {}

    for o in odds_1x2:
        eid = str(o.get("event_id") or "")
        if not eid:
            continue
        slug = o.get("bookmaker_slug", "")
        raw_out = (o.get("outcome") or "").upper()
        out = (
            "HOME" if raw_out in ("HOME", "HOME_WIN") else
            "DRAW" if raw_out == "DRAW" else
            "AWAY" if raw_out in ("AWAY", "AWAY_WIN") else ""
        )
        if not out:
            continue
        price = float(o.get("decimal_odds") or 0)
        if not price:
            continue
        if slug == "pinnacle":
            pins.setdefault(eid, {})[out] = price
        elif slug in ("bet365", "bet-365"):
            b365s.setdefault(eid, {})[out] = price

    # Merge events with 1X2 odds, normalising BSD field names
    result = []
    for ev in events:
        eid = str(ev.get("id") or ev.get("event_id") or "")
        if not eid:
            continue
        entry: dict = {
            "event_id": eid,
            "home": ev.get("home_team") or ev.get("home", ""),
            "away": ev.get("away_team") or ev.get("away", ""),
            "league": BSD_LEAGUE_ID_MAP.get(ev.get("league_id") or 0) or ev.get("league_name") or ev.get("league", ""),
            "date": ev.get("event_date") or ev.get("date", ""),
        }
        pin = pins.get(eid, {})
        b3 = b365s.get(eid, {})
        if pin:
            entry.update({
                "pinnacle_home": pin.get("HOME"),
                "pinnacle_draw": pin.get("DRAW"),
                "pinnacle_away": pin.get("AWAY"),
            })
        if b3:
            entry.update({
                "b365_home": b3.get("HOME"),
                "b365_draw": b3.get("DRAW"),
                "b365_away": b3.get("AWAY"),
            })
        result.append(entry)
    return result


def _extract_1x2_odds(ev: dict) -> dict[str, dict[str, float]]:
    """
    Extrai odds 1X2 da Pinnacle e B365 de um evento BSD.
    Retorna {'pinnacle': {'HOME': x, 'DRAW': y, 'AWAY': z}, 'b365': {...}}
    ou dicts vazios se os campos não estiverem presentes.
    """
    pinn = {}
    b365 = {}

    ph = ev.get("pinnacle_home")
    pd_ = ev.get("pinnacle_draw")
    pa = ev.get("pinnacle_away")
    if ph and pd_ and pa:
        pinn = {"HOME": float(ph), "DRAW": float(pd_), "AWAY": float(pa)}

    bh = ev.get("b365_home")
    bd = ev.get("b365_draw")
    ba = ev.get("b365_away")
    if bh and bd and ba:
        b365 = {"HOME": float(bh), "DRAW": float(bd), "AWAY": float(ba)}

    return {"pinnacle": pinn, "b365": b365}


# ── Telegram ────────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    if not TG_TOKEN:
        print("TG_TOKEN não definido — skip TG", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, method="POST"), timeout=10
        ) as resp:
            print(f"TG enviado (status {resp.status})")
    except Exception as exc:
        print(f"TG falhou (não fatal): {exc}", file=sys.stderr)


def _build_msg(pick: dict, div_raw: float, prefix: str = "") -> str:
    div_pct = round(div_raw * 100, 2)
    return "\n".join([
        f"{prefix}🔵 SHARP 1X2 — {pick['liga']}",
        f"{pick['casa']} vs {pick['fora']}",
        f"Outcome: {pick['outcome']} | B365={pick['odds_entrada']} / Pin={pick['odds_pinnacle']}",
        f"div_b365_pin={div_pct:+.2f}% | KO em {pick['timing_h']:.1f}h",
    ])


# ── Git ─────────────────────────────────────────────────────────────────────────

def git_commit_push(files: list[str], msg: str) -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=True)
        subprocess.run(["git", "add"] + files, check=True)
        if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print(f"Commit feito: {msg}")
        else:
            print("Sem alterações para commitar.")
    except subprocess.CalledProcessError as exc:
        print(f"git commit/push falhou: {exc}", file=sys.stderr)


# ── I/O ─────────────────────────────────────────────────────────────────────────

def _load_list(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        d = json.loads(path.read_text())
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_list(path: Path, data: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Main scan ───────────────────────────────────────────────────────────────────

def scan() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    existing_picks: dict[str, dict] = {p["id"]: p for p in _load_list(PICKS_FILE)}
    existing_rejected: list[dict] = _load_list(REJECTED_FILE)

    new_picks: list[dict] = []
    new_rejected: list[dict] = []
    alerts_sent = 0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_events: set[str] = set()

    for raw in _fetch_all_events():
        event_id = str(raw.get("event_id") or raw.get("id", ""))
        if not event_id or event_id in seen_events:
            continue
        seen_events.add(event_id)

        casa = raw.get("home") or raw.get("home_team", "")
        fora = raw.get("away") or raw.get("away_team", "")
        liga = raw.get("league") or raw.get("liga", "")
        commence = raw.get("date") or raw.get("commence_time", "")

        try:
            ko = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            timing_h = (ko - datetime.now(timezone.utc)).total_seconds() / 3600.0
        except Exception:
            continue
        if timing_h < 0:
            continue

        odds = _extract_1x2_odds(raw)
        pinn = odds["pinnacle"]
        b365 = odds["b365"]

        if not pinn or not b365:
            continue

        for out in ("HOME", "DRAW", "AWAY"):
            pinn_odds = pinn.get(out)
            b365_odds = b365.get(out)
            if not pinn_odds or not b365_odds:
                continue

            div_raw = b365_odds / pinn_odds - 1

            gate_reason = apply_sharp1x2_gates(
                out=out, liga=liga, div=div_raw, timing_h=timing_h,
            )

            pick_id = f"{event_id}_{out.lower()}_sh"

            base_pick = {
                "id": pick_id,
                "casa": casa,
                "fora": fora,
                "liga": liga,
                "data": commence,
                "outcome": out,
                "odds_entrada": b365_odds,
                "odds_pinnacle": pinn_odds,
                "div_b365_pin": round(div_raw * 100, 4),
                "timing_h": round(timing_h, 2),
                "gate_blocked_reason": gate_reason,
                "resultado_outcome": "",
                "clv": "",
                "odds_fecho": "",
                "saved_at": ts,
                "fonte": "auto-scan",
            }

            if gate_reason:
                new_rejected.append({**base_pick, "reject_reason": gate_reason})
                continue

            if pick_id in existing_picks:
                prev_odds = float(existing_picks[pick_id].get("odds_entrada") or 0)
                if prev_odds and abs(b365_odds - prev_odds) / prev_odds > ODDS_UPDATE_THRESHOLD:
                    update_id = f"{pick_id}_update"
                    if update_id not in existing_picks:
                        upd = {**base_pick, "id": update_id}
                        new_picks.append(upd)
                        existing_picks[update_id] = upd
                        send_telegram("🔄 ATUALIZAÇÃO\n" + _build_msg(base_pick, div_raw))
                        alerts_sent += 1
                continue

            new_picks.append(base_pick)
            existing_picks[pick_id] = base_pick
            send_telegram(_build_msg(base_pick, div_raw))
            alerts_sent += 1

    _save_list(PICKS_FILE, list(existing_picks.values()))

    rej_index = {r.get("id", "") + r.get("saved_at", ""): r for r in existing_rejected}
    for r in new_rejected:
        rej_index[r.get("id", "") + r.get("saved_at", "")] = r
    _save_list(REJECTED_FILE, list(rej_index.values()))

    print(
        f"Sharp 1X2 scan: {len(new_picks)} novos picks | "
        f"{len(new_rejected)} rejeitados | {alerts_sent} TG enviados"
    )

    git_commit_push(
        [str(PICKS_FILE), str(REJECTED_FILE)],
        f"auto-scan sharp1x2 {ts} [skip ci]",
    )


if __name__ == "__main__":
    scan()
