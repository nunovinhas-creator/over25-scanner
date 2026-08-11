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
from pathlib import Path

import pytest

import pipeline.scan_live as mod
from pipeline.scan_live import (
    COVERAGE_FIELDS,
    LIVE_ALERTS_ENABLED,
    build_coverage_entry,
    build_observation,
    build_observations_message,
    compute_coverage,
    compute_observation_result_updates,
    load_observation_state,
    maybe_commit_health,
    new_health_state,
    passes_coverage_log_gate,
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


# ── Bloco L1 — compute_coverage() ─────────────────────────────────────────


def _enriched_event(**over):  # synthetic — forma que sai de enrich_event()
    e = {
        "xgTotal": 2.0, "lastMom": 10,
        "da": {"h": 5, "a": 3}, "sot": {"h": 2, "a": 1},
        "corners": {"h": 4, "a": 2}, "possession": {"h": 60, "a": 40},
    }
    e.update(over)
    return e


def test_compute_coverage_full_when_all_fields_present():
    cov = compute_coverage(_enriched_event())
    assert cov["score"] == len(COVERAGE_FIELDS)
    assert cov["total"] == len(COVERAGE_FIELDS)
    assert all(cov["fields"].values())


def test_compute_coverage_zero_when_fields_missing():
    """xG em falta (ex.: liga sul-americana) não é um valor por omissão —
    fica explicitamente ausente na cobertura, nunca conta como presente."""
    e = _enriched_event(xgTotal=None, lastMom=None, da={}, sot={}, corners={}, possession={})
    cov = compute_coverage(e)
    assert cov["score"] == 0
    assert cov["total"] == len(COVERAGE_FIELDS)
    assert not any(cov["fields"].values())


def test_compute_coverage_requires_both_sides_for_paired_fields():
    """Um valor parcial (só casa) não conta como campo presente — mirror do
    que detect_patterns() precisa (soma h+a, ver `_t()`)."""
    e = _enriched_event(da={"h": 5, "a": None})
    cov = compute_coverage(e)
    assert cov["fields"]["da"] is False


def test_compute_coverage_partial_score():
    e = _enriched_event(xgTotal=None, lastMom=None)  # só os 4 campos pareados presentes
    cov = compute_coverage(e)
    assert cov["score"] == 4
    assert cov["fields"]["xgTotal"] is False and cov["fields"]["lastMom"] is False


# ── Bloco L1 — cobertura embutida em build_observation() ─────────────────


def test_build_observation_marks_kind_observation():
    obs = build_observation(_obs_event(id=1), pick_index={}, sharp_ids=set())
    assert obs["kind"] == "observation"


def test_build_observation_propagates_precomputed_coverage():
    """build_observation() nunca recalcula cobertura — só propaga o que
    enrich_event() já anexou em e['coverage'] (ver compute_coverage)."""
    e = _obs_event(id=1, coverage=compute_coverage(_enriched_event()))
    obs = build_observation(e, pick_index={}, sharp_ids=set())
    assert obs["coverage_score"] == len(COVERAGE_FIELDS)
    assert obs["coverage_total"] == len(COVERAGE_FIELDS)
    assert all(obs["coverage_fields"].values())


def test_build_observation_defaults_coverage_when_absent():
    """Evento sem e['coverage'] (nunca deveria acontecer em produção — só
    aqui, num evento sintético incompleto) não rebenta: cai num default
    explícito de zero, nunca finge cobertura."""
    e = _obs_event(id=1)  # sem chave "coverage"
    obs = build_observation(e, pick_index={}, sharp_ids=set())
    assert obs["coverage_score"] == 0
    assert obs["coverage_total"] == len(COVERAGE_FIELDS)
    assert obs["coverage_fields"] == {}


# ── Bloco L1 — passes_coverage_log_gate / build_coverage_entry ───────────


def test_coverage_gate_rejects_whitelisted_league():
    """Dentro da whitelist a cobertura já vem embutida na observação real —
    não gera um registo de cobertura em separado."""
    e = _obs_event(league="Serie A", min=40)
    assert not passes_coverage_log_gate(e)


def test_coverage_gate_accepts_non_whitelisted_league():
    e = _obs_event(league="MLS", min=40)
    assert passes_coverage_log_gate(e)


def test_coverage_gate_rejects_before_minute_eight():
    """Mesmo corte de detect_patterns() — antes do min 8 a BSD tipicamente
    ainda não populou stats."""
    e = _obs_event(league="MLS", min=5)
    assert not passes_coverage_log_gate(e)


def test_coverage_gate_ignores_score_and_prob_thresholds():
    """Ao contrário de passes_observation_gate(), a cobertura não filtra por
    patternScore/probLive — filtrar pelos mesmos critérios que se pretende
    avaliar enviesaria a amostra."""
    e = _obs_event(league="MLS", min=40, patternScore=0, probLive=1)
    assert passes_coverage_log_gate(e)


def test_build_coverage_entry_schema():
    e = _obs_event(id=999, league="MLS", min=52,
                    xgTotal=None, lastMom=None, da={}, sot={}, corners={}, possession={})
    entry = build_coverage_entry(e)
    assert entry["kind"] == "coverage_only"
    assert entry["event_id"] == "999"
    assert entry["id"].startswith("999_cov_")
    assert entry["liga"] == "MLS"
    assert entry["min"] == 52
    assert entry["coverage_score"] == 0
    assert entry["coverage_total"] == len(COVERAGE_FIELDS)
    assert "patterns" not in entry and "result_over25" not in entry


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
    assert state["coverage_seen_event_ids"] == set()


def test_load_observation_state_separates_coverage_only_entries(monkeypatch, tmp_path):
    """Entradas kind='coverage_only' (Bloco L1) têm dedup próprio — nunca
    entram em seen_event_ids/unresolved nem bloqueiam uma 👁 real futura
    para o mesmo event_id."""
    obs_file = tmp_path / "observations.json"
    obs_file.write_text(json.dumps([
        {"id": "1_obs_1", "event_id": "1", "result_over25": ""},
        {"id": "9_cov_1", "event_id": "9", "kind": "coverage_only"},
    ]), encoding="utf-8")
    monkeypatch.setattr(mod, "_OBS_PATH", obs_file)

    state = load_observation_state()
    assert state["seen_event_ids"] == {"1"}
    assert state["coverage_seen_event_ids"] == {"9"}
    assert state["unresolved"] == {"1": "1_obs_1"}


def test_load_observation_state_legacy_entries_without_kind_count_as_real(monkeypatch, tmp_path):
    """Entradas anteriores ao Bloco L1 (sem campo 'kind') continuam a contar
    como observação real — nunca reinterpretadas como cobertura."""
    obs_file = tmp_path / "observations.json"
    obs_file.write_text(json.dumps([
        {"id": "1_obs_legacy", "event_id": "1", "liga": "MLS", "result_over25": ""},
    ]), encoding="utf-8")
    monkeypatch.setattr(mod, "_OBS_PATH", obs_file)

    state = load_observation_state()
    assert state["seen_event_ids"] == {"1"}
    assert state["coverage_seen_event_ids"] == set()


def test_load_observation_state_empty_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_OBS_PATH", tmp_path / "does_not_exist.json")
    state = load_observation_state()
    assert state == {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}


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
    # git_commit_push devolve bool desde a correcção do bug de permissions
    # (live_scanner.yml sem `contents: write` — push falhava 403 em produção).
    # Aqui simula sempre sucesso; ver isolated_obs_env_persist_fails para o
    # caminho de falha.
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: (commits.append((files, msg)), True)[1])
    discards = []
    monkeypatch.setattr(mod, "git_discard_local_changes", lambda files: discards.append(files))
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])
    return {"obs_file": obs_file, "commits": commits, "sent": sent, "discards": discards}


