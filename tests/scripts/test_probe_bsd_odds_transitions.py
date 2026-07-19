"""
tests/scripts/test_probe_bsd_odds_transitions.py
----------------------------------------------------
Testes da lógica pura de scripts/probe_bsd_odds_transitions.py: a máquina de
estados TransitionTracker (detecção ANTES/DURANTE/DEPOIS), a anonimização
recursiva e o marker de auto-desligamento. Nada aqui depende de rede — o
polling em si só corre com BSD_API_KEY + rede reais (ver docs/ODDS_VALIDATION.md).

Todos os payloads são sintéticos (# synthetic).
"""

from __future__ import annotations

import pytest

from scripts.probe_bsd_odds_transitions import (
    MAX_TRANSITIONS,
    TransitionTracker,
    _already_captured_suspended,
    _anonymize_payload,
    _extract_market_status,
    _extract_over_odds,
    _is_force_enabled,
    main,
)


def _odds(value, status=None):  # synthetic
    return {"odds": {"over_25_goals": value, "over_25_goals_status": status}}


def test_extract_over_odds_and_market_status():
    assert _extract_over_odds(_odds(1.90)) == 1.90
    assert _extract_over_odds(_odds(1.00)) == 1.00
    assert _extract_over_odds(None) is None
    assert _extract_over_odds({}) is None
    assert _extract_market_status(_odds(1.90, "suspended")) == "suspended"
    assert _extract_market_status(_odds(1.90)) is None


def test_no_transition_on_first_reading():
    t = TransitionTracker()
    assert t.observe("e1", _odds(1.90), "t1") is None
    assert len(t.transitions) == 0


def test_no_transition_on_stable_repeated_readings():
    """Leituras repetidas sem mudança nunca disparam uma transição espúria."""
    t = TransitionTracker()
    for i in range(5):
        assert t.observe("e1", _odds(1.90), f"t{i}") is None
    assert len(t.transitions) == 0


def test_valid_to_suspended_to_valid_captures_before_during_after():
    t = TransitionTracker()
    assert t.observe("e1", _odds(1.90), "t1") is None      # before (armazenado)
    assert t.observe("e1", _odds(1.00), "t2") is None       # during (sentinela) — pending
    record = t.observe("e1", _odds(1.95), "t3")             # after — finaliza
    assert record is not None
    assert record["event_id"] == "e1"
    assert record["before_status"] == "VALID"
    assert record["during_status"] == "SUSPENDED"
    assert record["after_status"] == "VALID"
    assert record["before"] == _odds(1.90)
    assert record["during"] == _odds(1.00)
    assert record["after"] == _odds(1.95)
    assert len(t.transitions) == 1


def test_status_field_only_change_also_triggers_transition():
    """'ou aparece qualquer campo de status diferente' — mesmo sem a odd mudar."""
    t = TransitionTracker()
    t.observe("e1", _odds(1.90, None), "t1")
    assert t.observe("e1", _odds(1.90, "suspended"), "t2") is None  # pending
    record = t.observe("e1", _odds(1.90, None), "t3")
    assert record is not None
    assert record["during_status"] == "SUSPENDED"


def test_suspended_stays_suspended_after_does_not_resolve_back():
    """A transição fecha-se com a leitura seguinte, seja qual for o estado dela."""
    t = TransitionTracker()
    t.observe("e1", _odds(1.90), "t1")
    t.observe("e1", _odds(1.00), "t2")
    record = t.observe("e1", _odds(1.00), "t3")
    assert record is not None
    assert record["before_status"] == "VALID"
    assert record["during_status"] == "SUSPENDED"
    assert record["after_status"] == "SUSPENDED"


def test_events_tracked_independently():
    t = TransitionTracker()
    t.observe("e1", _odds(1.90), "t1")
    t.observe("e2", _odds(2.10), "t1")
    t.observe("e1", _odds(1.00), "t2")   # e1 em transição
    assert t.observe("e2", _odds(2.10), "t2") is None  # e2 continua estável, sem transição
    record = t.observe("e1", _odds(1.90), "t3")
    assert record is not None and record["event_id"] == "e1"


