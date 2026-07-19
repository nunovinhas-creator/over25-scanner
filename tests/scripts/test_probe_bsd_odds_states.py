"""
tests/scripts/test_probe_bsd_odds_states.py
---------------------------------------------
Testes das funções puras de scripts/probe_bsd_odds_states.py (bucketing por
estado de evento e anonimização de payload). Não testam chamadas de rede —
o script em si só corre com BSD_API_KEY + rede reais (ver
docs/ODDS_VALIDATION.md, Ponto 2: nenhum payload real foi capturado nesta
sessão por falta de ambas).

Todos os eventos aqui são sintéticos (# synthetic).
"""

from __future__ import annotations

import pytest

from scripts.probe_bsd_odds_states import (
    BUCKET_FINISHED,
    BUCKET_HALFTIME,
    BUCKET_INPLAY,
    BUCKET_NOTSTARTED,
    BUCKET_OTHER,
    _anonymize,
    _bucket_for,
    main,
)


@pytest.mark.parametrize("status,minute,expected", [  # synthetic
    ("notstarted", None, BUCKET_NOTSTARTED),
    ("not_started", None, BUCKET_NOTSTARTED),
    ("scheduled", None, BUCKET_NOTSTARTED),
    ("inplay", 55, BUCKET_INPLAY),
    ("1H", 20, BUCKET_INPLAY),
    ("2ndHalf", 70, BUCKET_INPLAY),
    ("halftime", 45, BUCKET_HALFTIME),
    ("HT", 45, BUCKET_HALFTIME),
    ("finished", 90, BUCKET_FINISHED),
    ("FT", 90, BUCKET_FINISHED),
    ("", 34, BUCKET_INPLAY),   # fail-open: minuto a decorrer sem status conhecido
    ("", None, BUCKET_OTHER),  # sem status nem minuto — não classificável
    ("weird_unknown_status", None, BUCKET_OTHER),
])
def test_bucket_for(status, minute, expected):
    ev = {"status": status, "current_minute": minute}
    assert _bucket_for(ev) == expected


def test_anonymize_strips_team_and_league_names_keeps_rest():
    ev = {
        "id": 123,
        "home_team": "Team A",
        "away_team": "Team B",
        "league_name": "Some League",
        "status": "inplay",
        "current_minute": 55,
    }
    out = _anonymize(ev)
    assert out["home_team"] == "<home_team>"
    assert out["away_team"] == "<away_team>"
    assert out["league_name"] == "<league_name>"
    # campos não sensíveis mantêm-se intactos
    assert out["id"] == 123
    assert out["status"] == "inplay"
    assert out["current_minute"] == 55


def test_anonymize_does_not_mutate_input():
    ev = {"home_team": "Team A"}
    _anonymize(ev)
    assert ev["home_team"] == "Team A"


def test_main_fails_closed_without_api_key(monkeypatch, capsys):
    """Invariante do Ponto 2: sem BSD_API_KEY, o script aborta em vez de
    inventar payloads — nenhum estado de erro pode parecer sucesso."""
    import scripts.probe_bsd_odds_states as mod

    monkeypatch.setattr(mod, "BSD_API_KEY", "")
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1
