#!/usr/bin/env python3
"""
pipeline/scan_over25.py
-----------------------
Scanner automático Over 2.5 — corre a cada 30 min em GitHub Actions.
Usa BSD Sports API (BSD_API_KEY) via pipeline.extract.fetch_bsd_events.
Aplica pipeline DC+calibrador, alerta via Telegram. Deduplica por id.

Uso: python -m pipeline.scan_over25
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
PICKS_FILE = DATA_DIR / "picks.json"
REJECTED_FILE = DATA_DIR / "rejected_picks.json"
SCAN_STATE_FILE = DATA_DIR / "scan_state_over25.json"

# ── env ────────────────────────────────────────────────────────────────────────
BSD_API_KEY = os.environ.get("BSD_API_KEY", "")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "1352687611")

# ── constants ──────────────────────────────────────────────────────────────────
MODEL_WEIGHT = 0.30
MIN_EV = 0.03
MAX_TIMING_H = 6.0
MIN_ODDS = 1.30
MAX_ODDS = 3.50
ODDS_UPDATE_THRESHOLD = 0.03  # >3% mudança nas odds → cria pick _update

WHITELIST = {
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2", "Bundesliga 2", "Serie B",
}


# ── BSD fetch ───────────────────────────────────────────────────────────────────

def _fetch_all_events() -> list[dict]:
    """Busca eventos BSD para hoje+amanhã e faz join com odds over/under."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    base = "https://sports.bzzoiro.com"
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}

    def _get(path: str) -> list:
        try:
            r = requests.get(base + path, headers=headers, timeout=30)
            r.raise_for_status()
            payload = r.json()
            if isinstance(payload, list):
                return payload
            return payload.get("results") or payload.get("data") or []
        except Exception as exc:
            print(f"BSD fetch {path}: {exc}", file=sys.stderr)
            return []

    events = _get(
        f"/api/v2/events/?status=notstarted&date_from={today}&date_to={tomorrow}&limit=200"
    )
    odds_over_raw = _get(
        f"/api/v2/odds/?market=over_under_25&outcome=over&limit=200&updated_after={today}T00:00:00Z"
    )
    odds_under_raw = _get(
        f"/api/v2/odds/?market=over_under_25&outcome=under&limit=200&updated_after={today}T00:00:00Z"
    )

    # Build maps: event_id → best decimal odds + movement
    ov_map: dict[str, float] = {}
    un_map: dict[str, float] = {}
    mov_map: dict[str, str] = {}

    for o in odds_over_raw:
        eid = str(o.get("event_id") or "")
        if not eid:
            continue
        price = float(o.get("decimal_odds") or 0)
        if not price:
            continue
        # prefer consensus/max-quote entry; otherwise first seen
        is_ref = o.get("bookmaker_slug") == "oddssafari-consensus" or o.get("is_max_quote")
        if is_ref or eid not in ov_map:
            ov_map[eid] = price
        if o.get("movement") and eid not in mov_map:
            mov_map[eid] = str(o["movement"]).upper()

    for o in odds_under_raw:
        eid = str(o.get("event_id") or "")
        if not eid:
            continue
        price = float(o.get("decimal_odds") or 0)
        if not price:
            continue
        is_ref = o.get("bookmaker_slug") == "oddssafari-consensus" or o.get("is_max_quote")
        if is_ref or eid not in un_map:
            un_map[eid] = price

    # Merge events with odds, normalising BSD field names
    result = []
    for ev in events:
        eid = str(ev.get("id") or ev.get("event_id") or "")
        if not eid:
            continue
        result.append({
            "event_id": eid,
            "home": ev.get("home_team") or ev.get("home", ""),
            "away": ev.get("away_team") or ev.get("away", ""),
            "league": ev.get("league_name") or ev.get("league", ""),
            "date": ev.get("event_date") or ev.get("date", ""),
            "odds_over": ov_map.get(eid),
            "odds_under": un_map.get(eid),
            "movement": mov_map.get(eid, "SHORTENING"),
        })
    return result


def _event_fields(ev: dict) -> dict:
    """Normaliza campos do BSD (trata aliases para robustez)."""
    return {
        "id": str(ev.get("event_id") or ev.get("id", "")),
        "casa": ev.get("home") or ev.get("home_team", ""),
        "fora": ev.get("away") or ev.get("away_team", ""),
        "liga": ev.get("league") or ev.get("liga", ""),
        "data": ev.get("date") or ev.get("commence_time", ""),
        "odds_over": ev.get("odds_over"),
        "odds_under": ev.get("odds_under"),
        "movimento": (ev.get("movement") or "SHORTENING").upper(),
    }


# ── Pipeline ────────────────────────────────────────────────────────────────────

def _load_calibrator_fn():
    p = DATA_DIR / "calibrator.json"
    if not p.exists():
        return None
    try:
        import numpy as np
        d = json.loads(p.read_text())
        if d.get("method") == "isotonic":
            x = np.array(d["x_thresholds"], dtype=np.float64)
            y = np.array(d["y_thresholds"], dtype=np.float64)
            return lambda arr: np.clip(
                np.interp(np.asarray(arr, dtype=np.float64), x, y), 1e-6, 1 - 1e-6
            )
    except Exception as exc:
        print(f"Calibrador não carregado: {exc}", file=sys.stderr)
    return None


def _load_dc_ratings() -> dict:
    p = DATA_DIR / "dc_ratings.json"
    return json.loads(p.read_text()) if p.exists() else {}


