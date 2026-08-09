"""
tests/pipeline/test_live_observations.py
-----------------------------------------
Testes do fluxo 👁 OBSERVAÇÕES no backend (pipeline/scan_live.py), porta
server-side de autoLogObservations() (index.html) — a peça que faltava para o
Live Scanner funcionar sem qualquer browser aberto.

Cobre: gate de qualificação, construção da observação, dedup persistente
(sobrevive a restart), actualização de resultado (WIN/LOSS), o ciclo completo
run_observations() (guardar + Telegram), scan_once() sem efeitos secundários
quando obs_state=None (compatibilidade com os testes pré-existentes), e o
mecanismo de health check.

Todos os eventos/ficheiros aqui são sintéticos (# synthetic) — nunca dados de
produção. Ficheiros reais (data/observations.json, data/picks*.json,
data/live_scanner_health.json) e git_commit_push são sempre monkeypatched:
nenhum destes testes toca no disco do repositório nem invoca git.
"""

from __future__ import annotations

import json

import pytest

import pipeline.scan_live as mod
from pipeline.scan_live import (
    LIVE_ALERTS_ENABLED,
    build_observation,
    build_observations_message,
    compute_observation_result_updates,
    load_observation_state,
    maybe_commit_health,
    new_health_state,
    passes_observation_gate,
    run_observations,
    update_health,
    write_health,
)


def _obs_event(**over):  # synthetic
    e = {
        "id": 1, "home": "Casa", "away": "Fora", "hScore": 1, "aScore": 0,
        "goals": 1, "min": 40, "period": "2nd_half", "league": "Serie A",
        "xgTotal": 2.0, "overOdds": 1.9, "probLive": 55,
        "patterns": [{"id": "pressure", "label": "Pressão 60", "emoji": "🔥", "level": "high"}],
        "patternScore": 8, "isSavedPick": False,
    }
    e.update(over)
    return e


# ── passes_observation_gate ──────────────────────────────────────────────


def test_gate_rejects_non_whitelisted_league():
    e = _obs_event(league="MLS")  # não está nas 10 ligas de produção
    assert not passes_observation_gate(e)


def test_gate_rejects_below_score_threshold():
    e = _obs_event(patternScore=5)  # THRESHOLD=6
    assert not passes_observation_gate(e)


def test_gate_accepts_at_score_threshold():
    e = _obs_event(patternScore=6)
    assert passes_observation_gate(e)


def test_gate_rejects_low_implied_probability():
    e = _obs_event(probLive=24)  # <25% — mercado já descartou
    assert not passes_observation_gate(e)


def test_gate_accepts_when_prob_live_missing():
    """probLive ausente (None) é fail-open — não bloqueia (mirror do JS: só
    bloqueia se `e.probLive!=null && e.probLive<25`)."""
    e = _obs_event(probLive=None)
    assert passes_observation_gate(e)


def test_gate_rejects_late_minute_without_enough_goals():
    e = _obs_event(min=80, goals=1)  # >=75' com <2 golos — improvável
    assert not passes_observation_gate(e)


def test_gate_accepts_late_minute_with_enough_goals():
    e = _obs_event(min=80, goals=2)
    assert passes_observation_gate(e)


# ── build_observation ────────────────────────────────────────────────────


def test_build_observation_fields_match_schema():
    e = _obs_event(id=999)
    obs = build_observation(e, pick_index={}, sharp_ids=set())
    assert obs["event_id"] == "999"
    assert obs["id"].startswith("999_obs_")
    assert obs["casa"] == "Casa" and obs["fora"] == "Fora"
    assert obs["liga"] == "Serie A"
    assert obs["score"] == "1-0"
    assert obs["goals"] == 1
    assert obs["xg"] == "2.00"
    assert obs["odds_live"] == "1.90"
    assert obs["prob_live"] == 55
    assert obs["pattern_score"] == 8
    assert obs["patterns"] == ["pressure"]
    assert obs["pattern_labels"] == ["🔥 Pressão 60"]
    assert obs["result_over25"] == "" and obs["final_score"] == "" and obs["result_at_min"] == ""


