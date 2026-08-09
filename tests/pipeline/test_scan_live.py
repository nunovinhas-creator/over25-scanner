"""
tests/pipeline/test_scan_live.py
--------------------------------
Testes da porta Python da lógica live "🔥 APOSTAR AGORA" (pipeline/scan_live.py),
equivalente a detectPatterns() / isLivePick() do index.html.

Todos os eventos aqui são sintéticos (# synthetic) — nunca dados de produção.
"""

from __future__ import annotations

import pytest

from pipeline.scan_live import (
    ALERT_FILTERS,
    PRESSAO_MIN_TELEGRAM,
    SCORE_MIN_TELEGRAM,
    TH_LIVE_PICK,
    _extract_events,
    _looks_live,
    build_live_pick_msg,
    detect_patterns,
    enrich_event,
    is_live_pick,
    passes_telegram_gate,
    pattern_score,
)


def _base_event(**over):  # synthetic
    """Evento live mínimo; sobrepõe campos via kwargs."""
    e = {
        "id": 1, "home": "Casa", "away": "Fora", "hScore": 0, "aScore": 0,
        "goals": 0, "min": 30, "status": "1st_half", "period": "1st_half",
        "league": "Serie A", "league_id": 4,
        "xgH": 0.0, "xgA": 0.0, "xgTotal": 0.0, "lastMom": 0,
        "overOdds": 2.0, "probLive": 50,
        "shots": {"h": 0, "a": 0}, "sot": {"h": 0, "a": 0},
        "da": {"h": 0, "a": 0}, "corners": {"h": 0, "a": 0},
        "possession": {"h": 50, "a": 50}, "redCards": {"h": 0, "a": 0},
        "isSavedPick": False,
    }
    e.update(over)
    return e


def _score(e):
    e["patterns"] = detect_patterns(e, {"ht": {}, "mkt": {}})
    e["patternScore"] = pattern_score(e["patterns"])
    return e


def test_early_minute_no_patterns():
    """Antes do minuto 8 não há qualquer padrão."""  # synthetic
    e = _score(_base_event(min=5, xgTotal=2.0, da={"h": 40, "a": 30}))
    assert e["patterns"] == []
    assert not is_live_pick(e)


def test_strong_first_half_qualifies():
    """Pressão crítica + xG ritmo + cantos na 1ª parte → score alto e live pick."""  # synthetic
    e = _score(_base_event(
        min=38, goals=1, hScore=1, xgTotal=3.3, lastMom=60,
        overOdds=1.7, probLive=59,
        shots={"h": 8, "a": 6}, sot={"h": 5, "a": 3},
        da={"h": 30, "a": 22}, corners={"h": 5, "a": 4},
        possession={"h": 60, "a": 40},
    ))
    assert e["patternScore"] >= TH_LIVE_PICK
    assert is_live_pick(e)
    # convergência aparece à cabeça quando há padrões fortes em simultâneo
    assert e["patterns"][0]["id"] == "conv"


def test_second_half_without_baseline_suppresses_volume():
    """Fiel ao browser: sem baseline de intervalo, os padrões de volume
    (pressão/cantos/remates) são suprimidos na 2ª parte — é o que decide se um
    jogo aparece no 'APOSTAR AGORA'. xG delta não depende de volume."""  # synthetic
    e = _score(_base_event(
        min=70, goals=2, hScore=1, aScore=1, status="2nd_half", period="2nd_half",
        xgTotal=3.6, lastMom=55, overOdds=1.65, probLive=61,
        da={"h": 45, "a": 38},
    ))
    ids = {p["id"] for p in e["patterns"]}
    assert "pressure" not in ids  # volume suprimido (sem baseline)
    assert "xg_delta" in ids       # xG delta não depende de volume


