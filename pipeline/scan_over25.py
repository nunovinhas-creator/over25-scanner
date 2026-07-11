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
BTTS_O25_FILE = DATA_DIR / "picks_btts_over25.json"

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
BTTS_O25_OVERLAY_MIN = 0.08   # overlay mínimo (gate antigo, comentado — mantido para referência)
CLV_BTTS_O25_MIN    = 0.05   # CLV real mínimo: p_dc_conjunta/(p_btts×p_o25_mkt)−1 ≥ 5%

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


# ── BSD fetch ───────────────────────────────────────────────────────────────────

def _is_consensus(slug: str | None) -> bool:
    """Entrada sintética de consenso da BSD (média dos bookmakers).

    O OpenAPI spec documenta o slug como ``consensus``; versões anteriores
    usavam ``oddssafari-consensus``. Match por substring cobre ambos.
    """
    return "consensus" in (slug or "")


def _fetch_all_events() -> list[dict]:
    """Busca eventos BSD para hoje+amanhã e faz join com odds over/under."""
    today = date.today().isoformat()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    base = "https://sports.bzzoiro.com"
    headers = {"Authorization": f"Token {BSD_API_KEY}", "Accept": "application/json"}

    def _get(path: str, max_pages: int = 10) -> list:
        # Segue o campo `next` do wrapper v2 (count/next/previous/results) —
        # 200 tuplos (evento × bookmaker) por página não chegam num dia cheio.
        records: list = []
        url: str | None = base + path
        for _ in range(max_pages):
            if not url:
                break
            try:
                r = requests.get(url, headers=headers, timeout=30)
                r.raise_for_status()
                payload = r.json()
            except Exception as exc:
                print(f"BSD fetch {path}: {exc}", file=sys.stderr)
                break
            if isinstance(payload, list):
                records.extend(payload)
                break
            page = payload.get("results") or payload.get("data") or []
            records.extend(page)
            url = payload.get("next")
            if url and url.startswith("/"):
                url = base + url
            if not page:
                break
        return records

    events = _get(
        f"/api/v2/events/?status=notstarted&date_from={today}&date_to={tomorrow}&limit=200"
    )
    odds_over_raw = _get(
        f"/api/v2/odds/?market=over_under_25&outcome=over&limit=200&updated_after={today}T00:00:00Z"
    )
    odds_under_raw = _get(
        f"/api/v2/odds/?market=over_under_25&outcome=under&limit=200&updated_after={today}T00:00:00Z"
    )
    odds_btts_yes_raw = _get(
        f"/api/v2/odds/?market=btts&outcome=yes&limit=200&updated_after={today}T00:00:00Z"
    )
    odds_btts_no_raw = _get(
        f"/api/v2/odds/?market=btts&outcome=no&limit=200&updated_after={today}T00:00:00Z"
    )
    # Predictions CatBoost da BSD — campo informativo por pick (não é gate).
    predictions_raw = _get(
        f"/api/v2/predictions/?date_from={today}&date_to={tomorrow}&limit=200"
    )

    # event_id → (prob_over_25, prob_btts_yes) do modelo CatBoost da BSD
    ml_map: dict[str, tuple[float | None, float | None]] = {}
    for pr in predictions_raw:
        pr_ev = pr.get("event") or {}
        pid = str(pr_ev.get("id") or "")
        if not pid:
            continue
        markets = pr.get("markets") or {}
        ou = markets.get("over_under") or {}
        bt = markets.get("btts") or {}
        ml_map[pid] = (ou.get("prob_over_25"), bt.get("prob_yes"))

    # Build maps: event_id → odds de referência + movement
    # Prioridade da referência: consensus > is_max_quote > primeiro visto.
    # (max_quote é a melhor odd do mercado — usá-la como p_market infla o EV,
    #  por isso só serve de fallback quando não há linha de consenso.)
    ov_map: dict[str, float] = {}
    un_map: dict[str, float] = {}
    ov_rank: dict[str, int] = {}
    un_rank: dict[str, int] = {}
    mov_map: dict[str, str] = {}
    # BTTS: eid → {slug: odds} para yes e no separadamente
    btts_yes_by_bk: dict[str, dict[str, float]] = {}
    btts_no_by_bk:  dict[str, dict[str, float]] = {}

    for o in odds_over_raw:
        eid = str(o.get("event_id") or "")
        if not eid:
            continue
        price = float(o.get("decimal_odds") or 0)
        if not price:
            continue
        rank = 2 if _is_consensus(o.get("bookmaker_slug")) else 1 if o.get("is_max_quote") else 0
        if rank > ov_rank.get(eid, -1):
            ov_map[eid] = price
            ov_rank[eid] = rank
        if o.get("movement") and eid not in mov_map:
            mov_map[eid] = str(o["movement"]).upper()

    for o in odds_under_raw:
        eid = str(o.get("event_id") or "")
        if not eid:
            continue
        price = float(o.get("decimal_odds") or 0)
        if not price:
            continue
        rank = 2 if _is_consensus(o.get("bookmaker_slug")) else 1 if o.get("is_max_quote") else 0
        if rank > un_rank.get(eid, -1):
            un_map[eid] = price
            un_rank[eid] = rank

    for o in odds_btts_yes_raw:
        eid = str(o.get("event_id") or "")
        price = float(o.get("decimal_odds") or 0)
        slug = (o.get("bookmaker_slug") or "").strip()
        if eid and price and slug:
            btts_yes_by_bk.setdefault(eid, {})[slug] = price

    for o in odds_btts_no_raw:
        eid = str(o.get("event_id") or "")
        price = float(o.get("decimal_odds") or 0)
        slug = (o.get("bookmaker_slug") or "").strip()
        if eid and price and slug:
            btts_no_by_bk.setdefault(eid, {})[slug] = price

    # Merge events with odds, normalising BSD field names
    result = []
    for ev in events:
        eid = str(ev.get("id") or ev.get("event_id") or "")
        if not eid:
            continue

        # Seleccionar odds BTTS: Pinnacle se disponível, senão bookie com odds mais baixas
        yes_bk = btts_yes_by_bk.get(eid, {})
        no_bk  = btts_no_by_bk.get(eid, {})
        if "pinnacle" in yes_bk:
            bk_btts = "pinnacle"
        elif yes_bk:
            bk_btts = min(yes_bk, key=lambda b: yes_bk[b])  # odds mais baixas = maior p implícita
        else:
            bk_btts = ""
        odds_btts_yes = yes_bk.get(bk_btts) if bk_btts else None
        # No: mesmo bookmaker se disponível, senão qualquer um
        odds_btts_no  = no_bk.get(bk_btts) or (next(iter(no_bk.values()), None) if no_bk else None)

        # H2H agregado vem embutido no próprio evento (EventDetailV2Schema) —
        # zero chamadas extra. Campos informativos, não são gate.
        h2h = ev.get("head_to_head") or {}
        prob_o25_ml, prob_btts_ml = ml_map.get(eid, (None, None))

        result.append({
            "event_id":      eid,
            "home":          ev.get("home_team") or ev.get("home", ""),
            "away":          ev.get("away_team") or ev.get("away", ""),
            "league":        BSD_LEAGUE_ID_MAP.get(ev.get("league_id") or 0) or ev.get("league_name") or ev.get("league", ""),
            "date":          ev.get("event_date") or ev.get("date", ""),
            "odds_over":     ov_map.get(eid),
            "odds_under":    un_map.get(eid),
            "movement":      mov_map.get(eid, "SHORTENING"),
            "odds_btts_yes": odds_btts_yes,
            "odds_btts_no":  odds_btts_no,
            "bookmaker_btts": bk_btts,
            "h2h_matches":   h2h.get("total_matches") if isinstance(h2h, dict) else None,
            "h2h_avg_goals": h2h.get("avg_total_goals") if isinstance(h2h, dict) else None,
            "prob_over25_ml": prob_o25_ml,
            "prob_btts_ml":  prob_btts_ml,
        })
    return result