def test_build_observation_cross_references_scan_pick_and_sharp():
    e = _obs_event(id=42)
    pick_index = {"42": {"score_sistema": "70"}}
    obs = build_observation(e, pick_index=pick_index, sharp_ids={"42"})
    assert obs["has_scan_pick"] == "1"
    assert obs["scan_score"] == "70"
    assert obs["has_sharp"] == "1"


def test_build_observation_blank_cross_reference_when_absent():
    e = _obs_event(id=43)
    obs = build_observation(e, pick_index={}, sharp_ids=set())
    assert obs["has_scan_pick"] == "" and obs["scan_score"] == "" and obs["has_sharp"] == ""


def test_build_observation_handles_missing_xg_and_odds():
    e = _obs_event(xgTotal=None, overOdds=None)
    obs = build_observation(e, pick_index={}, sharp_ids=set())
    assert obs["xg"] == "" and obs["odds_live"] == ""


# ── compute_observation_result_updates ───────────────────────────────────


def test_updates_marks_win_at_three_goals():
    unresolved = {"1": "1_obs_100"}
    events = [_obs_event(id=1, goals=3, hScore=2, aScore=1, min=70)]
    updates = compute_observation_result_updates(events, unresolved)
    assert updates == {"1_obs_100": {"result_over25": "WIN", "final_score": "2-1", "result_at_min": 70}}


def test_updates_marks_loss_at_full_time_without_three_goals():
    unresolved = {"1": "1_obs_100"}
    events = [_obs_event(id=1, goals=2, hScore=1, aScore=1, min=90, period="2nd_half")]
    updates = compute_observation_result_updates(events, unresolved)
    assert updates == {"1_obs_100": {"result_over25": "LOSS", "final_score": "1-1", "result_at_min": 90}}


def test_updates_marks_loss_on_ft_period_token():
    unresolved = {"1": "1_obs_100"}
    events = [_obs_event(id=1, goals=1, hScore=1, aScore=0, min=88, period="FT")]
    updates = compute_observation_result_updates(events, unresolved)
    assert updates["1_obs_100"]["result_over25"] == "LOSS"


def test_updates_ignores_still_running_games_below_three_goals():
    unresolved = {"1": "1_obs_100"}
    events = [_obs_event(id=1, goals=1, min=60, period="2nd_half")]
    assert compute_observation_result_updates(events, unresolved) == {}


def test_updates_ignores_events_without_pending_observation():
    """Jogo sem observação pendente (não está em `unresolved`) nunca gera update."""
    events = [_obs_event(id=2, goals=3, min=70)]
    assert compute_observation_result_updates(events, {}) == {}


# ── load_observation_state — dedup persistente (sobrevive a restart) ─────


def test_load_observation_state_builds_seen_and_unresolved_sets(monkeypatch, tmp_path):
    obs_file = tmp_path / "observations.json"
    obs_file.write_text(json.dumps([
        {"id": "1_obs_1", "event_id": "1", "result_over25": ""},
        {"id": "2_obs_2", "event_id": "2", "result_over25": "WIN"},
    ]), encoding="utf-8")
    monkeypatch.setattr(mod, "_OBS_PATH", obs_file)

    state = load_observation_state()
    assert state["seen_event_ids"] == {"1", "2"}
    assert state["unresolved"] == {"1": "1_obs_1"}  # "2" já resolvido — fora


def test_load_observation_state_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_OBS_PATH", tmp_path / "does_not_exist.json")
    state = load_observation_state()
    assert state == {"seen_event_ids": set(), "unresolved": {}}


# ── run_observations — ciclo completo: gate -> dedup -> guardar -> Telegram ──


@pytest.fixture
def isolated_obs_env(tmp_path, monkeypatch):
    """Isola os 3 ficheiros usados por run_observations() e neutraliza git —
    nenhum destes testes escreve no repositório real nem invoca git."""
    obs_file = tmp_path / "observations.json"
    picks_file = tmp_path / "picks.json"
    picks1x2_file = tmp_path / "picks_1x2.json"
    monkeypatch.setattr(mod, "_OBS_PATH", obs_file)
    monkeypatch.setattr(mod, "_PICKS_PATH", picks_file)
    monkeypatch.setattr(mod, "_PICKS_1X2_PATH", picks1x2_file)
    commits = []
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: commits.append((files, msg)))
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])
    return {"obs_file": obs_file, "commits": commits, "sent": sent}