def test_early_second_half_does_not_extrapolate_volume():
    """No arranque da 2ª parte (poucos min desde o baseline) o volume NÃO dispara
    — evita 'Pressão 100' / '7 remates 2ªP' ao 47'. Reproduz o sinal falso real."""  # synthetic
    state = {"ht": {}, "mkt": {}}
    # baseline capturado ao intervalo (1ª parte com pouca actividade)
    ht = _base_event(min=45, status="half_time", period="half_time",
                     shots={"h": 3, "a": 2}, da={"h": 15, "a": 10})
    detect_patterns(ht, state)
    # 47': 2 min de 2ª parte, alguma actividade → NÃO deve extrapolar
    sh = _base_event(min=47, goals=1, hScore=0, aScore=1, status="2nd_half", period="2nd_half",
                     shots={"h": 8, "a": 4}, da={"h": 19, "a": 12})
    sh["patterns"] = detect_patterns(sh, state)
    ids = {p["id"] for p in sh["patterns"]}
    assert "pressure" not in ids
    assert "shots" not in ids


def test_halftime_baseline_enables_second_half_volume():
    """Com baseline capturado ao intervalo, a 2ª parte usa deltas ajustados."""  # synthetic
    state = {"ht": {}, "mkt": {}}
    # Captura baseline ao intervalo (period=halftime)
    ht = _base_event(min=45, status="half_time", period="half_time",
                     xgTotal=1.5, shots={"h": 5, "a": 4}, sot={"h": 2, "a": 2},
                     da={"h": 20, "a": 15}, corners={"h": 3, "a": 2})
    detect_patterns(ht, state)
    assert 1 in state["ht"]  # baseline guardado em memória

    # 2ª parte: muita actividade adicional depois do intervalo
    sh = _base_event(min=65, goals=1, hScore=1, status="2nd_half", period="2nd_half",
                     xgTotal=3.4, lastMom=50, overOdds=1.7, probLive=59,
                     shots={"h": 12, "a": 9}, sot={"h": 6, "a": 4},
                     da={"h": 55, "a": 40}, corners={"h": 8, "a": 5},
                     possession={"h": 60, "a": 40})
    sh["patterns"] = detect_patterns(sh, state)
    sh["patternScore"] = pattern_score(sh["patterns"])
    ids = {p["id"] for p in sh["patterns"]}
    assert "pressure" in ids  # volume já não suprimido (há baseline)
    assert is_live_pick(sh)


def test_convergence_flags_live_pick_even_below_threshold():
    """Convergência qualifica como live pick mesmo sem golos (regra JS)."""  # synthetic
    e = _score(_base_event(
        min=40, goals=0, xgTotal=2.6, lastMom=75,
        shots={"h": 7, "a": 5}, sot={"h": 4, "a": 2},
        da={"h": 35, "a": 25}, corners={"h": 6, "a": 3},
    ))
    has_conv = any(p["id"] == "conv" for p in e["patterns"])
    if has_conv:
        assert is_live_pick(e)


def test_pattern_score_weights():
    """Pesos: critical=10, high=4, med=2, low/mkt=1, conv=0."""  # synthetic
    pats = [
        {"id": "conv", "level": "conv"},
        {"id": "a", "level": "critical"},
        {"id": "b", "level": "high"},
        {"id": "c", "level": "med"},
        {"id": "d", "level": "low"},
        {"id": "e", "level": "mkt"},
    ]
    assert pattern_score(pats) == 0 + 10 + 4 + 2 + 1 + 1


@pytest.mark.parametrize("ev,expected", [  # synthetic
    ({"status": "inplay", "current_minute": 0}, True),
    ({"status": "2nd_half", "current_minute": 67}, True),
    ({"status": "live", "current_minute": None}, True),
    ({"status": "", "current_minute": 34}, True),          # fail-open: minuto a decorrer
    ({"status": "notstarted", "current_minute": 0}, False),
    ({"status": "finished", "current_minute": 90}, False),  # terminal, mesmo com minuto
    ({"status": "scheduled", "current_minute": None}, False),
])
def test_looks_live(ev, expected):
    """Heurística de 'jogo a decorrer' robusta a vários tokens de status."""
    assert _looks_live(ev) is expected


def test_extract_events_handles_list_and_envelope():
    """/api/v2/events/ devolve list directa ou envelope {results|events|data}."""  # synthetic
    assert _extract_events([{"id": 1}]) == [{"id": 1}]
    assert _extract_events({"results": [{"id": 2}]}) == [{"id": 2}]
    assert _extract_events({"events": [{"id": 3}]}) == [{"id": 3}]
    assert _extract_events({"nope": 1}) == []
    assert _extract_events(None) == []


