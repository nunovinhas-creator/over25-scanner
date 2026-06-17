#!/usr/bin/env python3
"""
pipeline/scan_over25.py
-----------------------
Scanner automático Over 2.5 — corre a cada 30 min em GitHub Actions.
Chama The Odds API (mercado totals, Pinnacle), aplica pipeline DC+calibrador,
alerta via Telegram. Deduplica por id. Commita picks para main.

Uso: python -m pipeline.scan_over25
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
PICKS_FILE = DATA_DIR / "picks.json"
REJECTED_FILE = DATA_DIR / "rejected_picks.json"
SCAN_STATE_FILE = DATA_DIR / "scan_state_over25.json"

# ── env ────────────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1352687611")

# ── constants ──────────────────────────────────────────────────────────────────
MODEL_WEIGHT = 0.30
MIN_EV = 0.03
MAX_TIMING_H = 6.0
MIN_ODDS = 1.30
MAX_ODDS = 3.50
DRIFTING_THRESHOLD = 0.02    # >2% aumento nas odds → DRIFTING
ODDS_UPDATE_THRESHOLD = 0.03 # >3% mudança → cria pick _update e alerta

# sport_key (The Odds API) → liga canónica
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

def fetch_events(sport_key: str) -> list[dict]:
    """Busca eventos com odds Over/Under 2.5 (Pinnacle) para um sport_key."""
    if not ODDS_API_KEY:
        return []
    url = (
        "https://api.the-odds-api.com/v4/sports/"
        f"{sport_key}/odds/"
        f"?apiKey={ODDS_API_KEY}"
        "&regions=eu&markets=totals&oddsFormat=decimal&bookmakers=pinnacle"
    )
    data = _http_get(url)
    return data if isinstance(data, list) else []


def _extract_ou25(event: dict) -> tuple[float | None, float | None]:
    """Extrai (odds_over, odds_under) para linha 2.5 do primeiro bookmaker."""
    for bm in event.get("bookmakers", []):
        for market in bm.get("markets", []):
            if market.get("key") != "totals":
                continue
            ov = un = None
            for outcome in market.get("outcomes", []):
                try:
                    point = float(outcome.get("point", 0))
                    price = float(outcome.get("price", 0))
                except (TypeError, ValueError):
                    continue
                if abs(point - 2.5) < 0.01:
                    name = (outcome.get("name") or "").lower()
                    if name == "over":
                        ov = price
                    elif name == "under":
                        un = price
            if ov:
                return ov, un
    return None, None


def parse_event(raw: dict, league: str) -> dict | None:
    """Converte evento da Odds API para dicionário interno. None se inválido."""
    commence = raw.get("commence_time", "")
    try:
        ko = datetime.fromisoformat(commence.replace("Z", "+00:00"))
    except Exception:
        return None

    now = datetime.now(timezone.utc)
    timing_h = (ko - now).total_seconds() / 3600.0
    if timing_h < 0:
        return None

    odds_over, odds_under = _extract_ou25(raw)
    if odds_over is None:
        return None

    return {
        "id": raw.get("id", ""),
        "casa": raw.get("home_team", ""),
        "fora": raw.get("away_team", ""),
        "liga": league,
        "data": commence,
        "timing_h": round(timing_h, 2),
        "odds_over": odds_over,
        "odds_under": odds_under,
    }


# ── Pipeline ────────────────────────────────────────────────────────────────────

def _load_calibrator_fn():
    """Carrega calibrador isotónico de data/calibrator.json. None se ausente."""
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
    """Executa pipeline DC+calibrador para um evento. None em caso de erro."""
    try:
        from pipeline.transform import compute_final_probability_dc, normalize_team_names
        return compute_final_probability_dc(
            home=normalize_team_names(ev["casa"]),
            away=normalize_team_names(ev["fora"]),
            league=ev["liga"],
            dc_ratings=dc_ratings,
            calibrator_fn=calibrator_fn,
            odds_over=ev["odds_over"],
            odds_under=ev.get("odds_under"),
            model_weight=MODEL_WEIGHT,
        )
    except Exception as exc:
        print(f"compute_prob falhou ({ev.get('casa')} vs {ev.get('fora')}): {exc}", file=sys.stderr)
        return None


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


def _build_msg(ev: dict, prob: dict, prefix: str = "") -> str:
    p = round(prob["p_final"] * 100, 1)
    pm = round(prob["p_market"] * 100, 1)
    ev_pct = round(prob["ev_final"] * 100, 2)
    src = prob.get("p_model_source", "?")
    lines = [
        f"{prefix}⚽ OVER 2.5 — {ev['liga']}",
        f"{ev['casa']} vs {ev['fora']}",
        f"p_final={p}% | p_market={pm}% [{src}]",
        f"EV={ev_pct:+.2f}% | odds={ev['odds_over']}",
        f"KO em {ev['timing_h']:.1f}h",
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

    for sport_key, league in LEAGUE_SPORT_KEYS.items():
        for raw in fetch_events(sport_key):
            ev = parse_event(raw, league)
            if ev is None:
                continue

            ev_id = ev["id"]

            # Gate 1 — timing
            if ev["timing_h"] > MAX_TIMING_H:
                new_rejected.append({**ev, "reject_reason": "timing_apos_6h", "scanned_at": ts})
                continue

            # Gate 2 — odds band
            ov = ev["odds_over"]
            if ov < MIN_ODDS or ov > MAX_ODDS:
                new_rejected.append({**ev, "reject_reason": "odds_fora_banda", "scanned_at": ts})
                continue

            # Gate 3 — DRIFTING (odds vs última observação)
            prev_state = scan_state.get(ev_id, {})
            last_odds = prev_state.get("odds_over")
            if last_odds:
                change = (ov - float(last_odds)) / float(last_odds)
                if change > DRIFTING_THRESHOLD:
                    movimento = "DRIFTING"
                elif change < -DRIFTING_THRESHOLD:
                    movimento = "SHORTENING"
                else:
                    movimento = "STABLE"
            else:
                movimento = "SHORTENING"
            scan_state[ev_id] = {"odds_over": ov, "updated_at": ts}

            if movimento == "DRIFTING":
                new_rejected.append({**ev, "reject_reason": "odds_drifting", "movimento": movimento, "scanned_at": ts})
                continue

            # Gate 4 — EV (requer pipeline)
            prob = compute_prob(ev, dc_ratings, calibrator_fn)
            if prob is None:
                new_rejected.append({**ev, "reject_reason": "pipeline_error", "scanned_at": ts})
                continue

            if prob["ev_final"] < MIN_EV:
                new_rejected.append({**ev, **prob, "reject_reason": "ev_baixo", "movimento": movimento, "scanned_at": ts})
                continue

            # ── Passou todos os gates ─────────────────────────────────────
            if ev_id in existing_picks:
                # Dedup: verifica se odds mudaram significativamente
                prev_odds = float(existing_picks[ev_id].get("odds_over") or 0)
                if prev_odds and abs(ov - prev_odds) / prev_odds > ODDS_UPDATE_THRESHOLD:
                    update_id = f"{ev_id}_update"
                    if update_id not in existing_picks:
                        pick = {**ev, **prob, "id": update_id, "movimento": movimento,
                                "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": ts}
                        new_picks.append(pick)
                        existing_picks[update_id] = pick
                        send_telegram("🔄 ATUALIZAÇÃO\n" + _build_msg(ev, prob))
                        alerts_sent += 1
                continue

            pick = {**ev, **prob, "movimento": movimento,
                    "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": ts}
            new_picks.append(pick)
            existing_picks[ev_id] = pick
            send_telegram(_build_msg(ev, prob))
            alerts_sent += 1

    # Persiste picks
    _save_list(PICKS_FILE, list(existing_picks.values()))

    # Persiste rejeições (append, dedup por id+ts)
    rej_index = {r.get("id", "") + r.get("scanned_at", ""): r for r in existing_rejected}
    for r in new_rejected:
        rej_index[r.get("id", "") + r.get("scanned_at", "")] = r
    _save_list(REJECTED_FILE, list(rej_index.values()))

    # Persiste estado de scan
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