def test_run_observations_saves_new_qualifying_game(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1)]

    added = run_observations(events, obs_state)

    assert added == 1
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert len(saved) == 1 and saved[0]["event_id"] == "1"
    assert "1" in obs_state["seen_event_ids"]
    assert obs_state["unresolved"]["1"] == saved[0]["id"]
    assert isolated_obs_env["commits"], "esperava um git_commit_push"
    assert len(isolated_obs_env["sent"]) == 1
    assert "👁 OBSERVAÇÕES: 1 jogos guardados" in isolated_obs_env["sent"][0]
    assert "Casa vs Fora" in isolated_obs_env["sent"][0]


def test_run_observations_rejects_game_below_gate(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1, patternScore=2)]  # abaixo do THRESHOLD=6

    added = run_observations(events, obs_state)

    assert added == 0
    assert not isolated_obs_env["obs_file"].exists()
    assert isolated_obs_env["commits"] == []
    assert isolated_obs_env["sent"] == []


def test_run_observations_never_saves_same_event_twice(isolated_obs_env):
    """Anti-duplicação (requisito 6): o mesmo jogo não pode voltar a gerar uma
    observação/alerta em ciclos de polling subsequentes."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1)]

    n1 = run_observations(events, obs_state)
    n2 = run_observations(events, obs_state)  # 2º ciclo de polling, mesmo jogo

    assert n1 == 1
    assert n2 == 0  # não duplica
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert len(saved) == 1
    assert len(isolated_obs_env["sent"]) == 1  # só 1 mensagem TG no total


def test_run_observations_survives_restart_via_persisted_dedup(isolated_obs_env):
    """Dedup não pode depender de memória de processo (o worker reinicia a
    cada ~4h) — tem de sobreviver lendo o ficheiro persistido."""
    obs_state_1 = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1)], obs_state_1)

    # "restart": novo processo, novo obs_state reconstruído do ficheiro no disco
    obs_state_2 = load_observation_state()
    n2 = run_observations([_obs_event(id=1)], obs_state_2)

    assert n2 == 0
    assert len(isolated_obs_env["sent"]) == 1


def test_run_observations_different_games_both_saved_and_batched_in_one_message(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1, home="A", away="B"), _obs_event(id=2, home="C", away="D")]

    added = run_observations(events, obs_state)

    assert added == 2
    assert len(isolated_obs_env["sent"]) == 1  # 1 mensagem TG para os 2 jogos
    msg = isolated_obs_env["sent"][0]
    assert "A vs B" in msg and "C vs D" in msg
    assert "👁 OBSERVAÇÕES: 2 jogos guardados" in msg


def test_run_observations_updates_result_without_new_telegram_message(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1, goals=1, min=40)], obs_state)
    assert len(isolated_obs_env["sent"]) == 1

    # jogo termina sem chegar a 3 golos -> update para LOSS, sem novo alerta TG
    added = run_observations([_obs_event(id=1, goals=1, hScore=1, aScore=0, min=90)], obs_state)

    assert added == 0
    assert len(isolated_obs_env["sent"]) == 1  # nenhuma mensagem nova
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert saved[0]["result_over25"] == "LOSS"
    assert saved[0]["final_score"] == "1-0"
    assert "1" not in obs_state["unresolved"]  # resolvido — sai da lista de pendentes


def test_run_observations_no_op_when_nothing_new_or_changed(isolated_obs_env):
    """Sem observações novas nem updates: não escreve ficheiro, não chama git,
    não envia Telegram — evita I/O e commits desnecessários a cada ciclo de 60s."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1, patternScore=0)]  # não qualifica, sem pendentes

    added = run_observations(events, obs_state)

    assert added == 0
    assert isolated_obs_env["commits"] == []