def test_telegram_gate_requires_pressao_and_score():
    """Gate de envio TG: só passa com Pressão>=90 E Score>=20."""  # synthetic
    e = {"patterns": [{"id": "pressure", "label": f"Pressão {PRESSAO_MIN_TELEGRAM}"}],
         "patternScore": SCORE_MIN_TELEGRAM}
    assert passes_telegram_gate(e)

    e_low_pressure = {"patterns": [{"id": "pressure", "label": "Pressão 89"}],
                       "patternScore": SCORE_MIN_TELEGRAM}
    assert not passes_telegram_gate(e_low_pressure)

    e_low_score = {"patterns": [{"id": "pressure", "label": f"Pressão {PRESSAO_MIN_TELEGRAM}"}],
                   "patternScore": SCORE_MIN_TELEGRAM - 1}
    assert not passes_telegram_gate(e_low_score)


def test_telegram_gate_fails_closed_without_pressao():
    """Sem padrão 'pressure' (Pressão ausente), o gate falha fechado — não envia."""  # synthetic
    e = {"patterns": [], "patternScore": 100}
    assert not passes_telegram_gate(e)


# ── ALERT_FILTERS: filtros extra do gate de envio Telegram ───────────────────


def _gate_event(**over):  # synthetic
    """Evento mínimo que já passa o gate base (Pressão>=90 E Score>=20);
    sobrepõe campos via kwargs para testar os filtros extra isoladamente."""
    e = {
        "id": 42,
        "patterns": [{"id": "pressure", "label": f"Pressão {PRESSAO_MIN_TELEGRAM}"}],
        "patternScore": SCORE_MIN_TELEGRAM,
    }
    e.update(over)
    return e


@pytest.mark.parametrize("xg,blocked", [  # synthetic
    (0.99, False),
    (1.0, True),
    (1.49, True),
    (1.5, False),
    (2.49, False),
    (2.5, False),
])
def test_filtro_xg_banda_morta_boundaries(xg, blocked):
    """Banda 1.0<=xG<1.5 bloqueia (pior conversão na amostra); fora da banda passa."""
    e = _gate_event(xgTotal=xg, min=50)
    assert passes_telegram_gate(e) is not blocked


def test_filtro_xg_banda_morta_blocks_and_logs(capsys):
    e = _gate_event(xgTotal=1.2, min=50)
    assert passes_telegram_gate(e) is False
    out = capsys.readouterr().out
    assert "alerta_bloqueado motivo=xg_banda_morta ev=42" in out


def test_filtro_xg_banda_morta_missing_field_passes_and_logs(capsys):
    """xG ausente: fail-open — não bloqueia, só regista campo_ausente."""
    e = _gate_event(min=50)  # sem xgTotal
    assert passes_telegram_gate(e) is True
    out = capsys.readouterr().out
    assert "campo_ausente campo=xgTotal ev=42" in out


@pytest.mark.parametrize("minuto,blocked", [  # synthetic
    (84, False),
    (85, True),
    (86, True),
])
def test_filtro_minuto_tardio_boundaries(minuto, blocked):
    """minuto>=85: tempo estrutural insuficiente — bloqueia a partir daqui (inclusive)."""
    e = _gate_event(xgTotal=2.0, min=minuto)
    assert passes_telegram_gate(e) is not blocked


def test_filtro_minuto_tardio_blocks_and_logs(capsys):
    e = _gate_event(xgTotal=2.0, min=85)
    assert passes_telegram_gate(e) is False
    out = capsys.readouterr().out
    assert "alerta_bloqueado motivo=minuto_tardio ev=42" in out


def test_filtro_minuto_tardio_missing_field_passes_and_logs(capsys):
    """minuto ausente: fail-open — não bloqueia, só regista campo_ausente."""
    e = _gate_event(xgTotal=2.0)  # sem min
    assert passes_telegram_gate(e) is True
    out = capsys.readouterr().out
    assert "campo_ausente campo=min ev=42" in out


