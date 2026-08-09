"""
pipeline/scan_live.py
---------------------
Scanner LIVE server-side — corre ao minuto, 100% independente do browser
(nenhuma parte deste módulo depende de index.html/tracker.html estarem
abertos). Dois fluxos partilham a mesma detecção/enriquecimento por ciclo:

  1. "🔥 APOSTAR AGORA" — desactivado em produção (LIVE_ALERTS_ENABLED=False,
     pedido do autor, 9 ago 2026). Detecção/scoring continuam a correr
     (alimentam o card na tab Live do browser, que é só leitura); só o envio
     TG está bloqueado. NÃO REACTIVAR sem decisão explícita do autor.
  2. "👁 OBSERVAÇÕES" — activo, porta fiel de autoLogObservations() (index.html):
     gate (liga whitelisted + patternScore>=6 + probLive>=25% + não tardio sem
     golos) -> guarda em data/observations.json (dedup persistente por
     event_id, git-committed) -> Telegram. Ver run_observations().

Porta fiel da lógica do separador Live do index.html:
  - detect_patterns()  ← equivalente Python de detectPatterns() (12 padrões)
  - pattern_score()    ← mesma pontuação (critical=10, high=4, med=2, low/mkt=1, conv=0)
  - is_live_pick()     ← (score>=TH_LIVE_PICK e (pick guardado ou golos>=1)) ou convergência

O estado que no browser vive em localStorage (baseline de intervalo por jogo,
odd anterior de mercado, dedup de observações) aqui vive em dois sítios,
ambos sem depender do browser:
  - EM MEMÓRIA no dicionário `state` (baseline/mercado) — persiste entre
    iterações do loop, mas não sobrevive a um restart do processo.
  - EM DISCO (git) para dedup de observações — ver load_observation_state():
    lido de data/observations.json a cada arranque, por isso sobrevive aos
    restarts do workflow live_scanner.yml (a cada ~4h).

Cadência ao minuto: o GitHub Actions não permite cron fiável ao minuto (mínimo
~5 min, com atrasos). Por isso este módulo corre em `--loop`: um único job que
faz scan a cada `--interval` segundos durante `--minutes` minutos (< 6h, limite
do runner). O workflow `live_scanner.yml` reinicia o job nas janelas de jogos —
esse restart periódico é também o mecanismo de recuperação: se um processo
morrer ou ficar preso, o próximo arranque agendado (00:07/04:07/08:07/12:07/
16:07/20:07 UTC) substitui-o, e o histórico de runs no GitHub Actions serve de
health check de alto nível (complementar a data/live_scanner_health.json).

Uso:
    python -m pipeline.scan_live --once                # um scan e sai
    python -m pipeline.scan_live --loop --minutes 350  # loop ao minuto ~5h50

Requer secrets: BSD_API_KEY, TG_TOKEN, TG_CHAT_ID.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from pipeline.scan_common import (
    BSD_LEAGUE_ID_MAP,
    WHITELIST,
    classify_odds,
    git_commit_push,
    load_json_list,
    save_json_list,
    send_telegram,
)

# Definido localmente (não importar de pipeline.extract, que puxa pandas —
# este módulo só depende de `requests`, como o workflow live_scanner.yml instala).
BSD_BASE_URL = "https://sports.bzzoiro.com"

TH_LIVE_PICK = 12  # patternScore mínimo (calibrado: N=35, score>=12→46% WR vs <12→24%)
BSD_TIMEOUT = 20

# Filtros de envio Telegram (aplicam-se SÓ ao envio — deteção/logging/registo
# interno em scan_once() não são afectados). Thresholds e toggles num único
# bloco nomeado — nada hardcoded na lógica de passes_telegram_gate()/
# build_live_pick_msg(). GATE_BASE é o gate pré-existente (fail-closed: Pressão
# ausente/None → não envia); os restantes filtros são fail-open a campos
# ausentes (regista campo_ausente, não bloqueia).
ALERT_FILTERS = {
    "GATE_BASE": {
        "PRESSAO_MIN_TELEGRAM": 90,
        "SCORE_MIN_TELEGRAM": 20,
    },
    "FILTRO_XG_BANDA_MORTA": {
        # 1.0 <= xG < 1.5: pior banda de conversão na amostra (1G/5R) — bloqueia.
        "enabled": True,
        "XG_MIN": 1.0,
        "XG_MAX": 1.5,
    },
    "TIER_ALTA_CONVICCAO_XG": {
        # xG >= 2.5: 3/3 green na amostra. Não bloqueia — só marca a mensagem.
        "enabled": True,
        "XG_MIN": 2.5,
    },
    "FILTRO_MINUTO_TARDIO": {
        # A partir daqui, tempo estrutural insuficiente para o jogo virar Over — bloqueia.
        "enabled": True,
        "MINUTO_MAX": 85,
    },
    "DESCONTO_VANTAGEM_NUMERICA": {
        # Amostra n=1 — só instrumenta (aviso na mensagem quando há +1 homem
        # detectado), nunca bloqueia nem altera o gate.
        "enabled": False,
    },
}

# Aliases de topo — mantidos para não partir os imports existentes em
# tests/pipeline/test_scan_live.py. ALERT_FILTERS["GATE_BASE"] é a fonte de
# verdade; estas constantes derivam dele, nunca o inverso.
PRESSAO_MIN_TELEGRAM = ALERT_FILTERS["GATE_BASE"]["PRESSAO_MIN_TELEGRAM"]
SCORE_MIN_TELEGRAM = ALERT_FILTERS["GATE_BASE"]["SCORE_MIN_TELEGRAM"]

# Alertas TG "🔥 APOSTAR AGORA — LIVE OVER 2.5" desactivados (pedido do autor,
# 9 ago 2026). Detecção/scoring/log continuam a correr normalmente (scan_once
# ainda regista "ALERTA:" em stdout) — só o envio para o Telegram é bloqueado.
# Espelha LIVE_ALERTS_TG_ENABLED em index.html. OBSERVAÇÕES (index.html,
# autoLogObservations) não é afectado — vive noutro caminho de código.
LIVE_ALERTS_ENABLED = False

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PICKS_PATH = _PROJECT_ROOT / "data" / "picks.json"
_PICKS_1X2_PATH = _PROJECT_ROOT / "data" / "picks_1x2.json"
_OBS_PATH = _PROJECT_ROOT / "data" / "observations.json"
_HEALTH_PATH = _PROJECT_ROOT / "data" / "live_scanner_health.json"

# ── OBSERVAÇÕES (👁) — porta fiel de autoLogObservations() (index.html) ──
# Mesmos 4 filtros que o browser aplica de facto em `toSave` (não o comentário
# desactualizado "exige sinal de mercado OU score muito alto" que lá existe —
# esse comentário não corresponde a nenhum código; preserva-se o COMPORTAMENTO,
# não a prosa). Dedup por event_id é permanente (mirror de LSKEY.obs, que nunca
# expira), lida de data/observations.json em vez de localStorage — sobrevive a
# restarts do workflow (ver load_observation_state()).
OBS_PATTERN_SCORE_MIN = 6
OBS_PROB_LIVE_MIN = 25
OBS_LATE_MINUTE = 75
OBS_LATE_MIN_GOALS = 2
HEALTH_COMMIT_INTERVAL_S = 300  # throttle de commits só-de-health (sem obs novas)


# ---------------------------------------------------------------------------
# BSD API
# ---------------------------------------------------------------------------


def _bsd_get(api_key: str, path: str):
    """
    GET a um endpoint BSD. Devolve o JSON cru (dict ou list). Fail-safe: {} em erro.
    Timeout (connect, read) para não pendurar o loop se o servidor não responder.
    """
    headers = {"Authorization": f"Token {api_key}", "Accept": "application/json"}
    try:
        resp = requests.get(BSD_BASE_URL + path, headers=headers, timeout=(8, BSD_TIMEOUT))
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        _bsd_get_stats["failures"] += 1
        print(f"BSD GET falhou ({path}): {exc}", file=sys.stderr)
        return {}


def _bsd_get_dict(api_key: str, path: str) -> dict:
    """Como _bsd_get mas garante dict (para stats/odds por evento)."""
    payload = _bsd_get(api_key, path)
    return payload if isinstance(payload, dict) else {}


def _extract_events(payload) -> list[dict]:
    """Desembrulha resultados de /api/v2/events/ (list directa ou envelope)."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "results", "data"):
            val = payload.get(key)
            if isinstance(val, list):
                return val
    return []


