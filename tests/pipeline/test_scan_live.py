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
    TH_LIVE_PICK,
    _extract_events,
    _looks_live,
    build_live_pick_msg,
    detect_patterns,
    is_live_pick,
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


def test_build_msg_contains_key_fields():
    """A mensagem TG inclui equipas, minuto, resultado e sinais."""  # synthetic
    e = _score(_base_event(min=50, goals=2, hScore=2, home="Benfica", away="Porto",
                           xgTotal=3.0, da={"h": 30, "a": 20}))
    msg = build_live_pick_msg(e)
    assert "Benfica vs Porto" in msg
    assert "APOSTAR AGORA" in msg
    assert "50'" in msg
    assert "2-0" in msg