@pytest.fixture
def isolated_obs_env_persist_fails(isolated_obs_env, monkeypatch):
    """Variante de isolated_obs_env em que git_commit_push falha sempre
    (simula o 403 real de GITHUB_TOKEN sem `contents: write`) — para testar
    que run_observations() nunca produz um falso sucesso."""
    monkeypatch.setattr(
        mod, "git_commit_push",
        lambda files, msg: (isolated_obs_env["commits"].append((files, msg)), False)[1],
    )
    return isolated_obs_env


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


# ── run_observations — Bloco L1: registo de cobertura fora da whitelist ──


def test_run_observations_logs_coverage_for_non_whitelisted_league(isolated_obs_env):
    """Um jogo fora da whitelist (ex.: MLS) não qualifica para 👁 real, mas
    agora gera um registo de cobertura mínimo no mesmo ficheiro — sem
    Telegram, sem contar para o valor devolvido (que continua a ser só as
    👁 reais)."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    e = _obs_event(id=1, league="MLS", min=45)

    added = run_observations([e], obs_state)

    assert added == 0  # nenhuma 👁 real — só cobertura
    assert isolated_obs_env["sent"] == []  # nunca Telegram para cobertura
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert len(saved) == 1
    assert saved[0]["kind"] == "coverage_only"
    assert saved[0]["event_id"] == "1"
    assert saved[0]["liga"] == "MLS"
    assert "1" in obs_state["coverage_seen_event_ids"]
    assert "1" not in obs_state["seen_event_ids"]
    assert isolated_obs_env["commits"], "esperava um git_commit_push mesmo só com cobertura"


def test_run_observations_coverage_and_real_observation_batched_together(isolated_obs_env):
    """Um jogo dentro da whitelist (👁 real) e outro fora (cobertura) no
    mesmo ciclo persistem no mesmo commit, sem interferirem um no outro."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    events = [_obs_event(id=1, league="Serie A"), _obs_event(id=2, league="MLS", min=45)]

    added = run_observations(events, obs_state)

    assert added == 1  # só a 👁 real conta
    assert len(isolated_obs_env["sent"]) == 1  # 1 mensagem TG, só para a real
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    kinds = {o["event_id"]: o["kind"] for o in saved}
    assert kinds == {"1": "observation", "2": "coverage_only"}
    assert len(isolated_obs_env["commits"]) == 1  # um único commit para os dois


