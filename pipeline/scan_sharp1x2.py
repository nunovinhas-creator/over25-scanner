#!/usr/bin/env python3
"""
pipeline/scan_sharp1x2.py
-------------------------
Scanner automático Sharp 1X2 — corre a cada 30 min em GitHub Actions.
Chama The Odds API (mercado h2h, Pinnacle + Bet365), calcula div_b365_pin,
aplica _apply_sharp1x2_gates(), alerta via Telegram. Commita picks para main.

Uso: python -m pipeline.scan_sharp1x2
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks_1x2.json"
REJECTED_FILE = DATA_DIR / "rejected_picks_1x2.json"

# ── env ────────────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1352687611")

# ── constants ──────────────────────────────────────────────────────────────────
MAX_TIMING_H = 6.0
MIN_TIMING_H = 0.0
DIV_MIN = 0.03          # div_b365_pin > 3% para alertar
ODDS_UPDATE_THRESHOLD = 0.03

LEAGUE_SPORT_KEYS: dict[str, str] = {
    "soccer_epl":                    "Premier League",
    "soccer_spain_la_liga":          "La Liga",
    "soccer_germany_bundesliga":     "Bundesliga",
    "soccer_italy_serie_a":          "Serie A",
    "soccer_france_ligue_one":       "Ligue 1",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_belgium_first_div":      "Belgian Pro League",
    "soccer_england_championship":   "Championship",
    "soccer_spain_segunda":          "La Liga 2",
    "soccer_germany_bundesliga2":    "Bundesliga 2",
    "soccer_italy_serie_b":          "Serie B",
}

WHITELIST = set(LEAGUE_SPORT_KEYS.values())


# ── Gates — espelho Python de _applySharp1x2Gates() em index.html ──────────────

def apply_sharp1x2_gates(out: str, liga: str, div: float | None, timing_h: float) -> str:
    """
    Porta fiel de _applySharp1x2Gates() (JS, index.html).
    Devolve '' se aprovado, ou o nome da razão de rejeição.
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


# ── HTTP helper ─────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = 15) -> list | dict | None:
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        print(f"HTTP GET failed ({url[:70]}…): {exc}", file=sys.stderr)
        return None


# ── Odds API ────────────────────────────────────────────────────────────────────

def fetch_1x2_events(sport_key: str) -> list[dict]:
    """Busca eventos com odds 1X2 da Pinnacle e Bet365 para um sport_key."""
    if not ODDS_API_KEY:
        return []
    url = (
        "https://api.the-odds-api.com/v4/sports/"
        f"{sport_key}/odds/"
        f"?apiKey={ODDS_API_KEY}"
        "&regions=eu&markets=h2h&oddsFormat=decimal"
        "&bookmakers=pinnacle,bet365"
    )
    data = _http_get(url)
    return data if isinstance(data, list) else []


def _extract_h2h(bookmakers: list[dict], bm_key: str) -> dict[str, float]:
    """Extrai {Home/Draw/Away: odds} para um bookmaker específico."""
    for bm in bookmakers:
        if bm.get("key") != bm_key:
            continue
        for market in bm.get("markets", []):
            if market.get("key") != "h2h":
                continue
            return {
                o["name"]: float(o["price"])
                for o in market.get("outcomes", [])
                if o.get("name") and o.get("price")
            }
    return {}


def parse_1x2_event(raw: dict, league: str, timing_h: float) -> list[dict]:
    """
    Converte evento da Odds API para lista de picks 1X2 (um por outcome).
    div_b365_pin = b365_odds / pinn_odds - 1 (positivo → B365 > Pinnacle).
    """
    bms = raw.get("bookmakers", [])
    pinn = _extract_h2h(bms, "pinnacle")
    b365 = _extract_h2h(bms, "bet365")

    if not pinn or not b365:
        return []

    outcome_map = {"Home": "HOME", "Draw": "DRAW", "Away": "AWAY"}
    picks = []
    for label, out in outcome_map.items():
        pinn_odds = pinn.get(label)
        b365_odds = b365.get(label)
        if not pinn_odds or not b365_odds:
            continue
        div = round(b365_odds / pinn_odds - 1, 6)
        picks.append({
            "event_id": raw.get("id", ""),
            "casa": raw.get("home_team", ""),
            "fora": raw.get("away_team", ""),
            "liga": league,
            "data": raw.get("commence_time", ""),
            "outcome": out,
            "odds_pinnacle": pinn_odds,
            "odds_b365": b365_odds,
            "div_b365_pin": round(div * 100, 4),  # em % para armazenamento
            "_div_raw": div,                        # valor raw para os gates
            "timing_h": round(timing_h, 2),
        })
    return picks