def test_run_observations_empty_events_short_circuits(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    assert run_observations([], obs_state) == 0
    assert isolated_obs_env["commits"] == []


# ── build_observations_message ───────────────────────────────────────────


def test_build_observations_message_format():
    events = [_obs_event(id=1, home="Benfica", away="Porto")]
    entries = [build_observation(events[0], {}, set())]
    msg = build_observations_message(events, entries)
    assert msg.startswith("👁 OBSERVAÇÕES: 1 jogos guardados\n")
    assert "Benfica vs Porto [score:8]" in msg
    assert "🔥Pressão 60" in msg


# ── scan_once: compatibilidade retroactiva (obs_state=None) ──────────────


def test_scan_once_without_obs_state_has_no_observation_side_effects(monkeypatch):
    """Os testes pré-existentes chamam scan_once() sem obs_state — tem de
    continuar 100% inócuo (sem leitura/escrita de ficheiros nem git) quando
    esse parâmetro não é passado, para não quebrar a suite anterior."""
    raw_event = {"id": 321}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: {
        "id": 321, "home": "Casa", "away": "Fora", "hScore": 0, "aScore": 0, "goals": 0,
        "min": 40, "period": "1st_half", "league": "Serie A", "xgTotal": 2.0,
        "overOdds": 1.9, "probLive": 55, "isSavedPick": False,
    })
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: [
        {"id": "pressure", "label": "Pressão 60", "emoji": "🔥", "level": "high", "detail": "d"},
    ])

    def boom(*_a, **_k):
        raise AssertionError("run_observations não deveria correr sem obs_state")

    monkeypatch.setattr(mod, "run_observations", boom)

    state = {"ht": {}, "mkt": {}}
    n = mod.scan_once("fake_key", state, set(), set(), verbose=False)  # sem obs_state
    assert n == 0  # LIVE_ALERTS_ENABLED=False, comportamento inalterado


def test_scan_once_with_obs_state_runs_observations_independent_of_apostar_agora(isolated_obs_env, monkeypatch):
    """Com obs_state fornecido, scan_once já corre o fluxo OBSERVAÇÕES sobre os
    mesmos eventos enriquecidos — e continua a fazê-lo mesmo que o jogo não
    qualifique (ou esteja bloqueado) para o alerta '🔥 APOSTAR AGORA', que
    permanece desactivado (LIVE_ALERTS_ENABLED)."""
    assert LIVE_ALERTS_ENABLED is False  # não reactivado por este trabalho

    raw_event = {"id": 555}
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [raw_event])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: {
        "id": 555, "home": "Casa", "away": "Fora", "hScore": 1, "aScore": 0, "goals": 1,
        "min": 40, "period": "2nd_half", "league": "Serie A", "xgTotal": 2.0,
        "overOdds": 1.9, "probLive": 55, "isSavedPick": False,
    })
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: [
        {"id": "pressure", "label": "Pressão 60", "emoji": "🔥", "level": "high", "detail": "d"},
        {"id": "mom", "label": "Casa pressiona", "emoji": "💪", "level": "med", "detail": "d"},
    ])

    state = {"ht": {}, "mkt": {}}
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    health_stats: dict = {}
    n = mod.scan_once("fake_key", state, set(), set(), verbose=False,
                       obs_state=obs_state, health_stats=health_stats)

    assert n == 0  # APOSTAR AGORA continua desligado
    assert health_stats["observations_added"] == 1  # mas a observação foi guardada
    assert len(isolated_obs_env["sent"]) == 1
    assert health_stats["live_games"] == 1


# ── resiliência: uma falha num jogo não pode parar o scanner inteiro ─────


