"""
tests/pipeline/test_live_shadow.py
-----------------------------------
Testes do MODO SOMBRA (Bloco H1, pipeline/scan_live.py) — regista, sem
enviar Telegram, o que o gate "🔥 APOSTAR AGORA" (passes_telegram_gate)
teria decidido: enviaria, ou bloqueado por qual filtro (xg_banda_morta /
minuto_tardio). Cobre também o settlement (result_over25) e a captura da
odd de fecho (closing_odds_over25) a partir da última odd VALID observada.

Todos os eventos/ficheiros aqui são sintéticos (# synthetic) — nunca dados
de produção. Ficheiros reais (data/live_shadow_alerts.json) e
git_commit_push são sempre monkeypatched: nenhum destes testes toca no
disco do repositório nem invoca git ou Telegram.
"""

from __future__ import annotations

import json

import pytest

import pipeline.scan_live as mod
from pipeline.scan_live import (
    LIVE_ALERTS_ENABLED,
    _telegram_gate_block_reason,
    build_shadow_alert,
    compute_shadow_result_updates,
    load_shadow_state,
    passes_telegram_gate,
    run_shadow_alerts,
)


def _shadow_event(**over):  # synthetic
    """Evento já enriquecido + pontuado (patterns/patternScore), como chega
    a run_shadow_alerts() a partir de enriched_events em scan_once()."""
    e = {
        "id": 1, "home": "Casa", "away": "Fora", "hScore": 1, "aScore": 0,
        "goals": 1, "min": 60, "period": "2nd_half", "league": "Serie A",
        "xgTotal": 2.0, "overOdds": 1.75, "oddsStatus": "VALID", "probLive": 55,
        "patterns": [
            {"id": "pressure", "label": "Pressão 95", "emoji": "🔥", "level": "critical", "detail": "d"},
        ],
        "patternScore": 20, "isSavedPick": False,
    }
    e.update(over)
    return e


# ── _telegram_gate_block_reason ──────────────────────────────────────────


def test_block_reason_none_when_gate_base_blocks():
    """Pressão baixa (gate base) não é do âmbito do Bloco H1 — só
    xg_banda_morta/minuto_tardio interessam."""
    e = _shadow_event(patterns=[{"id": "pressure", "label": "Pressão 50", "level": "high", "emoji": "🔥", "detail": "d"}])
    assert not passes_telegram_gate(e)
    assert _telegram_gate_block_reason(e) is None


def test_block_reason_none_when_pressao_absent():
    e = _shadow_event(patterns=[])
    assert not passes_telegram_gate(e)
    assert _telegram_gate_block_reason(e) is None


def test_block_reason_xg_banda_morta():
    e = _shadow_event(xgTotal=1.2)  # 1.0<=xg<1.5 — banda morta
    assert not passes_telegram_gate(e)
    assert _telegram_gate_block_reason(e) == "xg_banda_morta"


def test_block_reason_minuto_tardio():
    e = _shadow_event(min=87)  # >=85
    assert not passes_telegram_gate(e)
    assert _telegram_gate_block_reason(e) == "minuto_tardio"


def test_block_reason_none_when_gate_would_pass():
    e = _shadow_event()
    assert passes_telegram_gate(e)
    assert _telegram_gate_block_reason(e) is None


# ── build_shadow_alert ────────────────────────────────────────────────────


def test_build_shadow_alert_would_send_fields():
    e = _shadow_event(id=999)
    entry = build_shadow_alert(e, blocked_by=None)
    assert entry["event_id"] == "999"
    assert entry["id"].startswith("999_shadow_")
    assert entry["casa"] == "Casa" and entry["fora"] == "Fora" and entry["liga"] == "Serie A"
    assert entry["min"] == 60 and entry["goals"] == 1 and entry["score"] == "1-0"
    assert entry["xg_total"] == 2.0
    assert entry["pattern_score"] == 20
    assert entry["pressao"] == 95.0
    assert entry["odds_live"] == 1.75
    assert entry["odds_status"] == "VALID"
    assert entry["blocked_by"] is None
    assert entry["result_over25"] == "" and entry["closing_odds_over25"] is None


def test_build_shadow_alert_blocked_fields():
    e = _shadow_event(xgTotal=1.2)
    entry = build_shadow_alert(e, blocked_by="xg_banda_morta")
    assert entry["blocked_by"] == "xg_banda_morta"