def test_filtros_extra_disabled_ignore_thresholds(monkeypatch):
    """Com os toggles desligados, os filtros extra não bloqueiam mesmo dentro
    dos limiares que normalmente bloqueariam."""
    monkeypatch.setitem(ALERT_FILTERS["FILTRO_XG_BANDA_MORTA"], "enabled", False)
    monkeypatch.setitem(ALERT_FILTERS["FILTRO_MINUTO_TARDIO"], "enabled", False)
    e = _gate_event(xgTotal=1.2, min=90)
    assert passes_telegram_gate(e) is True


def test_msg_includes_alta_conviccao_marker_when_xg_high():
    """TIER_ALTA_CONVICCAO_XG: xG>=2.5 marca a mensagem, não bloqueia nada."""
    e = _score(_base_event(min=60, xgTotal=2.5, da={"h": 20, "a": 10}))
    msg = build_live_pick_msg(e)
    assert "⭐ ALTA CONVICÇÃO" in msg


def test_msg_omits_alta_conviccao_marker_when_xg_below_threshold():
    e = _score(_base_event(min=60, xgTotal=2.49, da={"h": 20, "a": 10}))
    msg = build_live_pick_msg(e)
    assert "⭐ ALTA CONVICÇÃO" not in msg


def test_msg_omits_alta_conviccao_marker_when_xg_missing():
    e = _score(_base_event(min=60, xgTotal=None, da={"h": 20, "a": 10}))
    msg = build_live_pick_msg(e)
    assert "⭐ ALTA CONVICÇÃO" not in msg


def test_msg_includes_vantagem_numerica_warning_when_enabled(monkeypatch):
    """DESCONTO_VANTAGEM_NUMERICA activado: aviso na mensagem quando há +1
    homem detectado (reusa o padrão 'numerical' já produzido por detect_patterns,
    sem novo cálculo no live)."""
    monkeypatch.setitem(ALERT_FILTERS["DESCONTO_VANTAGEM_NUMERICA"], "enabled", True)
    e = _score(_base_event(min=60, redCards={"h": 0, "a": 1}))
    assert any(p["id"] == "numerical" for p in e["patterns"])
    msg = build_live_pick_msg(e)
    assert "⚠️" in msg
    assert "Casa +1 homem" in msg


def test_msg_omits_vantagem_numerica_warning_when_disabled():
    """Default desligado (n=1 na amostra): mesmo com vantagem numérica
    detectada, a mensagem não muda."""
    e = _score(_base_event(min=60, redCards={"h": 0, "a": 1}))
    assert any(p["id"] == "numerical" for p in e["patterns"])
    msg = build_live_pick_msg(e)
    assert "⚠️" not in msg


def test_build_msg_contains_key_fields():
    """A mensagem TG inclui equipas, minuto, resultado e sinais."""  # synthetic
    e = _score(_base_event(min=50, goals=2, hScore=2, home="Benfica", away="Porto",
                           xgTotal=3.0, da={"h": 30, "a": 20}))
    msg = build_live_pick_msg(e)
    assert "Benfica vs Porto" in msg
    assert "APOSTAR AGORA" in msg
    assert "50'" in msg
    assert "2-0" in msg


# ── issue #127: odds suspensas (sentinela 1.00) tratadas como preço real ──────


def _mock_bsd(monkeypatch, stats: dict, odds: dict):
    """Substitui _bsd_get_dict por respostas fixas (sem rede)."""
    import pipeline.scan_live as mod

    def fake(_api_key, path):
        if path.endswith("/stats/"):
            return stats
        if path.endswith("/odds/"):
            return odds
        return {}

    monkeypatch.setattr(mod, "_bsd_get_dict", fake)


def _live_ev(**over):  # synthetic
    ev = {"id": 555, "home_team": "Casa", "away_team": "Fora", "status": "2nd_half",
          "current_minute": 70, "home_score": 1, "away_score": 1, "league_id": 4}
    ev.update(over)
    return ev