def compute_prob(ev: dict, dc_ratings: dict, calibrator_fn) -> dict | None:
    """Executa pipeline DC+calibrador. None em caso de erro."""
    try:
        from pipeline.transform import compute_final_probability_dc, normalize_team_names
        return compute_final_probability_dc(
            home=normalize_team_names(ev["casa"]),
            away=normalize_team_names(ev["fora"]),
            league=ev["liga"],
            dc_ratings=dc_ratings,
            calibrator_fn=calibrator_fn,
            odds_over=float(ev["odds_over"]),
            odds_under=float(ev["odds_under"]) if ev.get("odds_under") else None,
            model_weight=MODEL_WEIGHT,
        )
    except Exception as exc:
        print(f"compute_prob: {ev.get('casa')} vs {ev.get('fora')}: {exc}", file=sys.stderr)
        return None


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


def _build_msg(ev: dict, prob: dict, prefix: str = "") -> str:
    p = round(prob["p_final"] * 100, 1)
    pm = round(prob["p_market"] * 100, 1)
    ev_pct = round(prob["ev_final"] * 100, 2)
    src = prob.get("p_model_source", "?")
    return "\n".join([
        f"{prefix}⚽ OVER 2.5 — {ev['liga']}",
        f"{ev['casa']} vs {ev['fora']}",
        f"p_final={p}% | p_market={pm}% [{src}]",
        f"EV={ev_pct:+.2f}% | odds={ev['odds_over']}",
        f"KO em {ev['timing_h']:.1f}h",
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

    dc_ratings = _load_dc_ratings()
    calibrator_fn = _load_calibrator_fn()
    existing_picks: dict[str, dict] = {p["id"]: p for p in _load_list(PICKS_FILE)}
    existing_rejected: list[dict] = _load_list(REJECTED_FILE)
    scan_state: dict = (
        json.loads(SCAN_STATE_FILE.read_text()) if SCAN_STATE_FILE.exists() else {}
    )

    new_picks: list[dict] = []
    new_rejected: list[dict] = []
    alerts_sent = 0
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seen_ids: set[str] = set()

    for raw in _fetch_all_events():
        ev = _event_fields(raw)
        ev_id = ev["id"]
        if not ev_id or ev_id in seen_ids:
            continue
        seen_ids.add(ev_id)

        # Gate 0 — liga whitelist
        if ev["liga"] not in WHITELIST:
            new_rejected.append({**ev, "reject_reason": "liga_fora_whitelist", "scanned_at": ts})
            continue

        # Gate 1 — timing
        try:
            ko = datetime.fromisoformat(ev["data"].replace("Z", "+00:00"))
            timing_h = (ko - datetime.now(timezone.utc)).total_seconds() / 3600.0
        except Exception:
            continue
        if timing_h < 0 or timing_h > MAX_TIMING_H:
            if timing_h > MAX_TIMING_H:
                new_rejected.append({**ev, "timing_h": round(timing_h, 2), "reject_reason": "timing_apos_6h", "scanned_at": ts})
            continue
        ev["timing_h"] = round(timing_h, 2)

        # Gate 2 — odds band
        if not ev.get("odds_over"):
            continue
        ov = float(ev["odds_over"])
        if ov < MIN_ODDS or ov > MAX_ODDS:
            new_rejected.append({**ev, "reject_reason": "odds_fora_banda", "scanned_at": ts})
            continue

        # Gate 3 — DRIFTING (BSD fornece movement diretamente)
        movimento = ev["movimento"]
        scan_state[ev_id] = {"odds_over": ov, "movimento": movimento, "updated_at": ts}
        if movimento == "DRIFTING":
            new_rejected.append({**ev, "reject_reason": "odds_drifting", "scanned_at": ts})
            continue

        # Gate 4 — EV (pipeline DC+calibrador)
        prob = compute_prob(ev, dc_ratings, calibrator_fn)
        if prob is None:
            new_rejected.append({**ev, "reject_reason": "pipeline_error", "scanned_at": ts})
            continue
        if prob["ev_final"] < MIN_EV:
            new_rejected.append({**ev, **prob, "reject_reason": "ev_baixo", "scanned_at": ts})
            continue

        # ── Passou todos os gates ─────────────────────────────────────────────
        if ev_id in existing_picks:
            prev_odds = float(existing_picks[ev_id].get("odds_over") or 0)
            if prev_odds and abs(ov - prev_odds) / prev_odds > ODDS_UPDATE_THRESHOLD:
                update_id = f"{ev_id}_update"
                if update_id not in existing_picks:
                    pick = {**ev, **prob, "id": update_id,
                            "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": ts}
                    new_picks.append(pick)
                    existing_picks[update_id] = pick
                    send_telegram("🔄 ATUALIZAÇÃO\n" + _build_msg(ev, prob))
                    alerts_sent += 1
            continue

        pick = {**ev, **prob, "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": ts}
        new_picks.append(pick)
        existing_picks[ev_id] = pick
        send_telegram(_build_msg(ev, prob))
        alerts_sent += 1

    _save_list(PICKS_FILE, list(existing_picks.values()))

    rej_index = {r.get("id", "") + r.get("scanned_at", ""): r for r in existing_rejected}
    for r in new_rejected:
        rej_index[r.get("id", "") + r.get("scanned_at", "")] = r
    _save_list(REJECTED_FILE, list(rej_index.values()))

    SCAN_STATE_FILE.write_text(json.dumps(scan_state, indent=2, ensure_ascii=False))

    print(
        f"Over 2.5 scan: {len(new_picks)} novos picks | "
        f"{len(new_rejected)} rejeitados | {alerts_sent} TG enviados"
    )

    git_commit_push(
        [str(PICKS_FILE), str(REJECTED_FILE), str(SCAN_STATE_FILE)],
        f"auto-scan over25 {ts} [skip ci]",
    )


if __name__ == "__main__":
    scan()