def test_run_observations_coverage_never_duplicates_same_event(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    e = _obs_event(id=1, league="MLS", min=45)

    run_observations([e], obs_state)
    run_observations([e], obs_state)  # 2º ciclo de polling, mesmo jogo

    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert len(saved) == 1


def test_run_observations_coverage_gate_still_needs_minute_eight(isolated_obs_env):
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    e = _obs_event(id=1, league="MLS", min=3)

    added = run_observations([e], obs_state)

    assert added == 0
    assert not isolated_obs_env["obs_file"].exists()
    assert isolated_obs_env["commits"] == []


def test_run_observations_never_double_logs_coverage_for_already_real_event(isolated_obs_env):
    """Um evento cuja liga é whitelisted mas que falhou a 👁 real por outro
    motivo (ex.: score baixo) fica fora da cobertura — dentro da whitelist a
    cobertura já vem embutida em cada 👁 real que vier a qualificar."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    e = _obs_event(id=1, league="Serie A", patternScore=0, min=45)  # abaixo do THRESHOLD=6

    added = run_observations([e], obs_state)

    assert added == 0
    assert not isolated_obs_env["obs_file"].exists()
    assert isolated_obs_env["commits"] == []


def test_run_observations_coverage_backward_compatible_with_obs_state_missing_key(isolated_obs_env):
    """obs_state construído sem 'coverage_seen_event_ids' (chamadores/testes
    antigos) não rebenta — a chave é inicializada em memória na primeira
    chamada (setdefault)."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}  # sem a chave nova
    e = _obs_event(id=1, league="MLS", min=45)

    added = run_observations([e], obs_state)

    assert added == 0
    assert "coverage_seen_event_ids" in obs_state
    assert "1" in obs_state["coverage_seen_event_ids"]


def test_run_observations_coverage_survives_restart_via_persisted_dedup(isolated_obs_env):
    obs_state_1 = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    run_observations([_obs_event(id=1, league="MLS", min=45)], obs_state_1)

    obs_state_2 = load_observation_state()  # "restart"
    n2 = run_observations([_obs_event(id=1, league="MLS", min=45)], obs_state_2)

    assert n2 == 0
    saved = json.loads(isolated_obs_env["obs_file"].read_text())
    assert len(saved) == 1  # não duplicou o registo de cobertura após o restart


def test_run_observations_coverage_persist_failure_never_advances_dedup(isolated_obs_env_persist_fails):
    obs_state = {"seen_event_ids": set(), "unresolved": {}, "coverage_seen_event_ids": set()}
    e = _obs_event(id=1, league="MLS", min=45)

    added = run_observations([e], obs_state)

    assert added == 0
    assert "1" not in obs_state["coverage_seen_event_ids"]
    assert isolated_obs_env_persist_fails["sent"] == []


# ── run_observations — falha de persistência (bug real: live_scanner.yml ──
# sem `permissions: contents: write` → git push 403 → run #150, 2026-08-09).
# Uma falha de git_commit_push() nunca pode produzir um falso sucesso: sem
# Telegram, sem avanço de dedup, sem ficar "queimado" para sempre.


def test_run_observations_returns_zero_when_persist_fails(isolated_obs_env_persist_fails):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1)]

    added = run_observations(events, obs_state)

    assert added == 0