# ── Telegram ────────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    if not TG_TOKEN:
        print("TELEGRAM_TOKEN não definido — skip TG", file=sys.stderr)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TG_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"TG enviado (status {resp.status})")
    except Exception as exc:
        print(f"TG falhou (não fatal): {exc}", file=sys.stderr)


def _build_msg(p: dict, prefix: str = "") -> str:
    div_pct = round(p["_div_raw"] * 100, 2)
    lines = [
        f"{prefix}🔵 SHARP 1X2 — {p['liga']}",
        f"{p['casa']} vs {p['fora']}",
        f"Outcome: {p['outcome']} | odds={p['odds_b365']} (B365) / {p['odds_pinnacle']} (Pin)",
        f"div_b365_pin={div_pct:+.2f}% | KO em {p['timing_h']:.1f}h",
    ]
    return "\n".join(lines)


# ── Git ─────────────────────────────────────────────────────────────────────────

def git_commit_push(files: list[str], msg: str) -> None:
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
        subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
        subprocess.run(["git", "fetch", "origin", "main"], check=True)
        subprocess.run(["git", "reset", "--soft", "origin/main"], check=True)
        subprocess.run(["git", "add"] + files, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            print(f"Commit feito: {msg}")
        else:
            print("Sem alterações para commitar.")
    except subprocess.CalledProcessError as exc:
        print(f"git commit/push falhou: {exc}", file=sys.stderr)


# ── I/O helpers ─────────────────────────────────────────────────────────────────

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
    if not ODDS_API_KEY:
        print("ODDS_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    existing_picks: dict[str, dict] = {p["id"]: p for p in _load_list(PICKS_FILE)}
    existing_rejected: list[dict] = _load_list(REJECTED_FILE)

    new_picks: list[dict] = []
    new_rejected: list[dict] = []
    alerts_sent = 0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for sport_key, league in LEAGUE_SPORT_KEYS.items():
        for raw in fetch_1x2_events(sport_key):
            commence = raw.get("commence_time", "")
            try:
                ko = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                timing_h = (ko - datetime.now(timezone.utc)).total_seconds() / 3600.0
            except Exception:
                continue
            if timing_h < 0:
                continue

            for candidate in parse_1x2_event(raw, league, timing_h):
                out = candidate["outcome"]
                div_raw = candidate["_div_raw"]

                gate_reason = apply_sharp1x2_gates(
                    out=out,
                    liga=league,
                    div=div_raw,
                    timing_h=timing_h,
                )

                # ID único: event_id + outcome (ex: "abc123_away_sh")
                pick_id = f"{candidate['event_id']}_{out.lower()}_sh"

                base_pick = {
                    "id": pick_id,
                    "casa": candidate["casa"],
                    "fora": candidate["fora"],
                    "liga": league,
                    "data": commence,
                    "outcome": out,
                    "odds_entrada": candidate["odds_b365"],
                    "odds_pinnacle": candidate["odds_pinnacle"],
                    "div_b365_pin": candidate["div_b365_pin"],
                    "timing_h": candidate["timing_h"],
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

                # Aprovado — dedup
                if pick_id in existing_picks:
                    prev_odds = float(existing_picks[pick_id].get("odds_entrada") or 0)
                    cur_odds = candidate["odds_b365"]
                    if prev_odds and abs(cur_odds - prev_odds) / prev_odds > ODDS_UPDATE_THRESHOLD:
                        update_id = f"{pick_id}_update"
                        if update_id not in existing_picks:
                            upd = {**base_pick, "id": update_id}
                            new_picks.append(upd)
                            existing_picks[update_id] = upd
                            send_telegram("🔄 ATUALIZAÇÃO\n" + _build_msg(candidate))
                            alerts_sent += 1
                    continue

                new_picks.append(base_pick)
                existing_picks[pick_id] = base_pick
                send_telegram(_build_msg(candidate))
                alerts_sent += 1

    # Persiste
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