# ── compute_shadow_result_updates ────────────────────────────────────────


def test_shadow_updates_marks_win_with_closing_odds():
    unresolved = {"1": ["1_shadow_100"]}
    events = [_shadow_event(id=1, goals=3, hScore=2, aScore=1, min=70)]
    updates = compute_shadow_result_updates(events, unresolved, {"1": 1.3})
    assert updates == {
        "1_shadow_100": {"result_over25": "WIN", "final_score": "2-1", "result_at_min": 70,
                          "closing_odds_over25": 1.3},
    }


def test_shadow_updates_marks_loss_without_closing_odds_when_unknown():
    """Nunca inventa uma odd de fecho — se a BSD nunca devolveu uma odd VALID
    para este evento, o campo fica de fora do patch (não é 'gravado como 0')."""
    unresolved = {"1": ["1_shadow_100"]}
    events = [_shadow_event(id=1, goals=1, hScore=1, aScore=0, min=90, period="FT")]
    updates = compute_shadow_result_updates(events, unresolved, {})
    assert updates == {"1_shadow_100": {"result_over25": "LOSS", "final_score": "1-0", "result_at_min": 90}}


def test_shadow_updates_applies_same_patch_to_multiple_ids_of_same_event():
    """Um jogo pode ter mais que um registo sombra pendente (ex.: bloqueado
    por xg_banda_morta numa fase, depois passaria noutra) — o resultado
    aplica-se a todos."""
    unresolved = {"1": ["1_shadow_a", "1_shadow_b"]}
    events = [_shadow_event(id=1, goals=3, hScore=3, aScore=0, min=75)]
    updates = compute_shadow_result_updates(events, unresolved, {})
    assert set(updates.keys()) == {"1_shadow_a", "1_shadow_b"}
    assert updates["1_shadow_a"]["result_over25"] == "WIN"


def test_shadow_updates_ignores_still_running_games():
    unresolved = {"1": ["1_shadow_100"]}
    events = [_shadow_event(id=1, goals=1, min=60)]
    assert compute_shadow_result_updates(events, unresolved, {}) == {}


def test_shadow_updates_ignores_events_without_pending_entry():
    events = [_shadow_event(id=2, goals=3, min=70)]
    assert compute_shadow_result_updates(events, {}, {}) == {}


# ── load_shadow_state ─────────────────────────────────────────────────────


def test_load_shadow_state_builds_keys_unresolved_and_last_odds(monkeypatch, tmp_path):
    shadow_file = tmp_path / "live_shadow_alerts.json"
    shadow_file.write_text(json.dumps([
        {"id": "1_shadow_a", "event_id": "1", "blocked_by": None, "result_over25": "",
         "odds_status": "VALID", "odds_live": 1.8},
        {"id": "1_shadow_b", "event_id": "1", "blocked_by": "minuto_tardio", "result_over25": "",
         "odds_status": "SUSPENDED", "odds_live": None},
        {"id": "2_shadow_c", "event_id": "2", "blocked_by": None, "result_over25": "WIN",
         "odds_status": "VALID", "odds_live": 1.5},
    ]), encoding="utf-8")
    monkeypatch.setattr(mod, "_SHADOW_PATH", shadow_file)

    state = load_shadow_state()
    assert state["logged_keys"] == {"1:send", "1:minuto_tardio", "2:send"}
    assert state["unresolved"] == {"1": ["1_shadow_a", "1_shadow_b"]}  # "2" já resolvido, fora
    assert state["last_valid_odds"] == {"1": 1.8}  # só a entrada VALID


def test_load_shadow_state_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_SHADOW_PATH", tmp_path / "does_not_exist.json")
    state = load_shadow_state()
    assert state == {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}


# ── run_shadow_alerts — ciclo completo ────────────────────────────────────


@pytest.fixture
def isolated_shadow_env(tmp_path, monkeypatch):
    shadow_file = tmp_path / "live_shadow_alerts.json"
    monkeypatch.setattr(mod, "_SHADOW_PATH", shadow_file)
    commits = []
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: (commits.append((files, msg)), True)[1])
    discards = []
    monkeypatch.setattr(mod, "git_discard_local_changes", lambda files: discards.append(files))

    def _no_telegram(*_a, **_k):
        raise AssertionError("modo sombra nunca deve chamar send_telegram()")

    monkeypatch.setattr(mod, "send_telegram", _no_telegram)
    return {"shadow_file": shadow_file, "commits": commits, "discards": discards}