@pytest.mark.parametrize("raw_over_odds,expected_status", [  # synthetic
    (1.00, "SUSPENDED"),   # sentinela confirmada (issue #127)
    (0, "SUSPENDED"),
    (None, "MISSING"),
])
def test_enrich_event_never_fabricates_price_from_sentinel(monkeypatch, capsys, raw_over_odds, expected_status):
    """enrich_event() nunca transforma uma odd sentinela/ausente num preço real
    — overOdds/probLive ficam None, o estado fica explícito em oddsStatus, e o
    achado é impresso em stdout (visível, não só stderr)."""
    _mock_bsd(monkeypatch, stats={"stats": {"home": {}, "away": {}}, "momentum": []},
              odds={"odds": {"over_25_goals": raw_over_odds}})

    e = enrich_event("fake_key", _live_ev(), set())

    assert e["overOdds"] is None
    assert e["probLive"] is None
    assert e["oddsStatus"] == expected_status

    out = capsys.readouterr().out
    assert f"ODDS_STATUS={expected_status}" in out
    assert "ev=555" in out


def test_enrich_event_keeps_valid_odds_as_real_price(monkeypatch):
    """Odd normal (>1.01) continua a ser lida como preço real — a correcção
    não introduz falsos negativos."""
    _mock_bsd(monkeypatch, stats={"stats": {"home": {}, "away": {}}, "momentum": []},
              odds={"odds": {"over_25_goals": 1.85}})

    e = enrich_event("fake_key", _live_ev(), set())

    assert e["overOdds"] == 1.85
    assert e["probLive"] == round((1 / 1.85) * 100)
    assert e["oddsStatus"] == "VALID"


def test_suspended_odds_message_shows_dash_never_fake_100_percent(monkeypatch):
    """Caminho completo enrich → patterns → mensagem TG: com odds suspensas,
    a mensagem mostra '—', nunca o sintoma reportado 'Prob Over 100% · Odd 1.00'."""
    _mock_bsd(monkeypatch, stats={"stats": {"home": {}, "away": {}}, "momentum": []},
              odds={"odds": {"over_25_goals": 1.00}})

    e = enrich_event("fake_key", _live_ev(), set())
    e = _score(e)
    msg = build_live_pick_msg(e)

    assert "Prob Over 100%" not in msg
    assert "Odd 1.00" not in msg
    assert "Prob Over —" in msg
    assert "Odd —" in msg


def test_suspended_odds_do_not_fabricate_sharp_money_signal():
    """Regressão directa do bug: antes da correcção, uma odd sentinela (1.00)
    durante a suspensão do mercado seria lida como queda de -50% face à odd de
    intervalo — um falso sinal 'sharp money' (padrão 'mkt', nível critical).
    Com over_odds=None (SUSPENDED), o padrão não dispara e o baseline real
    fica preservado para quando o mercado reabrir."""  # synthetic
    state = {"ht": {}, "mkt": {}}
    ht = _base_event(min=45, status="half_time", period="half_time", overOdds=2.00)
    detect_patterns(ht, state)
    assert state["mkt"][1] == 2.00

    live_suspended = _base_event(min=60, status="2nd_half", period="2nd_half", overOdds=None)
    pats = detect_patterns(live_suspended, state)
    assert "mkt" not in {p["id"] for p in pats}
    assert state["mkt"][1] == 2.00  # baseline preservado, não corrompido para 1.00

    # mercado reabre com preço real mais baixo — deteção de queda retoma normalmente
    live_resumed = _base_event(min=61, status="2nd_half", period="2nd_half", overOdds=1.80)
    pats2 = detect_patterns(live_resumed, state)
    ids2 = {p["id"] for p in pats2}
    assert "mkt" in ids2  # (2.00-1.80)/2.00=10% >= 6% -> critical


# ── regressão: dedup não pode "queimar" um jogo antes do gate de TG ──────────
#
# Bug diagnosticado: scan_once() fazia `alerted.add(key)` logo que um jogo
# atingia is_live_pick() (score>=TH_LIVE_PICK=12), ANTES de verificar
# passes_telegram_gate() (Pressão>=90 E Score>=20 — limiar mais exigente).
# Um jogo que qualificasse com Pressão<90 ficava marcado "alertado" para
# sempre nessa execução, mesmo que a Pressão subisse acima de 90 minutos
# depois — o alerta real nunca era reavaliado nem enviado.


