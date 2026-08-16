"""
tests/pipeline/test_send_dc_model_coverage_summary.py
--------------------------------------------------------
Bloco P — resumo diário no Telegram da cobertura do modelo Dixon-Coles no
Over 2.5 (pipeline/send_dc_model_coverage_summary.py). Cobre: agregação por
liga (n, n_dc, %), janela de 24h com acumulado, mensagem curta quando não
há candidatos avaliados, e falha explícita (mensagem de erro + exit code
!= 0) quando um dos ficheiros falta ou é inválido — nunca um "sucesso"
silencioso.

Todos os dados aqui são sintéticos (# synthetic). REJECTED_FILE, PICKS_FILE
e send_telegram são sempre monkeypatched: nenhum teste toca no disco do
repositório real nem chama a rede.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.send_dc_model_coverage_summary as mod
from pipeline.send_dc_model_coverage_summary import (
    aggregate_by_league,
    build_summary,
    main,
)

NOW = datetime(2026, 8, 20, 20, 0, 0, tzinfo=timezone.utc)  # synthetic


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(**over):  # synthetic
    base = {
        "id": "1", "liga": "Championship",
        "scanned_at": _iso(NOW - timedelta(hours=1)),
        "p_model_source": "market_only",
        "reject_reason": "sem_modelo_dc",
    }
    base.update(over)
    return base


# ── aggregate_by_league ────────────────────────────────────────────────


def test_aggregate_counts_dc_and_market_only_separately():
    entries = [
        _entry(id="1", liga="Championship", p_model_source="dc"),
        _entry(id="2", liga="Championship", p_model_source="market_only"),
        _entry(id="3", liga="Championship", p_model_source="market_only"),
    ]
    agg = aggregate_by_league(entries)
    assert agg["Championship"]["n"] == 3
    assert agg["Championship"]["n_dc"] == 1
    assert agg["Championship"]["pct"] == pytest.approx(33.333, rel=1e-3)


def test_aggregate_skips_entries_without_p_model_source():
    """Rejeições de gates anteriores (liga/timing/odds/drifting) nunca
    chamam compute_prob() — não têm p_model_source, ficam fora."""
    entries = [
        _entry(liga="Championship", p_model_source="dc"),
        {"liga": "Championship", "reject_reason": "timing_apos_6h"},
    ]
    agg = aggregate_by_league(entries)
    assert agg["Championship"]["n"] == 1


def test_aggregate_groups_by_liga():
    entries = [
        _entry(liga="Championship", p_model_source="dc"),
        _entry(liga="La Liga 2", p_model_source="market_only"),
    ]
    agg = aggregate_by_league(entries)
    assert set(agg.keys()) == {"Championship", "La Liga 2"}


def test_aggregate_uses_desconhecida_fallback_for_missing_liga():
    entries = [_entry(liga="", p_model_source="dc")]
    agg = aggregate_by_league(entries)
    assert "liga desconhecida" in agg


# ── build_summary — janela 24h, agregado, acumulado ──────────────────────


def test_build_summary_short_message_when_nothing_in_24h():
    old_entry = _entry(scanned_at=_iso(NOW - timedelta(hours=48)))
    msg = build_summary([old_entry], NOW)
    assert "Sem candidatos avaliados pelo Gate 4 (EV) nas últimas 24h" in msg
    assert "Acumulado desde o início" in msg


def test_build_summary_short_message_with_no_history_at_all():
    msg = build_summary([], NOW)
    assert "Sem candidatos avaliados pelo Gate 4 (EV) nas últimas 24h" in msg
    assert "Sem histórico acumulado ainda" in msg


def test_build_summary_shows_dc_ratio_per_league():
    e = _entry(liga="Primeira Liga", p_model_source="dc")
    msg = build_summary([e], NOW)
    assert "Primeira Liga: n=1 · com modelo DC 1/1 (100%)" in msg


def test_build_summary_shows_zero_percent_when_all_market_only():
    e = _entry(liga="Championship", p_model_source="market_only")
    msg = build_summary([e], NOW)
    assert "Championship: n=1 · com modelo DC 0/1 (0%)" in msg


def test_build_summary_excludes_outside_window_from_24h_but_keeps_in_accumulated():
    recent = _entry(id="r", scanned_at=_iso(NOW - timedelta(hours=2)), liga="Championship")
    old = _entry(id="o", scanned_at=_iso(NOW - timedelta(hours=30)), liga="Championship")
    msg = build_summary([recent, old], NOW)
    assert "Últimas 24h — 1 liga(s)" in msg
    assert "Acumulado desde o início — 1 liga(s), 0/2 com modelo DC (0%)" in msg


def test_build_summary_header_has_lisbon_local_time():
    msg = build_summary([_entry()], NOW)
    assert "Lisboa" in msg.splitlines()[0]


def test_build_summary_truncates_long_league_list():
    entries = [_entry(id=str(i), liga=f"Liga {i}") for i in range(25)]
    msg = build_summary(entries, NOW)
    assert "… e mais 5 liga(s)" in msg


# ── main() — falha explícita, nunca silenciosa ───────────────────────────


def test_main_sends_error_and_returns_nonzero_when_rejected_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REJECTED_FILE", tmp_path / "does_not_exist.json")
    monkeypatch.setattr(mod, "PICKS_FILE", tmp_path / "picks.json")
    (tmp_path / "picks.json").write_text("[]", encoding="utf-8")
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1
    assert "falhou" in sent[0]


def test_main_sends_error_and_returns_nonzero_when_picks_file_invalid(tmp_path, monkeypatch):
    rejected_file = tmp_path / "rejected_picks.json"
    rejected_file.write_text("[]", encoding="utf-8")
    picks_file = tmp_path / "picks.json"
    picks_file.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(mod, "REJECTED_FILE", rejected_file)
    monkeypatch.setattr(mod, "PICKS_FILE", picks_file)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1


def test_main_treats_empty_lists_as_valid_no_signals_not_error(tmp_path, monkeypatch):
    rejected_file = tmp_path / "rejected_picks.json"
    rejected_file.write_text("[]", encoding="utf-8")
    picks_file = tmp_path / "picks.json"
    picks_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "REJECTED_FILE", rejected_file)
    monkeypatch.setattr(mod, "PICKS_FILE", picks_file)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 0
    assert len(sent) == 1
    assert "Sem candidatos avaliados pelo Gate 4 (EV) nas últimas 24h" in sent[0]


def test_main_returns_nonzero_when_telegram_send_fails(tmp_path, monkeypatch):
    rejected_file = tmp_path / "rejected_picks.json"
    rejected_file.write_text("[]", encoding="utf-8")
    picks_file = tmp_path / "picks.json"
    picks_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "REJECTED_FILE", rejected_file)
    monkeypatch.setattr(mod, "PICKS_FILE", picks_file)
    monkeypatch.setattr(mod, "send_telegram", lambda text: False)

    assert main() == 1


def test_main_combines_rejected_and_picks_files(tmp_path, monkeypatch):
    rejected_file = tmp_path / "rejected_picks.json"
    rejected_file.write_text(json.dumps([_entry(liga="Championship", p_model_source="market_only")]),
                              encoding="utf-8")
    picks_file = tmp_path / "picks.json"
    picks_file.write_text(json.dumps([_entry(id="2", liga="Championship", p_model_source="dc")]),
                           encoding="utf-8")
    monkeypatch.setattr(mod, "REJECTED_FILE", rejected_file)
    monkeypatch.setattr(mod, "PICKS_FILE", picks_file)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    assert main() == 0
    assert "com modelo DC 1/2" in sent[0]