# Estados BSD que NÃO são "em jogo" (fail-open: qualquer outro estado com
# minuto a decorrer conta como live).
_NOT_LIVE_STATUS = {
    "notstarted", "not_started", "scheduled", "upcoming", "tbd",
    "finished", "ended", "completed", "closed", "ft", "aet", "afterpenalties",
    "cancelled", "canceled", "postponed", "abandoned", "suspended", "interrupted",
}


def _looks_live(ev: dict) -> bool:
    """Heurística robusta para 'jogo a decorrer' — não depende de um único token."""
    status = str(ev.get("status") or "").lower().replace("_", "").replace(" ", "").replace("-", "")
    minute = ev.get("current_minute")
    if status in {"inplay", "live", "1sthalf", "2ndhalf", "halftime", "1h", "2h", "ht", "playing", "live1h", "live2h"}:
        return True
    # Fail-open: minuto a decorrer + estado não-terminal
    if isinstance(minute, (int, float)) and minute > 0 and status not in _NOT_LIVE_STATUS:
        return True
    return False


def _log_live_scan_status(status: str, n_failures: int) -> None:
    """Regista, uma vez por ciclo de fetch, o estado agregado do scan LIVE —
    mesmo espírito do ODDS_STATUS= (~216): nunca deixar '0 eventos' por falha
    de fetch parecer igual a '0 eventos' por não haver jogos live (ex.:
    pré-época). status: OK | NO_LIVE_GAMES | API_ERROR."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"LIVE_SCAN_STATUS={status} n_failures={n_failures} ts={ts}")


def fetch_live_events(api_key: str, verbose: bool = False) -> list[dict]:
    """
    Eventos a decorrer, via o endpoint comprovado /api/v2/events/.

    O /events/live/ pendura (provável streaming) e uma query SEM data também
    pendura (o servidor varre tudo). Só queries DELIMITADAS POR DATA respondem
    depressa — é o padrão que a produção usa a cada 30 min. Por isso todas as
    tentativas aqui são delimitadas por data (hoje→amanhã, cobre jogos que
    cruzam a meia-noite UTC), e filtramos os que estão em jogo client-side.

    Fail-safe: [] em erro. Emite sempre LIVE_SCAN_STATUS= (ver
    _log_live_scan_status) para distinguir esse [] de uma janela sem jogos.
    """
    _bsd_get_stats["failures"] = 0
    today = datetime.now(timezone.utc).date().isoformat()
    tomorrow = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    date_q = f"date_from={today}&date_to={tomorrow}&limit=200"

    # 1) primária (fiável): eventos da janela, sem filtro de status → filtra live.
    #    Query delimitada por data = resposta rápida (como a produção).
    payload = _bsd_get(api_key, f"/api/v2/events/?{date_q}")
    fetched = _extract_events(payload)
    live = [e for e in fetched if _looks_live(e)]
    if verbose:
        print(f"fetch_live_events: {len(fetched)} eventos na janela, {len(live)} a decorrer.")
    if live:
        _log_live_scan_status("OK", _bsd_get_stats["failures"])
        return live

    # 2) secundária: filtro directo por status de jogo (também delimitado por data).
    payload2 = _bsd_get(api_key, f"/api/v2/events/?status=inplay&{date_q}")
    live2 = [e for e in _extract_events(payload2) if _looks_live(e)]
    if verbose and live2:
        print(f"fetch_live_events: status=inplay devolveu {len(live2)} a decorrer.")

    failures = _bsd_get_stats["failures"]
    if live2:
        status = "OK"
    elif failures > 0:
        status = "API_ERROR"
    else:
        status = "NO_LIVE_GAMES"
    _log_live_scan_status(status, failures)
    return live2


def load_today_pick_ids() -> set[str]:
    """IDs de picks Over 2.5 de hoje ainda por resolver (mirror de isSavedPick)."""
    if not _PICKS_PATH.exists():
        return set()
    try:
        picks = json.loads(_PICKS_PATH.read_text())
    except Exception:  # noqa: BLE001
        return set()
    today = datetime.now(timezone.utc).date().isoformat()
    ids: set[str] = set()
    for p in picks if isinstance(picks, list) else []:
        data = str(p.get("data") or p.get("date") or "")
        if data[:10] == today and not p.get("result_over25"):
            ids.add(str(p.get("ev_id") or p.get("id") or ""))
    ids.discard("")
    return ids


# ---------------------------------------------------------------------------
# Enriquecimento (mirror do map em loadLive)
# ---------------------------------------------------------------------------


def _num(v):
    """None-safe: devolve None se não numérico, senão float/int."""
    if v is None:
        return None
    try:
        return v if isinstance(v, (int, float)) else float(v)
    except (TypeError, ValueError):
        return None


# Contador de falhas de _bsd_get, reposto a cada fetch_live_events() — permite
# distinguir "janela sem jogos" (n_failures=0) de "BSD indisponível"
# (n_failures>0), que de outra forma dariam o mesmo resultado ([] eventos).
_bsd_get_stats = {"failures": 0}

_RAW_DUMPED = False


def _dump_raw(api_key: str, ev_id) -> None:
    """Dump único da resposta crua de stats+odds — descobre a estrutura real
    da BSD quando o enriquecimento devolve vazio. Corre 1x por processo."""
    global _RAW_DUMPED
    if _RAW_DUMPED:
        return
    _RAW_DUMPED = True
    for label, path in (("stats", f"/api/v2/events/{ev_id}/stats/"),
                        ("odds", f"/api/v2/events/{ev_id}/odds/")):
        raw = _bsd_get(api_key, path)
        kind = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        snippet = json.dumps(raw, ensure_ascii=False)[:900]
        print(f"  [debug {label}] ev={ev_id} keys={kind}\n    {snippet}")


def _log_odds_status(ev_id, ev: dict, odds_status: str, raw_value) -> None:
    """Regista de forma explícita e visível (stdout — 'não só stderr') sempre
    que a odd ao vivo não é um preço negociável. Nunca deixar 'Odd 1.00 ·
    Prob Over 100%' passar como sucesso (issue #127) — a ausência/suspensão
    de preço fica visível nos logs do scan em vez de escondida atrás de um
    número fabricado."""
    home = ev.get("home_team") or ev.get("home") or "?"
    away = ev.get("away_team") or ev.get("away") or "?"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"ODDS_STATUS={odds_status} ev={ev_id} {home} vs {away} raw_over_odds={raw_value!r} ts={ts}")


def enrich_event(api_key: str, ev: dict, pick_ids: set[str]) -> dict:
    """Constrói o objecto de evento com stats + odds (equivalente ao map JS)."""
    ev_id = ev.get("id")
    stats = _bsd_get_dict(api_key, f"/api/v2/events/{ev_id}/stats/")
    odds = _bsd_get_dict(api_key, f"/api/v2/events/{ev_id}/odds/")

    sh = (stats.get("stats") or {}).get("home") or {}
    sa = (stats.get("stats") or {}).get("away") or {}
    xg_h = _num((sh.get("xg") or {}).get("actual"))
    xg_a = _num((sa.get("xg") or {}).get("actual"))
    mom = stats.get("momentum") or []
    last_mom = _num(mom[-1].get("v")) if mom and isinstance(mom[-1], dict) else None

    odds_block = odds.get("odds") or {}
    raw_over_odds = odds_block.get("over_25_goals")
    # over_25_goals_status: campo defensivo, não confirmado numa resposta BSD
    # real nesta sessão (ver classify_odds em scan_common.py).
    market_status = odds_block.get("over_25_goals_status") or odds.get("status")
    odds_status, over_odds = classify_odds(raw_over_odds, market_status)
    if odds_status != "VALID":
        _log_odds_status(ev_id, ev, odds_status, raw_over_odds)
    prob_live = round((1 / over_odds) * 100) if over_odds is not None else None

    h_score = ev.get("home_score") or 0
    a_score = ev.get("away_score") or 0
    goals = h_score + a_score
    xg_total = (xg_h + xg_a) if (xg_h is not None and xg_a is not None) else None

    return {
        "id": ev_id,
        "home": ev.get("home_team") or ev.get("home") or "",
        "away": ev.get("away_team") or ev.get("away") or "",
        "hScore": h_score,
        "aScore": a_score,
        "goals": goals,
        "min": ev.get("current_minute") or 0,
        "status": ev.get("status") or "",
        "period": ev.get("period"),
        "league": BSD_LEAGUE_ID_MAP.get(ev.get("league_id"), ev.get("league_name") or ""),
        "league_id": ev.get("league_id"),
        "xgH": xg_h, "xgA": xg_a, "xgTotal": xg_total,
        "lastMom": last_mom, "overOdds": over_odds, "probLive": prob_live,
        "oddsStatus": odds_status,
        "shots": {"h": _num(sh.get("total_shots")), "a": _num(sa.get("total_shots"))},
        "sot": {"h": _num(sh.get("shots_on_target")), "a": _num(sa.get("shots_on_target"))},
        "da": {"h": _num(sh.get("dangerous_attack")), "a": _num(sa.get("dangerous_attack"))},
        "corners": {"h": _num(sh.get("corners")), "a": _num(sa.get("corners"))},
        "possession": {"h": _num(sh.get("possession")), "a": _num(sa.get("possession"))},
        "redCards": {"h": _num(sh.get("red_cards")) or 0, "a": _num(sa.get("red_cards")) or 0},
        "isSavedPick": str(ev_id) in pick_ids,
    }


# ---------------------------------------------------------------------------
# detect_patterns — porta fiel de detectPatterns() (index.html)
# ---------------------------------------------------------------------------


def detect_patterns(e: dict, state: dict) -> list[dict]:
    """
    Equivalente Python de detectPatterns(). `state` guarda:
      state["ht"][id]  = baseline de intervalo (dict) — substitui localStorage htKey
      state["mkt"][id] = odd over anterior (float) — substitui localStorage mktPrev
    Função determinística dado (e, state).
    """
    p: list[dict] = []
    mn = e.get("min") or 0
    if mn < 8:
        return p

    ev_id = e["id"]
    remaining = max(0, 90 - mn)
    period_raw = str(e.get("period") or e.get("status") or "").lower()
    period_raw = period_raw.replace("_", "").replace(" ", "").replace("-", "")
    is_ht = period_raw in ("halftime", "ht") or e.get("status") == "half_time"
    is_2h = (not is_ht) and 46 <= mn <= 95

    def _t(d):  # total h+a com None→0
        return (d.get("h") or 0) + (d.get("a") or 0)

    total_shots = _t(e["shots"])
    total_sot = _t(e["sot"])
    total_da = _t(e["da"])
    total_corners = _t(e["corners"])
    xg_total = e.get("xgTotal") or 0
    today = datetime.now(timezone.utc).date().isoformat()

    ht_store = state.setdefault("ht", {})
    entry = {"shots": total_shots, "xg": xg_total, "da": total_da,
             "sot": total_sot, "corners": total_corners, "min": mn, "date": today}
    if is_ht:
        ht_store[ev_id] = entry
    elif is_2h and mn <= 52:
        ht_store.setdefault(ev_id, entry)
    if e["goals"] >= 3 or mn >= 90:
        ht_store.pop(ev_id, None)

    ht_base = None
    if is_2h:
        raw = ht_store.get(ev_id)
        if raw and raw.get("da") is not None and raw.get("sot") is not None \
                and raw.get("corners") is not None and raw.get("date") == today:
            ht_base = raw

    # Fiel ao browser (detectPatterns): na 2ª parte usa deltas relativos ao
    # baseline de intervalo; sem baseline suprime o volume (não conta a 1ª parte).
    # É isto que faz um jogo aparecer — ou não — no "APOSTAR AGORA".
    adj_shots = max(0, total_shots - ht_base["shots"]) if (is_2h and ht_base) else total_shots
    adj_xg = max(0, xg_total - ht_base["xg"]) if (is_2h and ht_base) else xg_total
    adj_da = max(0, total_da - (ht_base.get("da") or 0)) if (is_2h and ht_base) else total_da
    adj_sot = max(0, total_sot - (ht_base.get("sot") or 0)) if (is_2h and ht_base) else total_sot
    adj_corners = max(0, total_corners - (ht_base.get("corners") or 0)) if (is_2h and ht_base) else total_corners
    adj_min = max(1, mn - 45) if is_2h else mn
    # Guarda contra extrapolação no arranque da 2ª parte: com poucos minutos
    # decorridos, adj/adj_min*90 dá valores absurdos (ex.: 4 DA em 2 min →
    # "Pressão 100"; "7 remates 2ªP" ao 47'). Só confia no rácio da 2ª parte
    # após MIN_2H_WINDOW min — a mesma ideia que o browser já usa em xg_pace.
    MIN_2H_WINDOW = 15
    early_2h = bool(is_2h and ht_base and adj_min < MIN_2H_WINDOW)
    suppress_volume = is_ht or (is_2h and not ht_base) or early_2h
    sfx = " (2ªP)" if is_2h else ""

    # 1. PRESSÃO (DA)
    if not suppress_volume and adj_da > 0 and adj_min > 0:
        da_rate = (adj_da / adj_min) * 90
        pi = min(100, round(da_rate + adj_sot * 5))
        detail = f"{adj_da} atq. perigosos · {adj_sot} ao alvo{sfx}"
        label = f"Pressão {pi}{sfx}"
        if da_rate >= 60:
            p.append({"id": "pressure", "label": label, "emoji": "🔥", "level": "critical", "detail": detail})
        elif da_rate >= 40:
            p.append({"id": "pressure", "label": label, "emoji": "🔥", "level": "high", "detail": detail})
        elif da_rate >= 24:
            p.append({"id": "pressure", "label": label, "emoji": "🔥", "level": "med", "detail": detail})

    # 2. xG OVERDUE
    if e.get("xgTotal") is not None and mn >= 40 and e["goals"] < 3:
        delta = e["xgTotal"] - e["goals"]
        if delta >= 2.5:
            p.append({"id": "xg_delta", "label": f"xG +{delta:.1f} acima", "emoji": "🎲", "level": "critical",
                      "detail": f"xG {e['xgTotal']:.2f} vs {e['goals']} golos — golo(s) em dívida"})
        elif delta >= 1.8:
            p.append({"id": "xg_delta", "label": f"xG +{delta:.1f} acima", "emoji": "🎲", "level": "high",
                      "detail": f"xG {e['xgTotal']:.2f} vs {e['goals']} golos"})
        elif delta >= 1.2 and mn >= 55:
            p.append({"id": "xg_delta", "label": "xG sobrevaloriza", "emoji": "🎲", "level": "med",
                      "detail": f"xG {e['xgTotal']:.2f} vs {e['goals']} golos"})

    # 3. xG RITMO
    if not suppress_volume and adj_xg > 0 and adj_min >= 20:
        pace = (adj_xg / adj_min) * 90
        lvl = "critical" if pace >= 4.0 else "high" if pace >= 3.0 else "med" if pace >= 2.5 else None
        if lvl:
            p.append({"id": "xg_pace", "label": f"xG {pace:.1f}/90{sfx}", "emoji": "⚡", "level": lvl,
                      "detail": f"xG a ritmo de {pace:.1f} por 90min{sfx}"})

    # 4. CANTOS
    if not suppress_volume and adj_corners > 0 and adj_min > 0:
        c_rate = (adj_corners / adj_min) * 90
        if adj_corners >= 8 or (c_rate >= 12 and adj_min >= 20):
            p.append({"id": "corners", "label": f"{adj_corners} cantos{sfx}", "emoji": "🚩", "level": "high",
                      "detail": f"{c_rate:.1f}/90min — pressão de área elevada{sfx}"})
        elif adj_corners >= 5 or (c_rate >= 8 and adj_min >= 15):
            p.append({"id": "corners", "label": f"{adj_corners} cantos{sfx}", "emoji": "🚩", "level": "med",
                      "detail": f"{c_rate:.1f}/90min{sfx}"})

    # 5. VOLUME DE REMATES
    if not suppress_volume and adj_shots > 0 and adj_min > 0:
        s_rate = (adj_shots / adj_min) * 90
        shot_label = f"{adj_shots}r 2ªP" if (is_2h and ht_base) else f"{total_shots} remates"
        if mn > 55 and e["goals"] == 0 and (e.get("probLive") or 0) < 35:
            pass  # ignora: volume sem conversão
        elif s_rate >= 28:
            p.append({"id": "shots", "label": shot_label, "emoji": "🎯", "level": "high",
                      "detail": f"{s_rate:.0f} remates/90min{sfx} (muito alto)"})
        elif s_rate >= 22:
            p.append({"id": "shots", "label": shot_label, "emoji": "🎯", "level": "med",
                      "detail": f"{s_rate:.0f} remates/90min{sfx}"})

    # 6. JOGO ABERTO
    if mn >= 55 and e["goals"] < 3:
        diff = abs(e["hScore"] - e["aScore"])
        both_need = e["goals"] <= 1 and diff <= 1
        lm = e.get("lastMom")
        losing_push = lm is not None and (
            (e["hScore"] < e["aScore"] and lm < -15) or (e["hScore"] > e["aScore"] and lm > 15)
        )
        if both_need and losing_push and e["goals"] >= 1:
            p.append({"id": "open", "label": "JOGO ABERTO", "emoji": "🎭", "level": "low",
                      "detail": f"{remaining}min, equipa a perder ataca"})

    # 7. MOMENTUM
    if e.get("lastMom") is not None:
        a = abs(e["lastMom"])
        side = "Casa" if e["lastMom"] > 0 else "Fora"
        if a >= 70:
            p.append({"id": "mom", "label": f"{side} domina", "emoji": "💥", "level": "high",
                      "detail": f"Momentum {side.lower()} {a:.0f} — controlo total"})
        elif a >= 45:
            p.append({"id": "mom", "label": f"{side} pressiona", "emoji": "💪", "level": "med",
                      "detail": f"Momentum {side.lower()} {a:.0f}"})

    # 8. LATE PUSH
    if mn >= 68 and e["goals"] == 2:
        prob = e.get("probLive") or 0
        if prob >= 28:
            p.append({"id": "late", "label": f"1 gol em {remaining}min", "emoji": "⏰", "level": "low",
                      "detail": f"Falta 1 golo · mercado {prob}% · {remaining}min"})

    # 9. MERCADO AO VIVO — odds a cair
    mkt_store = state.setdefault("mkt", {})
    over_odds = e.get("overOdds")
    if is_ht and over_odds:
        mkt_store[ev_id] = round(over_odds, 3)
    prev = mkt_store.get(ev_id, 0)
    if (not is_ht) and over_odds and prev > 0:
        drop = (prev - over_odds) / prev * 100
        if drop >= 6:
            p.append({"id": "mkt", "label": f"Odds −{drop:.1f}%", "emoji": "📈", "level": "critical",
                      "detail": f"Mercado caiu de {prev:.2f} → {over_odds:.2f} (sharp money)"})
        elif drop >= 4:
            p.append({"id": "mkt", "label": f"Odds −{drop:.1f}%", "emoji": "📈", "level": "mkt",
                      "detail": f"Mercado caiu de {prev:.2f} → {over_odds:.2f}"})
        if drop >= 4 or over_odds >= prev:
            mkt_store[ev_id] = round(over_odds, 3)
    elif (not is_ht) and over_odds:
        mkt_store[ev_id] = round(over_odds, 3)

    # 10. POSSE DOMINANTE
    ph, pa = e["possession"]["h"], e["possession"]["a"]
    if ph is not None and pa is not None:
        dom = max(ph, pa)
        side = "Casa" if ph > pa else "Fora"
        has_activity = adj_da > 0 or adj_sot > 0 or adj_corners >= 3
        if dom >= 70 and has_activity:
            p.append({"id": "possession", "label": f"Posse {side} {round(dom)}%", "emoji": "⚽", "level": "med",
                      "detail": f"{side} domina com {round(dom)}% de posse"})

    # 11. VANTAGEM NUMÉRICA
    r_h, r_a = e["redCards"]["h"] or 0, e["redCards"]["a"] or 0
    if r_h != r_a and mn >= 30:
        adv = "Casa" if r_h < r_a else "Fora"
        diff = abs(r_h - r_a)
        label = f"{adv} +1 homem" if diff == 1 else f"{adv} +{diff} homens"
        other = "Fora" if adv == "Casa" else "Casa"
        p.append({"id": "numerical", "label": label, "emoji": "🟥", "level": "med",
                  "detail": f"{other} com {max(r_h, r_a)} vermelho(s) — {adv} em vantagem numérica"})

    # 12. CONVERGÊNCIA
    strong = [x for x in p if x["level"] in ("critical", "high", "mkt")]
    has_pressure = any(x["id"] == "pressure" for x in p)
    has_xg_delta = any(x["id"] == "xg_delta" for x in p)
    if (len(p) >= 3 and len(strong) >= 2) or (has_pressure and has_xg_delta and len(p) >= 3 and len(strong) >= 1):
        p.insert(0, {"id": "conv", "label": "CONVERGÊNCIA", "emoji": "🔮", "level": "conv",
                     "detail": f"{len(p)} padrões em simultâneo — sinal forte"})

    return p


_LEVEL_PTS = {"critical": 10, "high": 4, "med": 2, "conv": 0}


def pattern_score(patterns: list[dict]) -> int:
    """Soma de pontos (mesma fórmula do JS: low/mkt→1)."""
    return sum(_LEVEL_PTS.get(pt["level"], 1) for pt in patterns)


def is_live_pick(e: dict) -> bool:
    """(score>=TH e (pick guardado ou golos>=1)) ou convergência."""
    score = e.get("patternScore", 0)
    has_conv = any(pt["id"] == "conv" for pt in e.get("patterns", []))
    return (score >= TH_LIVE_PICK and (e.get("isSavedPick") or e["goals"] >= 1)) or has_conv


_PRESSAO_RE = re.compile(r"Pressão (\d+)")


def _pressao_value(e: dict) -> float | None:
    """Valor numérico do padrão 'pressure' (ex.: 'Pressão 87 (2ªP)' → 87.0).
    None se o padrão não estiver presente (jogo sem sinal de pressão detectado)."""
    for pt in e.get("patterns", []):
        if pt.get("id") == "pressure":
            m = _PRESSAO_RE.search(pt.get("label", ""))
            if m:
                return float(m.group(1))
    return None


def _log_alert_blocked(motivo: str, e: dict) -> None:
    """Log estruturado (stdout) de alerta bloqueado por um filtro de
    ALERT_FILTERS — permite contabilizar no backtest quantos alertas cada
    filtro removeu."""
    print(f"alerta_bloqueado motivo={motivo} ev={e.get('id')}")


def _log_campo_ausente(campo: str, e: dict) -> None:
    """Log estruturado (stdout) quando um filtro de ALERT_FILTERS não
    consegue avaliar por falta do campo — fail-open: não bloqueia, só regista."""
    print(f"campo_ausente campo={campo} ev={e.get('id')}")


def passes_telegram_gate(e: dict) -> bool:
    """Filtro de envio Telegram: Pressão >= PRESSAO_MIN_TELEGRAM E
    Score >= SCORE_MIN_TELEGRAM (fail-closed: Pressão ausente/None → não
    envia), mais os filtros opcionais em ALERT_FILTERS (fail-open a campos
    ausentes — regista campo_ausente e deixa passar, para não perder greens
    por dados em falta)."""
    pressao = _pressao_value(e)
    if pressao is None:
        return False
    if not (pressao >= PRESSAO_MIN_TELEGRAM and e.get("patternScore", 0) >= SCORE_MIN_TELEGRAM):
        return False

    banda = ALERT_FILTERS["FILTRO_XG_BANDA_MORTA"]
    if banda["enabled"]:
        xg = e.get("xgTotal")
        if xg is None:
            _log_campo_ausente("xgTotal", e)
        elif banda["XG_MIN"] <= xg < banda["XG_MAX"]:
            _log_alert_blocked("xg_banda_morta", e)
            return False

    tardio = ALERT_FILTERS["FILTRO_MINUTO_TARDIO"]
    if tardio["enabled"]:
        minuto = e.get("min")
        if minuto is None:
            _log_campo_ausente("min", e)
        elif minuto >= tardio["MINUTO_MAX"]:
            _log_alert_blocked("minuto_tardio", e)
            return False

    return True


# ---------------------------------------------------------------------------
# Mensagem TG (mirror de buildLivePickMsg do index.html)
# ---------------------------------------------------------------------------


def build_live_pick_msg(e: dict) -> str:
    pats = " · ".join(f"{pt['emoji']} {pt['label']}" for pt in e.get("patterns", [])) or "—"
    prob = f"{e['probLive']}%" if e.get("probLive") is not None else "—"
    odd = f"{e['overOdds']:.2f}" if e.get("overOdds") else "—"
    xg_total = e.get("xgTotal")
    xg = f"{xg_total:.2f}" if xg_total is not None else "—"

    header = "🔥 APOSTAR AGORA — LIVE OVER 2.5"
    tier = ALERT_FILTERS["TIER_ALTA_CONVICCAO_XG"]
    if tier["enabled"] and xg_total is not None and xg_total >= tier["XG_MIN"]:
        header += "\n⭐ ALTA CONVICÇÃO"

    aviso_vantagem = ""
    if ALERT_FILTERS["DESCONTO_VANTAGEM_NUMERICA"]["enabled"]:
        numerical = next((pt for pt in e.get("patterns", []) if pt.get("id") == "numerical"), None)
        if numerical:
            aviso_vantagem = f"\n⚠️ {numerical['label']}"

    return (
        f"{header}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⚽ {e['home']} vs {e['away']}\n"
        f"🏆 {e['league']}\n"
        f"⏱ {e['min']}'  ·  Resultado {e['hScore']}-{e['aScore']} ({e['goals']}/3 golos)\n"
        f"📊 Prob Over {prob}  ·  Odd {odd}  ·  xG {xg}\n"
        f"🎯 Sinais: {pats}\n"
        f"Score {e.get('patternScore', 0)}"
        f"{aviso_vantagem}"
    )


# ---------------------------------------------------------------------------
# OBSERVAÇÕES (👁) — porta fiel de autoLogObservations() (index.html)
# ---------------------------------------------------------------------------


def passes_observation_gate(e: dict) -> bool:
    """Mirror exacto do filtro `toSave` em autoLogObservations(): liga
    whitelisted + patternScore>=6 + odds implícitas>=25% (ou ausentes) +
    não tardio (>=75') sem golos suficientes (<2)."""
    if e.get("league") not in WHITELIST:
        return False
    if (e.get("patternScore") or 0) < OBS_PATTERN_SCORE_MIN:
        return False
    prob_live = e.get("probLive")
    if prob_live is not None and prob_live < OBS_PROB_LIVE_MIN:
        return False
    if (e.get("min") or 0) >= OBS_LATE_MINUTE and (e.get("goals") or 0) < OBS_LATE_MIN_GOALS:
        return False
    return True


def _load_pick_index() -> dict[str, dict]:
    """base_id -> pick Over 2.5 (data/picks.json) — para has_scan_pick/scan_score.
    Informativo apenas (não é gate); scanGame?.score do browser (score da tab
    Scanner em memória) não tem equivalente persistido, por isso fica de fora
    — mesma limitação que já existe na prática (o browser também o deixa em
    branco quando o jogo não passou pela tab Scanner nesta sessão)."""
    index: dict[str, dict] = {}
    for p in load_json_list(_PICKS_PATH):
        base_id = str(p.get("ev_id") or p.get("id") or "").split("_")[0]
        if base_id:
            index[base_id] = p
    return index


def _load_sharp_ids() -> set[str]:
    """base_ids com sinal Sharp 1X2 (data/picks_1x2.json) — para has_sharp."""
    ids: set[str] = set()
    for p in load_json_list(_PICKS_1X2_PATH):
        base_id = str(p.get("ev_id") or p.get("id") or "").split("_")[0]
        if base_id:
            ids.add(base_id)
    return ids


def build_observation(e: dict, pick_index: dict[str, dict], sharp_ids: set[str]) -> dict:
    """Mirror do map em autoLogObservations() — mesmos campos, mesma forma
    (ver data/observations.json para o schema real gravado pelo browser)."""
    base_id = str(e["id"])
    obs_id = f"{base_id}_obs_{int(time.time() * 1000)}"
    pick = pick_index.get(base_id)
    xg_total = e.get("xgTotal")
    over_odds = e.get("overOdds")
    now = datetime.now(timezone.utc)
    return {
        "id": obs_id,
        "event_id": base_id,
        "casa": e.get("home", ""), "fora": e.get("away", ""), "liga": e.get("league", ""),
        "data": now.date().isoformat(),
        "detected_at": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "min": e.get("min", 0),
        "score": f"{e.get('hScore', 0)}-{e.get('aScore', 0)}",
        "goals": e.get("goals", 0),
        "xg": f"{xg_total:.2f}" if xg_total is not None else "",
        "odds_live": f"{over_odds:.2f}" if over_odds else "",
        "prob_live": e.get("probLive") if e.get("probLive") is not None else "",
        "pattern_score": e.get("patternScore", 0),
        "patterns": [pt["id"] for pt in e.get("patterns", [])],
        "pattern_labels": [f"{pt['emoji']} {pt['label']}" for pt in e.get("patterns", [])],
        "has_scan_pick": "1" if pick else "",
        "scan_score": str(pick.get("score_sistema", "")) if pick else "",
        "has_sharp": "1" if base_id in sharp_ids else "",
        "result_over25": "",
        "final_score": "",
        "result_at_min": "",
    }


def compute_observation_result_updates(events: list[dict], unresolved: dict[str, str]) -> dict[str, dict]:
    """Mirror do loop `updates` em autoLogObservations(): para observações já
    guardadas e ainda sem resultado (`unresolved`: event_id -> obs_id), marca
    WIN se golos>=3, LOSS se o jogo terminou (min>=90 ou period FT) sem lá
    chegar. Devolve {obs_id: patch}."""
    updates: dict[str, dict] = {}
    for e in events:
        obs_id = unresolved.get(str(e["id"]))
        if not obs_id:
            continue
        goals = e.get("goals", 0)
        min_ = e.get("min", 0)
        period = str(e.get("period") or "").upper()
        final_score = f"{e.get('hScore', 0)}-{e.get('aScore', 0)}"
        if goals >= 3:
            updates[obs_id] = {"result_over25": "WIN", "final_score": final_score, "result_at_min": min_}
        elif min_ >= 90 or period == "FT":
            updates[obs_id] = {"result_over25": "LOSS", "final_score": final_score, "result_at_min": min_}
    return updates


def build_observations_message(events: list[dict], entries: list[dict]) -> str:
    """Mirror da mensagem TG em autoLogObservations() — '👁 OBSERVAÇÕES: N
    jogos guardados' seguido de uma linha por jogo com os padrões detectados."""
    lines = []
    for e, entry in zip(events, entries):
        pats = " ".join(f"{pt['emoji']}{pt['label']}" for pt in e.get("patterns", []))
        lines.append(f"{e['home']} vs {e['away']} [score:{entry['pattern_score']}] {pats}")
    return f"👁 OBSERVAÇÕES: {len(entries)} jogos guardados\n" + "\n".join(lines)


def load_observation_state() -> dict:
    """Estado inicial de dedup ao arrancar/reiniciar o loop, lido de
    data/observations.json (fonte de verdade persistida via git — substitui o
    localStorage do browser, que não existe no backend). Sobrevive aos
    restarts do workflow live_scanner.yml (a cada ~4h) sem duplicar
    observações já guardadas nem perder o histórico de pendentes."""
    current = load_json_list(_OBS_PATH)
    seen = {str(o.get("event_id") or "") for o in current}
    seen.discard("")
    unresolved = {
        str(o["event_id"]): o["id"]
        for o in current
        if not o.get("result_over25") and o.get("event_id") and o.get("id")
    }
    return {"seen_event_ids": seen, "unresolved": unresolved}


def run_observations(events: list[dict], obs_state: dict, verbose: bool = False) -> int:
    """Um ciclo do fluxo OBSERVAÇÕES: detecção -> gate -> dedup -> guardar ->
    Telegram. `obs_state` (seen_event_ids/unresolved) vive em memória durante
    o loop e é inicializado por load_observation_state(). Devolve o nº de
    observações novas guardadas neste ciclo."""
    if not events:
        return 0

    new_events = [
        e for e in events
        if str(e["id"]) not in obs_state["seen_event_ids"] and passes_observation_gate(e)
    ]
    updates = compute_observation_result_updates(events, obs_state["unresolved"])

    if not new_events and not updates:
        return 0

    pick_index = _load_pick_index()
    sharp_ids = _load_sharp_ids()
    new_entries = [build_observation(e, pick_index, sharp_ids) for e in new_events]

    current = load_json_list(_OBS_PATH)
    by_id = {o["id"]: o for o in current}
    for obs_id, patch in updates.items():
        if obs_id in by_id:
            by_id[obs_id] = {**by_id[obs_id], **patch}
    for entry in new_entries:
        by_id[entry["id"]] = entry
    save_json_list(_OBS_PATH, list(by_id.values()))
    git_commit_push(
        [str(_OBS_PATH)],
        f"obs: {len(new_entries)} new, {len(updates)} updated [skip ci]",
    )

    for e in new_events:
        obs_state["seen_event_ids"].add(str(e["id"]))
    for entry in new_entries:
        obs_state["unresolved"][entry["event_id"]] = entry["id"]
    resolved_ids = set(updates.keys())
    for event_id, obs_id in list(obs_state["unresolved"].items()):
        if obs_id in resolved_ids:
            del obs_state["unresolved"][event_id]

    if verbose:
        print(f"OBSERVACOES: {len(new_entries)} nova(s), {len(updates)} actualizada(s).")

    if new_entries:
        send_telegram(build_observations_message(new_events, new_entries))

    return len(new_entries)


# ---------------------------------------------------------------------------
# Health check — visibilidade de que o worker está vivo sem depender do browser
# ---------------------------------------------------------------------------


def new_health_state() -> dict:
    """Contadores acumulados do worker LIVE, independentes de `state`
    (ht/mkt) — não deve ser apagado no reset diário do loop principal."""
    return {
        "last_scan_at": None,
        "last_successful_api_request_at": None,
        "live_games_found": 0,
        "observations_generated_total": 0,
        "last_telegram_sent_at": None,
        "errors": [],  # últimos 5: {"at": iso, "msg": str}
        "_last_committed_at": 0.0,
    }


def update_health(health: dict, *, live_games: int, observations_added: int,
                   api_ok: bool, error: str | None = None) -> None:
    """Actualiza os contadores em memória após um ciclo de scan_once().
    write_health()/maybe_commit_health() decidem quando persistir em disco/git."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    health["last_scan_at"] = now
    health["live_games_found"] = live_games
    if api_ok:
        health["last_successful_api_request_at"] = now
    if observations_added:
        health["observations_generated_total"] += observations_added
        health["last_telegram_sent_at"] = now
    if error:
        health["errors"] = (health.get("errors") or [])[-4:] + [{"at": now, "msg": error}]


def write_health(health: dict, running: bool = True) -> None:
    snapshot = {k: v for k, v in health.items() if not k.startswith("_")}
    snapshot["running"] = running
    _HEALTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    _HEALTH_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_commit_health(health: dict, running: bool = True, force: bool = False) -> None:
    """Comita data/live_scanner_health.json no máximo a cada
    HEALTH_COMMIT_INTERVAL_S — health é informativo, não precisa (nem deve)
    gerar um commit por ciclo de 60s durante 5h50 de loop."""
    write_health(health, running)
    last = health.get("_last_committed_at", 0)
    if not force and (time.time() - last) < HEALTH_COMMIT_INTERVAL_S:
        return
    git_commit_push([str(_HEALTH_PATH)], "health: live scanner status [skip ci]")
    health["_last_committed_at"] = time.time()


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def scan_once(api_key: str, state: dict, pick_ids: set[str], alerted: set[str],
              verbose: bool = True, obs_state: dict | None = None,
              health_stats: dict | None = None) -> int:
    """
    Um ciclo de scan. Envia TG para sinais qualificados novos (goals<3, ainda
    não alertados). Devolve nº de alertas "🔥 APOSTAR AGORA" enviados (o
    interruptor global continua desligado — ver LIVE_ALERTS_ENABLED).

    NOTA: o sinal LIVE é INDEPENDENTE da whitelist do pré-jogo. Baseia-se só em
    padrões do próprio jogo (xG, pressão, momentum, mercado ao vivo), que não
    dependem do modelo Dixon-Coles nem do histórico por liga. Por isso NÃO se
    filtra por WHITELIST aqui — igual ao separador Live do browser.

    `obs_state` (opcional): quando fornecido (produção, via load_observation_state()),
    corre também o fluxo 👁 OBSERVAÇÕES sobre os mesmos eventos enriquecidos —
    detecção/scoring reutilizados, sem novo fetch à BSD. Quando None (default,
    usado pelos testes existentes), o passo de observações é ignorado por
    completo — sem qualquer leitura/escrita de ficheiros nem chamada a git.
    `health_stats` (opcional): dict mutado in-place com {"live_games":, "api_ok":,
    "observations_added":} para quem quiser telemetria do ciclo (ver update_health()).
    """
    events = fetch_live_events(api_key, verbose=verbose)
    if health_stats is not None:
        health_stats["live_games"] = len(events)
    if not events:
        if health_stats is not None:
            health_stats["api_ok"] = _bsd_get_stats["failures"] == 0
        return 0

    sent = 0
    enriched_events: list[dict] = []
    for ev in events[:50]:
        try:
            e = enrich_event(api_key, ev, pick_ids)
        except Exception as exc:  # noqa: BLE001
            print(f"enrich falhou (ev {ev.get('id')}): {exc}", file=sys.stderr)
            continue
        e["patterns"] = detect_patterns(e, state)
        e["patternScore"] = pattern_score(e["patterns"])
        enriched_events.append(e)

        if verbose:
            # Diagnóstico por jogo live: confirma que enrich+padrões funcionam e
            # mostra a distância ao gate (score>=12). xg=None ⇒ stats sem xG.
            pat_ids = ",".join(pt["id"] for pt in e["patterns"]) or "—"
            xg = f"{e['xgTotal']:.1f}" if e.get("xgTotal") is not None else "?"
            print(f"  live: {e['home']} {e['hScore']}-{e['aScore']} {e['away']} "
                  f"{e['min']}' xg={xg} score={e['patternScore']} [{pat_ids}]")
            # Se o enrich veio vazio, dumpa a resposta crua (1x) p/ descobrir a
            # estrutura real do endpoint de stats/odds da BSD.
            if e.get("xgTotal") is None and not e["patterns"]:
                _dump_raw(api_key, ev.get("id"))

        if not is_live_pick(e) or e["isSavedPick"] or e["goals"] >= 3:
            continue
        key = str(e["id"])
        if key in alerted:
            continue
        # NOTA (correcção bug #8): só marcar `alerted` DEPOIS de confirmar o
        # gate de TG e de o envio ter sucesso. Antes desta correcção,
        # alerted.add(key) corria logo que is_live_pick() era True — um jogo
        # que qualificasse com Pressão<90 ficava "queimado" para sempre nesta
        # execução, mesmo que a Pressão subisse e cumprisse o gate minutos
        # depois. Também não se marca em caso de falha de envio (send_telegram
        # devolve False), para o jogo ser reavaliado no ciclo seguinte.
        if not passes_telegram_gate(e):
            continue
        if not LIVE_ALERTS_ENABLED:
            continue
        if send_telegram(build_live_pick_msg(e)):
            alerted.add(key)
            sent += 1
            print(f"ALERTA: {e['home']} vs {e['away']} ({e['min']}') score={e['patternScore']}")

    if health_stats is not None:
        health_stats["api_ok"] = _bsd_get_stats["failures"] == 0

    if obs_state is not None:
        added = run_observations(enriched_events, obs_state, verbose=verbose)
        if health_stats is not None:
            health_stats["observations_added"] = added

    return sent


def main() -> None:
    ap = argparse.ArgumentParser(description="Scanner LIVE Over 2.5 — alertas TG ao minuto")
    ap.add_argument("--loop", action="store_true", help="loop contínuo (a cada --interval s)")
    ap.add_argument("--once", action="store_true", help="um único scan e sai")
    ap.add_argument("--interval", type=int, default=60, help="segundos entre scans (default 60)")
    ap.add_argument("--minutes", type=int, default=350, help="duração máx do loop em min (<360, limite runner)")
    args = ap.parse_args()

    api_key = os.environ.get("BSD_API_KEY", "")
    if not api_key:
        print("BSD_API_KEY não definido — abort", file=sys.stderr)
        sys.exit(1)

    state: dict = {"ht": {}, "mkt": {}}
    alerted: set[str] = set()
    # obs_state sobrevive a restarts do workflow porque é reconstruído a partir
    # de data/observations.json (persistido em git), não de memória — ver
    # load_observation_state(). health não é reposto no reset diário (abaixo):
    # é telemetria do PROCESSO (este arranque do worker), não do dia de jogos.
    obs_state = load_observation_state()
    health = new_health_state()

    if args.once or not args.loop:
        pick_ids = load_today_pick_ids()
        health_stats: dict = {}
        try:
            n = scan_once(api_key, state, pick_ids, alerted, obs_state=obs_state, health_stats=health_stats)
        except Exception as exc:  # noqa: BLE001
            update_health(health, live_games=0, observations_added=0, api_ok=False, error=str(exc))
            maybe_commit_health(health, running=False, force=True)
            raise
        update_health(health, live_games=health_stats.get("live_games", 0),
                      observations_added=health_stats.get("observations_added", 0),
                      api_ok=health_stats.get("api_ok", False))
        maybe_commit_health(health, running=False, force=True)
        print(f"Scan único terminado — {n} alerta(s), {health_stats.get('observations_added', 0)} observação(ões).")
        return

    deadline = time.time() + args.minutes * 60
    cur_day = datetime.now(timezone.utc).date()
    pick_ids = load_today_pick_ids()
    write_health(health, running=True)
    print(f"Loop LIVE iniciado — interval={args.interval}s, até {args.minutes}min.")
    while time.time() < deadline:
        # Recarrega picks e limpa dedup à mudança de dia (novos jogos). obs_state
        # NÃO é reposto aqui — a dedup de observações é por event_id, permanente,
        # e cada jogo já tem um event_id próprio (nunca reaparece no dia seguinte).
        today = datetime.now(timezone.utc).date()
        if today != cur_day:
            cur_day = today
            pick_ids = load_today_pick_ids()
            alerted.clear()
            state = {"ht": {}, "mkt": {}}
        health_stats = {}
        try:
            n = scan_once(api_key, state, pick_ids, alerted, obs_state=obs_state, health_stats=health_stats)
            if n:
                print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {n} alerta(s) enviado(s).")
            update_health(health, live_games=health_stats.get("live_games", 0),
                          observations_added=health_stats.get("observations_added", 0),
                          api_ok=health_stats.get("api_ok", False))
        except Exception as exc:  # noqa: BLE001
            print(f"scan_once erro (não fatal): {exc}", file=sys.stderr)
            update_health(health, live_games=health_stats.get("live_games", 0),
                          observations_added=0, api_ok=False, error=str(exc))
        maybe_commit_health(health, running=True)
        time.sleep(args.interval)
    maybe_commit_health(health, running=False, force=True)
    print("Loop LIVE terminado (deadline).")


if __name__ == "__main__":
    main()