def _event_fields(ev: dict) -> dict:
    """Normaliza campos do BSD (trata aliases para robustez)."""
    return {
        "id":              str(ev.get("event_id") or ev.get("id", "")),
        "casa":            ev.get("home") or ev.get("home_team", ""),
        "fora":            ev.get("away") or ev.get("away_team", ""),
        "liga":            ev.get("league") or ev.get("liga", ""),
        "data":            ev.get("date") or ev.get("commence_time", ""),
        "odds_over":       ev.get("odds_over"),
        "odds_under":      ev.get("odds_under"),
        "movimento":       (ev.get("movement") or "SHORTENING").upper(),
        "odds_btts_yes":   ev.get("odds_btts_yes"),
        "odds_btts_no":    ev.get("odds_btts_no"),
        "bookmaker_btts":  ev.get("bookmaker_btts", ""),
        # Campos informativos da BSD API (não são gates)
        "h2h_matches":     ev.get("h2h_matches"),
        "h2h_avg_goals":   ev.get("h2h_avg_goals"),
        "prob_over25_ml":  ev.get("prob_over25_ml"),
        "prob_btts_ml":    ev.get("prob_btts_ml"),
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


def _fetch_lineup_info(ev_id: str) -> dict:
    """Resumo de indisponíveis (lesões/suspensões) via BSD lineups.

    Chamado apenas para eventos que passam todos os gates (1 chamada por pick
    novo). Campos informativos — não são gate. Fail-safe: {} em erro.
    """
    try:
        from pipeline.extract import fetch_event_lineups, summarize_lineups
        return summarize_lineups(fetch_event_lineups(BSD_API_KEY, ev_id))
    except Exception as exc:
        print(f"_fetch_lineup_info: {ev_id}: {exc}", file=sys.stderr)
        return {}


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


# ── BTTS+Over 2.5 joint probability ─────────────────────────────────────────────

def _compute_btts_over25(
    casa: str,
    fora: str,
    liga: str,
    dc_ratings: dict,
    odds_btts_yes: float | None = None,
    odds_btts_no:  float | None = None,
    bookmaker_btts: str = "",
    p_over25_market: float | None = None,
) -> dict | None:
    """
    Compute P(BTTS AND Over 2.5) from the DC bivariate grid, e CLV real vs mercado.

    clv_btts_over25 = p_dc_conjunta / (p_btts_market × p_over25_market) − 1

    Parâmetros de mercado:
        odds_btts_yes / odds_btts_no — odds BSD do market=btts
        p_over25_market              — probabilidade de-vigged Over 2.5 (de compute_prob)
    """
    try:
        import numpy as np
        from pipeline.transform import normalize_team_names
        from models.math.poisson import build_dc_grid, extract_btts_over25_prob, prob_over25_poisson

        # ── DC bivariate grid ────────────────────────────────────────────────
        league_data = dc_ratings.get(liga)
        if not league_data:
            return None
        teams = league_data.get("teams", {})
        home_data = teams.get(normalize_team_names(casa))
        away_data = teams.get(normalize_team_names(fora))
        if not home_data or not away_data:
            return None

        lambda_h = float(np.exp(home_data["attack"] + away_data["defence"] + league_data["home_adv"]))
        lambda_a = float(np.exp(away_data["attack"] + home_data["defence"]))
        rho      = float(league_data.get("rho", 0.0))

        grid          = build_dc_grid(lambda_h, lambda_a, rho=rho)
        p_dc_conjunta = extract_btts_over25_prob(grid)
        p_btts_dc     = float(grid[1:, 1:].sum())
        p_over25_dc   = prob_over25_poisson(lambda_h, lambda_a, rho=rho)
        p_naive       = p_btts_dc * p_over25_dc
        overlay       = p_dc_conjunta - p_naive

        # ── Probabilidade de mercado BTTS (de-vig) ───────────────────────────
        p_btts_market:    float | None = None
        btts_market_source = "unavailable"

        if odds_btts_yes and float(odds_btts_yes) > 1.0:
            p_yes_raw = 1.0 / float(odds_btts_yes)
            if odds_btts_no and float(odds_btts_no) > 1.0:
                p_no_raw = 1.0 / float(odds_btts_no)
                p_btts_market     = p_yes_raw / (p_yes_raw + p_no_raw)
                btts_market_source = "devig"
            else:
                p_btts_market     = p_yes_raw / 1.05   # assume margem 5%
                btts_market_source = "fallback"

        # ── CLV real ─────────────────────────────────────────────────────────
        clv_btts_over25: float | None = None
        p_naive_market:  float | None = None

        if p_btts_market is not None and p_over25_market and p_over25_market > 0:
            p_naive_market  = p_btts_market * p_over25_market
            clv_btts_over25 = p_dc_conjunta / p_naive_market - 1 if p_naive_market > 0 else None

        return {
            "p_dc_conjunta":    round(p_dc_conjunta, 6),
            "p_btts_dc":        round(p_btts_dc, 6),
            "p_over25_dc":      round(p_over25_dc, 6),
            "p_naive":          round(p_naive, 6),
            "overlay":          round(overlay, 6),
            "p_btts_market":    round(p_btts_market, 6)    if p_btts_market    is not None else None,
            "p_over25_market":  round(p_over25_market, 6)  if p_over25_market  is not None else None,
            "p_naive_market":   round(p_naive_market, 6)   if p_naive_market   is not None else None,
            "clv_btts_over25":  round(clv_btts_over25, 6)  if clv_btts_over25  is not None else None,
            "btts_market_source": btts_market_source,
            "odds_btts":        float(odds_btts_yes) if odds_btts_yes else None,
            "bookmaker_btts":   bookmaker_btts,
        }
    except Exception as exc:
        print(f"BTTS+O2.5 compute: {exc}", file=sys.stderr)
        return None


# ── Main scan ───────────────────────────────────────────────────────────────────

def scan() -> None:
    if not BSD_API_KEY:
        print("BSD_API_KEY não definido — a abortar", file=sys.stderr)
        sys.exit(0)

    dc_ratings = _load_dc_ratings()
    calibrator_fn = _load_calibrator_fn()
    existing_picks: dict[str, dict] = {p["id"]: p for p in _load_list(PICKS_FILE)}
    existing_rejected: list[dict] = _load_list(REJECTED_FILE)
    existing_btts: dict[str, dict] = {p["id"]: p for p in _load_list(BTTS_O25_FILE)}
    scan_state: dict = (
        json.loads(SCAN_STATE_FILE.read_text()) if SCAN_STATE_FILE.exists() else {}
    )

    new_picks: list[dict] = []
    new_rejected: list[dict] = []
    new_btts_picks: list[dict] = []
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

        lineup_info = _fetch_lineup_info(ev_id)
        pick = {**ev, **prob, **lineup_info, "gate_blocked_reason": "", "resultado_outcome": "", "scanned_at": ts}
        new_picks.append(pick)
        existing_picks[ev_id] = pick
        msg = _build_msg(ev, prob)
        if lineup_info.get("indisp_casa") is not None or lineup_info.get("indisp_fora") is not None:
            msg += (
                f"\n🚑 Indisponíveis: casa {lineup_info.get('indisp_casa', '?')}"
                f" / fora {lineup_info.get('indisp_fora', '?')}"
            )
        send_telegram(msg)
        alerts_sent += 1

        # ── BTTS+Over 2.5 gate ──────────────────────────────────────────────
        btts_data = _compute_btts_over25(
            ev["casa"], ev["fora"], ev["liga"], dc_ratings,
            odds_btts_yes=ev.get("odds_btts_yes"),
            odds_btts_no=ev.get("odds_btts_no"),
            bookmaker_btts=ev.get("bookmaker_btts", ""),
            p_over25_market=prob.get("p_market"),
        )
        # Gate antigo (overlay DC naive): btts_data["overlay"] >= BTTS_O25_OVERLAY_MIN
        clv_b25 = btts_data.get("clv_btts_over25") if btts_data else None
        if btts_data and clv_b25 is not None and clv_b25 >= CLV_BTTS_O25_MIN:
            btts_id = f"{ev_id}_btts"
            if btts_id not in existing_btts:
                btts_pick = {
                    **ev, **prob, **btts_data, **lineup_info,
                    "id": btts_id,
                    "resultado_btts_over25": "",
                    "scanned_at": ts,
                    "fonte": "auto-scan-btts",
                }
                new_btts_picks.append(btts_pick)
                existing_btts[btts_id] = btts_pick
                clv_pct = round(clv_b25 * 100, 1)
                naive_pct = round((btts_data["p_naive_market"] or 0) * 100, 1)
                send_telegram(
                    f"⚽ BTTS+Over 2.5 — CLV +{clv_pct:.1f}% vs mercado\n"
                    f"{ev['liga']}: {ev['casa']} vs {ev['fora']}\n"
                    f"p_conjunta={btts_data['p_dc_conjunta']*100:.1f}% | "
                    f"p_mkt={naive_pct:.1f}% | odds_btts={btts_data['odds_btts']}"
                )
                alerts_sent += 1

    _save_list(PICKS_FILE, list(existing_picks.values()))

    rej_index = {r.get("id", "") + r.get("scanned_at", ""): r for r in existing_rejected}
    for r in new_rejected:
        rej_index[r.get("id", "") + r.get("scanned_at", "")] = r
    _save_list(REJECTED_FILE, list(rej_index.values()))

    _save_list(BTTS_O25_FILE, list(existing_btts.values()))
    SCAN_STATE_FILE.write_text(json.dumps(scan_state, indent=2, ensure_ascii=False))

    print(
        f"Over 2.5 scan: {len(new_picks)} novos picks | "
        f"{len(new_rejected)} rejeitados | {alerts_sent} TG enviados | "
        f"{len(new_btts_picks)} BTTS+O2.5 novos"
    )

    git_commit_push(
        [str(PICKS_FILE), str(REJECTED_FILE), str(SCAN_STATE_FILE), str(BTTS_O25_FILE)],
        f"auto-scan over25 {ts} [skip ci]",
    )


if __name__ == "__main__":
    scan()