def test_run_observations_does_not_send_telegram_when_persist_fails(isolated_obs_env_persist_fails):
    """O requisito central: nunca 'Telegram enviado, persistência falhou'."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1)], obs_state)

    assert isolated_obs_env_persist_fails["sent"] == []


def test_run_observations_does_not_advance_dedup_when_persist_fails(isolated_obs_env_persist_fails):
    """Sem persistência confirmada, o evento tem de continuar elegível no
    próximo ciclo — não pode ficar marcado como já visto."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1)], obs_state)

    assert "1" not in obs_state["seen_event_ids"]
    assert obs_state["unresolved"] == {}


def test_run_observations_retries_successfully_after_persist_recovers(isolated_obs_env_persist_fails, monkeypatch):
    """Depois de uma falha, o mesmo evento tem de ser retomado com sucesso
    assim que git_commit_push voltar a funcionar (ex.: permissions corrigidas) —
    sem ficar 'queimado' pelo ciclo anterior falhado."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    events = [_obs_event(id=1)]

    n1 = run_observations(events, obs_state)
    assert n1 == 0
    assert isolated_obs_env_persist_fails["sent"] == []

    # "permissions corrigidas": git_commit_push volta a ter sucesso
    commits = isolated_obs_env_persist_fails["commits"]
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: (commits.append((files, msg)), True)[1])

    n2 = run_observations(events, obs_state)
    assert n2 == 1
    assert "1" in obs_state["seen_event_ids"]
    assert len(isolated_obs_env_persist_fails["sent"]) == 1


def test_run_observations_discards_local_file_when_persist_fails(isolated_obs_env_persist_fails):
    """Reverte o ficheiro local para o estado de origin/main — evita que o
    próximo ciclo releia do disco um conteúdo nunca pushado e acumule
    entradas duplicadas a cada retry (ver git_discard_local_changes)."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1)], obs_state)

    assert isolated_obs_env_persist_fails["discards"] == [
        [str(isolated_obs_env_persist_fails["obs_file"])]
    ]


def test_run_observations_persist_failure_logs_clear_error(isolated_obs_env_persist_fails, capsys):
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    run_observations([_obs_event(id=1)], obs_state)

    err = capsys.readouterr().err
    assert "obs_persist_failed" in err
    assert "novos=1" in err


def test_run_observations_result_update_not_lost_when_persist_fails(isolated_obs_env_persist_fails, monkeypatch):
    """Um update de resultado (WIN/LOSS) pendente também não pode ser dado
    como resolvido se a persistência falhar — tem de ser recalculado no
    próximo ciclo a partir do mesmo `unresolved`."""
    obs_state = {"seen_event_ids": set(), "unresolved": {}}
    # 1º ciclo com sucesso: observação guardada e pendente
    monkeypatch.setattr(
        mod, "git_commit_push",
        lambda files, msg: (isolated_obs_env_persist_fails["commits"].append((files, msg)), True)[1],
    )
    run_observations([_obs_event(id=1, goals=1, min=40)], obs_state)
    assert obs_state["unresolved"] == {"1": obs_state["unresolved"]["1"]}

    # 2º ciclo: jogo termina (LOSS), mas a persistência falha
    monkeypatch.setattr(
        mod, "git_commit_push",
        lambda files, msg: (isolated_obs_env_persist_fails["commits"].append((files, msg)), False)[1],
    )
    added = run_observations([_obs_event(id=1, goals=1, hScore=1, aScore=0, min=90)], obs_state)

    assert added == 0
    assert "1" in obs_state["unresolved"]  # continua pendente, não foi dado como resolvido


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


# ── infra: live_scanner.yml precisa de permissions: contents: write ──────
#
# Causa raiz do bug real (run #150, workflow_dispatch, 2026-08-09): sem esta
# permissão o GITHUB_TOKEN do job só tem "Contents: read" e qualquer
# git_commit_push() falha com 403, 4/4 vezes seguidas nos logs reais. Ver
# .github/workflows/scanner.yml para o mesmo padrão já usado nos outros
# workflows que fazem auto-commit.


def test_live_scanner_workflow_has_contents_write_permission():
    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "live_scanner.yml"
    )
    content = workflow_path.read_text(encoding="utf-8")

    job_marker = "\n  live:\n"
    assert job_marker in content, "job 'live:' não encontrado em live_scanner.yml"
    job_section = content[content.index(job_marker):]

    steps_marker = "\n    steps:"
    assert steps_marker in job_section
    steps_idx = job_section.index(steps_marker)
    header = job_section[:steps_idx]  # só o cabeçalho do job, antes dos steps

    assert "permissions:" in header
    assert "contents: write" in header