def test_scan_once_one_event_failure_does_not_block_others(monkeypatch, isolated_obs_env):
    """Requisito 5: falha de enrich num jogo é ignorada (log + continue); os
    restantes jogos continuam a ser processados normalmente, incluindo
    observações."""
    events_raw = [{"id": "bad"}, {"id": 777}]
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: events_raw)

    def flaky_enrich(api_key, ev, pick_ids):
        if ev["id"] == "bad":
            raise RuntimeError("BSD indisponível para este evento")
        return {
            "id": 777, "home": "Casa", "away": "Fora", "hScore": 1, "aScore": 0, "goals": 1,
            "min": 40, "period": "2nd_half", "league": "Serie A", "xgTotal": 2.0,
            "overOdds": 1.9, "probLive": 55, "isSavedPick": False,
        }

    monkeypatch.setattr(mod, "enrich_event", flaky_enrich)
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: [
        {"id": "pressure", "label": "Pressão 60", "emoji": "🔥", "level": "high", "detail": "d"},
        {"id": "mom", "label": "Casa pressiona", "emoji": "💪", "level": "med", "detail": "d"},
    ])

    state = {"ht": {}, "mkt": {}}
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    health_stats: dict = {}
    n = mod.scan_once("fake_key", state, set(), set(), verbose=False,
                       obs_state=obs_state, health_stats=health_stats)

    assert n == 0
    assert health_stats["observations_added"] == 1  # o jogo "777" foi processado apesar do "bad"


# ── health check ──────────────────────────────────────────────────────────


def test_new_health_state_defaults():
    h = new_health_state()
    assert "running" not in h  # "running" só existe no snapshot (write_health), não nos contadores
    assert h["live_games_found"] == 0
    assert h["observations_generated_total"] == 0
    assert h["errors"] == []


def test_update_health_tracks_scan_and_api_status():
    h = new_health_state()
    update_health(h, live_games=3, observations_added=0, api_ok=True)
    assert h["live_games_found"] == 3
    assert h["last_scan_at"] is not None
    assert h["last_successful_api_request_at"] is not None


def test_update_health_accumulates_observations_and_records_telegram_time():
    h = new_health_state()
    update_health(h, live_games=1, observations_added=2, api_ok=True)
    update_health(h, live_games=1, observations_added=3, api_ok=True)
    assert h["observations_generated_total"] == 5
    assert h["last_telegram_sent_at"] is not None


def test_update_health_records_errors_capped_at_five(monkeypatch):
    h = new_health_state()
    for i in range(7):
        update_health(h, live_games=0, observations_added=0, api_ok=False, error=f"erro {i}")
    assert len(h["errors"]) == 5
    assert h["errors"][-1]["msg"] == "erro 6"  # mantém só os mais recentes


def test_update_health_api_not_ok_does_not_advance_last_success(monkeypatch):
    h = new_health_state()
    update_health(h, live_games=1, observations_added=0, api_ok=True)
    first_success = h["last_successful_api_request_at"]
    update_health(h, live_games=0, observations_added=0, api_ok=False, error="BSD down")
    assert h["last_successful_api_request_at"] == first_success  # não avança em falha


def test_write_health_persists_running_flag(tmp_path, monkeypatch):
    health_path = tmp_path / "live_scanner_health.json"
    monkeypatch.setattr(mod, "_HEALTH_PATH", health_path)
    h = new_health_state()
    update_health(h, live_games=5, observations_added=1, api_ok=True)

    write_health(h, running=True)

    snapshot = json.loads(health_path.read_text())
    assert snapshot["running"] is True
    assert snapshot["live_games_found"] == 5
    assert "_last_committed_at" not in snapshot  # campo interno não vai para o ficheiro


def test_maybe_commit_health_throttles_repeated_commits(tmp_path, monkeypatch):
    """Health não deve gerar um commit por ciclo de 60s durante 5h50 —
    só comita de novo após HEALTH_COMMIT_INTERVAL_S, salvo force=True."""
    monkeypatch.setattr(mod, "_HEALTH_PATH", tmp_path / "health.json")
    commits = []
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: commits.append(msg))
    h = new_health_state()

    maybe_commit_health(h, running=True, force=True)  # 1º commit sempre passa (force)
    maybe_commit_health(h, running=True)  # imediatamente a seguir — throttled
    assert len(commits) == 1

    maybe_commit_health(h, running=True, force=True)  # force ignora o throttle
    assert len(commits) == 2