def test_cap_stops_new_transitions_but_keeps_existing():
    t = TransitionTracker(max_transitions=1)
    t.observe("e1", _odds(1.90), "t1")
    t.observe("e1", _odds(1.00), "t2")
    t.observe("e1", _odds(1.90), "t3")  # transição 1/1 — cap atingido
    assert t.is_full()
    assert len(t.transitions) == 1

    # novo evento após o cap: observe() não deve sequer começar a rastrear
    assert t.observe("e2", _odds(1.90), "t4") is None
    assert t.observe("e2", _odds(1.00), "t5") is None
    assert t.observe("e2", _odds(1.90), "t6") is None
    assert len(t.transitions) == 1


def test_default_max_transitions_matches_module_constant():
    assert TransitionTracker().max_transitions == MAX_TRANSITIONS == 5


def test_anonymize_payload_strips_identifying_keys_recursively():
    payload = {
        "event_id": "e1",
        "before": {"home_team": "Team A", "away_team": "Team B", "odds": {"over_25_goals": 1.9}},
        "during": {"nested": [{"league_name": "Some League", "status": "suspended"}]},
    }
    out = _anonymize_payload(payload)
    assert out["before"]["home_team"] == "<home_team>"
    assert out["before"]["away_team"] == "<away_team>"
    assert out["before"]["odds"]["over_25_goals"] == 1.9
    assert out["during"]["nested"][0]["league_name"] == "<league_name>"
    assert out["during"]["nested"][0]["status"] == "suspended"
    # não muta o original
    assert payload["before"]["home_team"] == "Team A"


@pytest.mark.parametrize("records,expected", [  # synthetic
    ([], False),
    ([{"during_status": "VALID"}], False),
    ([{"during_status": "MISSING"}], False),
    ([{"during_status": "SUSPENDED"}], True),
    ([{"during_status": "VALID"}, {"during_status": "SUSPENDED"}], True),
])
def test_already_captured_suspended(records, expected):
    assert _already_captured_suspended(records) is expected


def test_is_force_enabled(monkeypatch):
    monkeypatch.delenv("PROBE_FORCE", raising=False)
    assert _is_force_enabled() is False
    for value in ("1", "true", "True", "yes", "YES"):
        monkeypatch.setenv("PROBE_FORCE", value)
        assert _is_force_enabled() is True
    for value in ("0", "false", "", "no"):
        monkeypatch.setenv("PROBE_FORCE", value)
        assert _is_force_enabled() is False


def test_main_fails_closed_without_api_key(monkeypatch):
    import scripts.probe_bsd_odds_transitions as mod

    monkeypatch.setattr(mod, "BSD_API_KEY", "")
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 1


def test_main_auto_shuts_down_without_api_calls_when_already_captured(monkeypatch, tmp_path, capsys):
    """Invariante central do auto-desligamento: se o estado alvo já foi
    capturado, main() sai sem tentar sequer contactar a BSD (não faz sentido
    gastar chamadas de API numa sondagem que já cumpriu o objectivo)."""
    import scripts.probe_bsd_odds_transitions as mod

    monkeypatch.setattr(mod, "BSD_API_KEY", "fake-key-synthetic")
    monkeypatch.delenv("PROBE_FORCE", raising=False)

    out_path = tmp_path / "probe_odds_transitions.json"
    out_path.write_text('[{"during_status": "SUSPENDED"}]', encoding="utf-8")
    monkeypatch.setattr(mod, "OUTPUT_PATH", out_path)

    def _boom(*args, **kwargs):
        raise AssertionError("main() não devia chamar a rede quando já auto-desligado")

    monkeypatch.setattr(mod, "fetch_live_events", _boom)
    monkeypatch.setattr(mod, "_get", _boom)

    main()  # não deve lançar nem chamar _boom
    assert "auto-desligamento" in capsys.readouterr().out
