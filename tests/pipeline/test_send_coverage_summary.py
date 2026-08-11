"""
tests/pipeline/test_send_coverage_summary.py
----------------------------------------------
Testes do Bloco L1 — resumo diário no Telegram da cobertura de dados
(pipeline/send_coverage_summary.py). Cobre: agregação por liga (n, score
médio, percentagem por campo), janela de 24h com acumulado, mensagem curta
quando não há jogos observados, e falha explícita (mensagem de erro + exit
code != 0) quando o ficheiro falta ou é inválido — nunca um "sucesso"
silencioso.

Todos os dados aqui são sintéticos (# synthetic). OBS_FILE e send_telegram
são sempre monkeypatched: nenhum teste toca no disco do repositório real
nem chama a rede.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.send_coverage_summary as mod
from pipeline.send_coverage_summary import (
    aggregate_by_league,
    build_summary,
    main,
)
from pipeline.scan_live import COVERAGE_FIELDS

NOW = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)  # synthetic


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _full_fields(**overrides) -> dict:
    fields = {f: True for f in COVERAGE_FIELDS}
    fields.update(overrides)
    return fields


def _entry(**over):  # synthetic
    base = {
        "id": "1_obs_123",
        "event_id": "1",
        "kind": "observation",
        "liga": "Serie A",
        "detected_at": _iso(NOW - timedelta(hours=1)),
        "coverage_score": len(COVERAGE_FIELDS),
        "coverage_total": len(COVERAGE_FIELDS),
        "coverage_fields": _full_fields(),
    }
    base.update(over)
    return base


# ── aggregate_by_league ────────────────────────────────────────────────


def test_aggregate_groups_by_liga_and_counts_n():
    entries = [_entry(liga="Serie A"), _entry(liga="Serie A"), _entry(liga="MLS")]
    agg = aggregate_by_league(entries)
    assert agg["Serie A"]["n"] == 2
    assert agg["MLS"]["n"] == 1


def test_aggregate_computes_average_score():
    entries = [
        _entry(liga="Serie A", coverage_score=6, coverage_fields=_full_fields()),
        _entry(liga="Serie A", coverage_score=0, coverage_fields=_full_fields(**{f: False for f in COVERAGE_FIELDS})),
    ]
    agg = aggregate_by_league(entries)
    assert agg["Serie A"]["avg_score"] == 3.0
    assert agg["Serie A"]["total"] == len(COVERAGE_FIELDS)


def test_aggregate_computes_field_percentage():
    entries = [
        _entry(liga="MLS", coverage_fields=_full_fields(xgTotal=True)),
        _entry(liga="MLS", coverage_fields=_full_fields(xgTotal=False)),
    ]
    agg = aggregate_by_league(entries)
    assert agg["MLS"]["field_pct"]["xgTotal"] == 50.0


def test_aggregate_skips_entries_without_coverage():
    """Entradas anteriores ao Bloco L1 (sem coverage_score/coverage_total)
    ficam fora da agregação — nada a medir nelas."""
    entries = [_entry(liga="Serie A"), {"liga": "Serie A", "event_id": "2"}]
    agg = aggregate_by_league(entries)
    assert agg["Serie A"]["n"] == 1


def test_aggregate_uses_desconhecida_fallback_for_missing_liga():
    entries = [_entry(liga="")]
    agg = aggregate_by_league(entries)
    assert "liga desconhecida" in agg


def test_aggregate_combines_observation_and_coverage_only_kinds():
    """👁 reais (kind=observation) e registos de cobertura pura
    (kind=coverage_only) entram na mesma agregação por liga."""
    entries = [
        _entry(liga="MLS", kind="observation"),
        _entry(liga="MLS", kind="coverage_only", event_id="2"),
    ]
    agg = aggregate_by_league(entries)
    assert agg["MLS"]["n"] == 2


# ── build_summary — janela 24h, agregado, acumulado ──────────────────────


def test_build_summary_short_message_when_nothing_in_24h():
    old_entry = _entry(detected_at=_iso(NOW - timedelta(hours=48)))
    msg = build_summary([old_entry], NOW)
    assert "Sem jogos observados nas últimas 24h" in msg
    assert "Acumulado desde o início" in msg


def test_build_summary_lists_league_with_n_and_percentage():
    e = _entry(liga="Primeira Liga", coverage_score=6, coverage_fields=_full_fields())
    msg = build_summary([e], NOW)
    assert "Primeira Liga: n=1 · cobertura média 6.0/6 (100%)" in msg


def test_build_summary_shows_low_coverage_for_leagues_without_stats():
    e = _entry(liga="International Friendly Games",
               coverage_score=0, coverage_fields=_full_fields(**{f: False for f in COVERAGE_FIELDS}))
    msg = build_summary([e], NOW)
    assert "International Friendly Games: n=1 · cobertura média 0.0/6 (0%)" in msg


def test_build_summary_excludes_outside_window_from_24h_but_keeps_in_accumulated():
    recent = _entry(event_id="r", detected_at=_iso(NOW - timedelta(hours=2)), liga="Serie A")
    old = _entry(event_id="o", detected_at=_iso(NOW - timedelta(hours=30)), liga="Serie A")
    msg = build_summary([recent, old], NOW)
    assert "Últimas 24h — 1 liga(s), 1 jogo(s)" in msg
    assert "Acumulado desde o início — 1 liga(s), 2 jogo(s) observado(s)" in msg


def test_build_summary_ranks_leagues_by_sample_size():
    entries = [
        _entry(event_id=str(i), liga="Serie A")
        for i in range(3)
    ] + [_entry(event_id="x", liga="MLS")]
    msg = build_summary(entries, NOW)
    lines = msg.splitlines()
    idx_a = next(i for i, l in enumerate(lines) if l.startswith("Serie A"))
    idx_mls = next(i for i, l in enumerate(lines) if l.startswith("MLS"))
    assert idx_a < idx_mls  # mais jogos observados aparece primeiro


def test_build_summary_truncates_long_league_list():
    entries = [_entry(event_id=str(i), liga=f"Liga {i}") for i in range(25)]
    msg = build_summary(entries, NOW)
    assert "… e mais 5 liga(s)" in msg


def test_build_summary_header_has_lisbon_local_time():
    msg = build_summary([_entry()], NOW)
    assert "Lisboa" in msg.splitlines()[0]


# ── main() — falha explícita, nunca silenciosa ───────────────────────────


def test_main_sends_error_and_returns_nonzero_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "OBS_FILE", tmp_path / "does_not_exist.json")
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1
    assert "falhou" in sent[0]


def test_main_sends_error_and_returns_nonzero_when_json_invalid(tmp_path, monkeypatch):
    f = tmp_path / "observations.json"
    f.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(mod, "OBS_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1


def test_main_sends_error_and_returns_nonzero_when_not_a_list(tmp_path, monkeypatch):
    f = tmp_path / "observations.json"
    f.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    monkeypatch.setattr(mod, "OBS_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1


def test_main_treats_empty_list_as_valid_no_signals_not_error(tmp_path, monkeypatch):
    f = tmp_path / "observations.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "OBS_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 0
    assert len(sent) == 1
    assert "Sem jogos observados nas últimas 24h" in sent[0]


def test_main_returns_nonzero_when_telegram_send_fails(tmp_path, monkeypatch):
    f = tmp_path / "observations.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "OBS_FILE", f)
    monkeypatch.setattr(mod, "send_telegram", lambda text: False)

    assert main() == 1


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    entries = [_entry()]
    f = tmp_path / "observations.json"
    f.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(mod, "OBS_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    assert main() == 0
    assert len(sent) == 1  # mensagem única por corrida