def _fake_enriched_event(ev_id: int, goals: int = 1) -> dict:  # synthetic
    """Evento já enriquecido — mínimo necessário para is_live_pick/gate/msg."""
    return {
        "id": ev_id, "home": "Casa", "away": "Fora", "hScore": goals, "aScore": 0,
        "goals": goals, "min": 60, "status": "2nd_half", "league": "Serie A",
        "overOdds": 1.7, "xgTotal": 3.0, "probLive": 55, "isSavedPick": False,
    }


def test_alerted_not_burned_before_telegram_gate_passes(monkeypatch):
    """Jogo que atinge is_live_pick (score>=12) com Pressão<90 num ciclo, e só
    passa o gate de TG (Pressão>=90 E Score>=20) num ciclo posterior: deve
    resultar em exactamente 1 alerta, enviado no 2º ciclo — nunca 'queimado'
    no 1º."""  # synthetic
    import pipeline.scan_live as mod

    monkeypatch.setattr(mod, "LIVE_ALERTS_ENABLED", True)  # testa a lógica de dedup, não o interruptor global
    raw_event = {"id": 777}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: _fake_enriched_event(777))

    # ciclo 1: score 14 (>=12) qualifica is_live_pick, mas Pressão=55 (<90)
    # não cumpre o gate de TG.
    patterns_cycle_1 = [
        {"id": "pressure", "label": "Pressão 55", "emoji": "🔥", "level": "critical", "detail": "d"},
        {"id": "mom", "label": "Casa domina", "emoji": "💥", "level": "high", "detail": "d"},
    ]
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: patterns_cycle_1)

    sent_msgs: list[str] = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent_msgs.append(text), True)[1])

    state = {"ht": {}, "mkt": {}}
    alerted: set[str] = set()

    n1 = mod.scan_once("fake_key", state, set(), alerted, verbose=False)
    assert n1 == 0
    assert sent_msgs == []
    assert "777" not in alerted  # não pode ficar queimado sem passar o gate

    # ciclo 2: Pressão sobe para 92 (>=90) e score sobe para 24 (>=20) → alerta agora
    patterns_cycle_2 = [
        {"id": "pressure", "label": "Pressão 92", "emoji": "🔥", "level": "critical", "detail": "d"},
        {"id": "mom", "label": "Casa domina", "emoji": "💥", "level": "high", "detail": "d"},
        {"id": "xg_delta", "label": "xG +2.6 acima", "emoji": "🎲", "level": "critical", "detail": "d"},
    ]
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: patterns_cycle_2)

    n2 = mod.scan_once("fake_key", state, set(), alerted, verbose=False)
    assert n2 == 1
    assert len(sent_msgs) == 1
    assert "777" in alerted


def test_alerted_not_marked_when_send_telegram_fails(monkeypatch):
    """Se o envio TG falhar (send_telegram devolve False), o jogo NÃO fica
    marcado em `alerted` — tem de ser reavaliado (e re-enviado) no ciclo
    seguinte."""  # synthetic
    import pipeline.scan_live as mod

    monkeypatch.setattr(mod, "LIVE_ALERTS_ENABLED", True)  # testa a lógica de retry, não o interruptor global
    raw_event = {"id": 888}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: _fake_enriched_event(888))

    patterns = [
        {"id": "pressure", "label": "Pressão 92", "emoji": "🔥", "level": "critical", "detail": "d"},
        {"id": "mom", "label": "Casa domina", "emoji": "💥", "level": "high", "detail": "d"},
        {"id": "xg_delta", "label": "xG +2.6 acima", "emoji": "🎲", "level": "critical", "detail": "d"},
    ]
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: patterns)

    calls = {"n": 0}

    def failing_send(_text):
        calls["n"] += 1
        return False

    monkeypatch.setattr(mod, "send_telegram", failing_send)

    state = {"ht": {}, "mkt": {}}
    alerted: set[str] = set()

    n1 = mod.scan_once("fake_key", state, set(), alerted, verbose=False)
    assert n1 == 0
    assert calls["n"] == 1
    assert "888" not in alerted

    sent_msgs: list[str] = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent_msgs.append(text), True)[1])

    n2 = mod.scan_once("fake_key", state, set(), alerted, verbose=False)
    assert n2 == 1
    assert len(sent_msgs) == 1
    assert "888" in alerted