def test_run_shadow_alerts_logs_would_send_entry(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    added = run_shadow_alerts([_shadow_event(id=1)], shadow_state)

    assert added == 1
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert len(saved) == 1
    assert saved[0]["event_id"] == "1" and saved[0]["blocked_by"] is None
    assert "1:send" in shadow_state["logged_keys"]
    assert isolated_shadow_env["commits"]


def test_run_shadow_alerts_logs_xg_banda_morta_block(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    added = run_shadow_alerts([_shadow_event(id=1, xgTotal=1.2)], shadow_state)

    assert added == 1
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert saved[0]["blocked_by"] == "xg_banda_morta"


def test_run_shadow_alerts_logs_minuto_tardio_block(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    added = run_shadow_alerts([_shadow_event(id=1, min=87)], shadow_state)

    assert added == 1
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert saved[0]["blocked_by"] == "minuto_tardio"


def test_run_shadow_alerts_does_not_log_gate_base_block(isolated_shadow_env):
    """Bloqueio por Pressão/Score (gate base) fica fora do âmbito pedido —
    não se regista no ficheiro sombra."""
    e = _shadow_event(id=1, patterns=[
        {"id": "pressure", "label": "Pressão 50", "level": "high", "emoji": "🔥", "detail": "d"},
    ])
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    added = run_shadow_alerts([e], shadow_state)

    assert added == 0
    assert not isolated_shadow_env["shadow_file"].exists()
    assert isolated_shadow_env["commits"] == []


def test_run_shadow_alerts_never_duplicates_same_event_and_category(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    n1 = run_shadow_alerts([_shadow_event(id=1)], shadow_state)
    n2 = run_shadow_alerts([_shadow_event(id=1)], shadow_state)

    assert n1 == 1 and n2 == 0
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert len(saved) == 1


def test_run_shadow_alerts_logs_both_categories_for_same_event_separately(isolated_shadow_env):
    """O mesmo jogo pode aparecer bloqueado numa fase e "enviaria" noutra —
    são categorias diferentes, cada uma registada uma vez."""
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    run_shadow_alerts([_shadow_event(id=1, min=50, xgTotal=1.2)], shadow_state)  # blocked xg
    added2 = run_shadow_alerts([_shadow_event(id=1, min=60, xgTotal=2.0)], shadow_state)  # would send

    assert added2 == 1
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert len(saved) == 2
    reasons = {s["blocked_by"] for s in saved}
    assert reasons == {"xg_banda_morta", None}


def test_run_shadow_alerts_survives_restart_via_persisted_dedup(isolated_shadow_env):
    shadow_state_1 = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    run_shadow_alerts([_shadow_event(id=1)], shadow_state_1)

    shadow_state_2 = load_shadow_state()
    n2 = run_shadow_alerts([_shadow_event(id=1)], shadow_state_2)

    assert n2 == 0


def test_run_shadow_alerts_settles_result_and_captures_closing_odds(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    run_shadow_alerts([_shadow_event(id=1, min=60, overOdds=1.75)], shadow_state)

    # jogo termina: 1 golo só (LOSS), última odds VALID vista foi 1.30.
    # patterns=[]/patternScore=0 -> deixa de ser candidato a NOVA entrada
    # (is_live_pick=False), isolando este cenário ao caminho de settlement.
    added = run_shadow_alerts(
        [_shadow_event(id=1, goals=1, hScore=1, aScore=0, min=90, period="FT",
                       overOdds=1.30, patterns=[], patternScore=0)],
        shadow_state,
    )

    assert added == 0  # não é entrada nova, é update
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert len(saved) == 1
    assert saved[0]["result_over25"] == "LOSS"
    assert saved[0]["final_score"] == "1-0"
    assert saved[0]["closing_odds_over25"] == 1.3
    assert "1" not in shadow_state["unresolved"]


def test_run_shadow_alerts_win_settlement(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    run_shadow_alerts([_shadow_event(id=1, min=50, goals=1)], shadow_state)
    run_shadow_alerts([_shadow_event(id=1, min=80, goals=3, hScore=2, aScore=1)], shadow_state)

    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert saved[0]["result_over25"] == "WIN"
    assert saved[0]["final_score"] == "2-1"


def test_run_shadow_alerts_no_op_when_nothing_new_or_pending(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    e = _shadow_event(id=1, patterns=[], patternScore=0)  # não é candidato (is_live_pick False)
    added = run_shadow_alerts([e], shadow_state)

    assert added == 0
    assert isolated_shadow_env["commits"] == []


def test_run_shadow_alerts_empty_events_short_circuits(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    assert run_shadow_alerts([], shadow_state) == 0
    assert isolated_shadow_env["commits"] == []


def test_run_shadow_alerts_skips_saved_picks_and_three_plus_goals(isolated_shadow_env):
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    saved_pick = _shadow_event(id=1, isSavedPick=True)
    already_over = _shadow_event(id=2, goals=3)
    added = run_shadow_alerts([saved_pick, already_over], shadow_state)
    assert added == 0


# ── falha de persistência (mesma semântica fail-safe de run_observations) ──


def test_run_shadow_alerts_returns_zero_and_discards_on_persist_failure(isolated_shadow_env, monkeypatch):
    monkeypatch.setattr(
        mod, "git_commit_push",
        lambda files, msg: (isolated_shadow_env["commits"].append((files, msg)), False)[1],
    )
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    added = run_shadow_alerts([_shadow_event(id=1)], shadow_state)

    assert added == 0
    assert "1:send" not in shadow_state["logged_keys"]
    assert shadow_state["unresolved"] == {}
    assert isolated_shadow_env["discards"] == [[str(isolated_shadow_env["shadow_file"])]]


def test_run_shadow_alerts_retries_after_persist_recovers(isolated_shadow_env, monkeypatch):
    monkeypatch.setattr(
        mod, "git_commit_push",
        lambda files, msg: (isolated_shadow_env["commits"].append((files, msg)), False)[1],
    )
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    n1 = run_shadow_alerts([_shadow_event(id=1)], shadow_state)
    assert n1 == 0

    commits = isolated_shadow_env["commits"]
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: (commits.append((files, msg)), True)[1])
    n2 = run_shadow_alerts([_shadow_event(id=1)], shadow_state)
    assert n2 == 1
    assert "1:send" in shadow_state["logged_keys"]


# ── scan_once: nunca envia Telegram, com ou sem shadow_state ─────────────


def test_scan_once_without_shadow_state_has_no_shadow_side_effects(monkeypatch):
    raw_event = {"id": 321}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: _shadow_event(id=321))
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: e.get("patterns", []))

    def boom(*_a, **_k):
        raise AssertionError("run_shadow_alerts não deveria correr sem shadow_state")

    monkeypatch.setattr(mod, "run_shadow_alerts", boom)

    state = {"ht": {}, "mkt": {}}
    n = mod.scan_once("fake_key", state, set(), set(), verbose=False)
    assert n == 0


def test_scan_once_with_shadow_state_runs_shadow_never_sends_telegram(isolated_shadow_env, monkeypatch):
    assert LIVE_ALERTS_ENABLED is False

    raw_event = {"id": 555}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(
        mod, "enrich_event",
        lambda api_key, ev, pick_ids: _shadow_event(id=555, min=40, goals=1),
    )
    monkeypatch.setattr(
        mod, "detect_patterns",
        lambda e, state: [
            {"id": "pressure", "label": "Pressão 95", "emoji": "🔥", "level": "critical", "detail": "d"},
            {"id": "xg_delta", "label": "xG alto", "emoji": "🎲", "level": "critical", "detail": "d"},
        ],
    )

    state = {"ht": {}, "mkt": {}}
    shadow_state = {"logged_keys": set(), "unresolved": {}, "last_valid_odds": {}}
    n = mod.scan_once("fake_key", state, set(), set(), verbose=False, shadow_state=shadow_state)

    assert n == 0  # APOSTAR AGORA continua desligado, sem TG
    saved = json.loads(isolated_shadow_env["shadow_file"].read_text())
    assert len(saved) == 1 and saved[0]["event_id"] == "555"
