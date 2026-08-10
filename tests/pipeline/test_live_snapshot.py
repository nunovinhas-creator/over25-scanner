"""
tests/pipeline/test_live_snapshot.py
-------------------------------------
Testes para o snapshot server-side dos jogos ao vivo (Bloco J, 10 ago 2026):
data/live_snapshot.json, publicado por pipeline/scan_live.py para que
loadLive() (index.html) deixe de chamar a BSD API directamente do browser —
a BSD não envia Access-Control-Allow-Origin para a origem do GitHub Pages,
por isso essas chamadas nunca tinham sucesso ali (mesmo diagnóstico do
PR #149 para os scanners pré-jogo).

Cobre: snapshot_status() (OK/NO_LIVE_GAMES/API_ERROR), write_live_snapshot()
(conteúdo do ficheiro), maybe_commit_live_snapshot() (throttle de commit,
mesmo padrão de maybe_commit_health()) e scan_once() a expor
health_stats["enriched_events"] para quem publica o snapshot.

Todos os eventos aqui são sintéticos (# synthetic). git_commit_push e os
paths de ficheiro são sempre monkeypatched — nenhum destes testes toca no
disco do repositório nem invoca git.
"""

from __future__ import annotations

import json

import pipeline.scan_live as mod
from pipeline.scan_live import (
    maybe_commit_live_snapshot,
    snapshot_status,
    write_live_snapshot,
)


def _fake_enriched_event(ev_id: int, goals: int = 1) -> dict:  # synthetic
    return {
        "id": ev_id, "home": "Casa", "away": "Fora", "hScore": goals, "aScore": 0,
        "goals": goals, "min": 60, "status": "2nd_half", "league": "Serie A",
        "overOdds": 1.7, "xgTotal": 3.0, "probLive": 55, "isSavedPick": False,
        "patterns": [], "patternScore": 0,
    }


# ── snapshot_status ───────────────────────────────────────────────────────


def test_snapshot_status_ok_when_events_present():
    assert snapshot_status([_fake_enriched_event(1)], api_ok=False) == "OK"


def test_snapshot_status_no_live_games_when_empty_and_api_ok():
    assert snapshot_status([], api_ok=True) == "NO_LIVE_GAMES"


def test_snapshot_status_api_error_when_empty_and_not_api_ok():
    assert snapshot_status([], api_ok=False) == "API_ERROR"


# ── write_live_snapshot ───────────────────────────────────────────────────


def test_write_live_snapshot_persists_events_status_and_count(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "live_snapshot.json"
    monkeypatch.setattr(mod, "_SNAPSHOT_PATH", snapshot_path)
    events = [_fake_enriched_event(1), _fake_enriched_event(2)]

    write_live_snapshot(events, "OK")

    data = json.loads(snapshot_path.read_text())
    assert data["status"] == "OK"
    assert data["count"] == 2
    assert data["events"] == events
    assert data["generated_at"]  # timestamp presente


def test_write_live_snapshot_empty_events_still_has_fresh_timestamp(tmp_path, monkeypatch):
    """0 jogos legítimo (NO_LIVE_GAMES) continua a publicar generated_at —
    é o campo que o browser usa para nunca confundir '0 jogos' com 'dados
    obsoletos' (ver loadLiveSnapshot() em index.html)."""
    snapshot_path = tmp_path / "live_snapshot.json"
    monkeypatch.setattr(mod, "_SNAPSHOT_PATH", snapshot_path)

    write_live_snapshot([], "NO_LIVE_GAMES")

    data = json.loads(snapshot_path.read_text())
    assert data["status"] == "NO_LIVE_GAMES"
    assert data["count"] == 0
    assert data["events"] == []
    assert data["generated_at"]


# ── maybe_commit_live_snapshot ────────────────────────────────────────────


def test_maybe_commit_live_snapshot_throttles_repeated_commits(tmp_path, monkeypatch):
    """Mesmo motivo do throttle em maybe_commit_health(): não gerar um commit
    por ciclo de 60s durante 5h50 de loop."""
    monkeypatch.setattr(mod, "_SNAPSHOT_PATH", tmp_path / "live_snapshot.json")
    commits = []
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: commits.append(msg))
    state: dict = {"_last_committed_at": 0.0}

    maybe_commit_live_snapshot([], "NO_LIVE_GAMES", state, force=True)  # 1º sempre passa
    maybe_commit_live_snapshot([], "NO_LIVE_GAMES", state)  # imediatamente a seguir — throttled
    assert len(commits) == 1

    maybe_commit_live_snapshot([], "NO_LIVE_GAMES", state, force=True)  # force ignora o throttle
    assert len(commits) == 2


def test_maybe_commit_live_snapshot_always_writes_even_when_throttled(tmp_path, monkeypatch):
    """O disco fica sempre actualizado (mesmo padrão de write_health() em
    maybe_commit_health()) — só o commit/push git é que é throttled."""
    snapshot_path = tmp_path / "live_snapshot.json"
    monkeypatch.setattr(mod, "_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: True)
    state: dict = {"_last_committed_at": 0.0}

    maybe_commit_live_snapshot([_fake_enriched_event(1)], "OK", state, force=True)
    maybe_commit_live_snapshot([_fake_enriched_event(1), _fake_enriched_event(2)], "OK", state)

    data = json.loads(snapshot_path.read_text())
    assert data["count"] == 2  # disco reflecte a 2ª escrita, mesmo sem novo commit


def test_maybe_commit_live_snapshot_independent_throttle_from_health(tmp_path, monkeypatch):
    """snapshot_state e health usam relógios de throttle independentes —
    forçar um não deve destravar o outro."""
    monkeypatch.setattr(mod, "_SNAPSHOT_PATH", tmp_path / "live_snapshot.json")
    monkeypatch.setattr(mod, "_HEALTH_PATH", tmp_path / "health.json")
    commits = []
    monkeypatch.setattr(mod, "git_commit_push", lambda files, msg: commits.append(msg))

    health = mod.new_health_state()
    snapshot_state: dict = {"_last_committed_at": 0.0}

    mod.maybe_commit_health(health, running=True, force=True)
    maybe_commit_live_snapshot([], "NO_LIVE_GAMES", snapshot_state, force=True)
    assert len(commits) == 2  # ambos os commits forçados aconteceram, sem se bloquearem

    mod.maybe_commit_health(health, running=True)  # throttled
    maybe_commit_live_snapshot([], "NO_LIVE_GAMES", snapshot_state)  # throttled
    assert len(commits) == 2


# ── scan_once expõe enriched_events para quem publica o snapshot ─────────


def test_scan_once_exposes_enriched_events_in_health_stats(monkeypatch):
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [{"id": 42}])
    monkeypatch.setattr(mod, "enrich_event", lambda api_key, ev, pick_ids: _fake_enriched_event(42))
    monkeypatch.setattr(mod, "detect_patterns", lambda e, state: [])

    health_stats: dict = {}
    mod.scan_once("fake_key", {"ht": {}, "mkt": {}}, set(), set(),
                  verbose=False, health_stats=health_stats)

    assert len(health_stats["enriched_events"]) == 1
    assert health_stats["enriched_events"][0]["id"] == 42


def test_scan_once_exposes_empty_enriched_events_when_no_live_games(monkeypatch):
    monkeypatch.setattr(mod, "fetch_live_events", lambda api_key, verbose=False: [])

    health_stats: dict = {}
    mod.scan_once("fake_key", {"ht": {}, "mkt": {}}, set(), set(),
                  verbose=False, health_stats=health_stats)

    assert health_stats["enriched_events"] == []