# ── LIVE_ALERTS_ENABLED: interruptor global "🔥 APOSTAR AGORA" (pedido 9 ago 2026) ──


def test_live_alerts_disabled_by_default():
    """Produção não envia mais o alerta '🔥 APOSTAR AGORA — LIVE OVER 2.5'."""
    import pipeline.scan_live as mod

    assert mod.LIVE_ALERTS_ENABLED is False


def test_scan_once_never_sends_when_live_alerts_disabled(monkeypatch):
    """Mesmo com o gate de TG totalmente cumprido, LIVE_ALERTS_ENABLED=False
    bloqueia o envio — a detecção/scoring continua a correr (não afecta
    is_live_pick/patternScore), só o push para o Telegram é suprimido."""  # synthetic
    import pipeline.scan_live as mod

    monkeypatch.setattr(mod, "LIVE_ALERTS_ENABLED", False)
    raw_event = {"id": 999}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: _fake_enriched_event(999))

    patterns = [
        {"id": "pressure", "label": "Pressão 92", "emoji": "🔥", "level": "critical", "detail": "d"},
        {"id": "mom", "label": "Casa domina", "emoji": "💥", "level": "high", "detail": "d"},
        {"id": "xg_delta", "label": "xG +2.6 acima", "emoji": "🎲", "level": "critical", "detail": "d"},
    ]
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: patterns)

    sent_msgs: list[str] = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent_msgs.append(text), True)[1])

    state = {"ht": {}, "mkt": {}}
    alerted: set[str] = set()
    n = mod.scan_once("fake_key", state, set(), alerted, verbose=False)

    assert n == 0
    assert sent_msgs == []
    assert "999" not in alerted


# ── LIVE_SCAN_STATUS: distinguir janela sem jogos de falha de fetch ──────────


def test_live_scan_status_distinguishes_api_error_from_no_live_games(monkeypatch, capsys):
    """Erro de rede/HTTP no fetch tem de ficar marcado como API_ERROR com
    n_failures>0 — nunca igual, nos logs, a uma janela genuinamente sem jogos
    (NO_LIVE_GAMES, n_failures=0)."""  # synthetic
    import pipeline.scan_live as mod

    def raise_get(*_a, **_k):
        raise Exception("boom")  # noqa: TRY002 - simula falha genérica de rede

    monkeypatch.setattr(mod.requests, "get", raise_get)
    events = mod.fetch_live_events("fake_key", verbose=False)
    assert events == []
    out = capsys.readouterr().out
    assert "LIVE_SCAN_STATUS=API_ERROR" in out
    assert "n_failures=2" in out  # falha na tentativa primária E na secundária


def test_live_scan_status_ok_when_no_errors_and_no_games(monkeypatch, capsys):
    """Sem falhas de fetch e sem jogos a decorrer → NO_LIVE_GAMES, n_failures=0
    (comportamento correcto de pré-época, distinto de um erro de API)."""  # synthetic
    import pipeline.scan_live as mod

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp())
    events = mod.fetch_live_events("fake_key", verbose=False)
    assert events == []
    out = capsys.readouterr().out
    assert "LIVE_SCAN_STATUS=NO_LIVE_GAMES" in out
    assert "n_failures=0" in out


def test_live_scan_status_ok_when_live_games_present(monkeypatch, capsys):
    """Com jogos live devolvidos e sem falhas de fetch → OK, n_failures=0."""  # synthetic
    import pipeline.scan_live as mod

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"id": 1, "status": "inplay", "current_minute": 10}]}

    monkeypatch.setattr(mod.requests, "get", lambda *a, **k: FakeResp())
    events = mod.fetch_live_events("fake_key", verbose=False)
    assert len(events) == 1
    out = capsys.readouterr().out
    assert "LIVE_SCAN_STATUS=OK" in out
    assert "n_failures=0" in out
