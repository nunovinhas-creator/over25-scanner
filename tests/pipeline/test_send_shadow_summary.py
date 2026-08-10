"""
tests/pipeline/test_send_shadow_summary.py
--------------------------------------------
Testes do Bloco K — resumo diário no Telegram do MODO SOMBRA
(pipeline/send_shadow_summary.py). Cobre: contagem enviaria/bloqueados por
filtro, WR sempre acompanhado de odd média/break-even/ROI (nunca sozinho),
mensagem curta quando não há sinais nas últimas 24h (com acumulado), e
falha explícita (mensagem de erro + exit code != 0) quando o ficheiro
falta ou é inválido — nunca um "sucesso" silencioso.

Todos os dados aqui são sintéticos (# synthetic). SHADOW_FILE e
send_telegram são sempre monkeypatched: nenhum teste toca no disco do
repositório real nem chama a rede.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import pipeline.send_shadow_summary as mod
from pipeline.send_shadow_summary import (
    build_summary,
    compute_segment,
    format_segment,
    main,
)

NOW = datetime(2026, 8, 10, 20, 0, 0, tzinfo=timezone.utc)  # synthetic


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _entry(**over):  # synthetic
    base = {
        "id": "1_shadow_send_123",
        "event_id": "1",
        "casa": "Casa", "fora": "Fora", "liga": "Serie A",
        "detected_at": _iso(NOW - timedelta(hours=1)),
        "min": 60, "score": "1-0", "goals": 1,
        "xg_total": 2.0, "pattern_score": 20, "pressao": 87.0,
        "odds_live": 1.75, "odds_status": "VALID",
        "blocked_by": None,
        "result_over25": "", "final_score": "", "result_at_min": "",
        "closing_odds_over25": None,
    }
    base.update(over)
    return base


# ── compute_segment / format_segment — WR nunca sozinho ─────────────────


def test_compute_segment_empty_when_no_settled():
    seg = compute_segment([_entry(result_over25="")])
    assert seg == {"n": 0, "excluded": 0, "wr": None, "avg_odds": None, "breakeven": None, "roi": None}


def test_compute_segment_win_loss_matches_calibsegment_formula():
    entries = [
        _entry(result_over25="WIN", odds_live=2.0),
        _entry(result_over25="LOSS", odds_live=1.5),
    ]
    seg = compute_segment(entries)
    assert seg["n"] == 2
    assert seg["wr"] == 50.0
    assert seg["avg_odds"] == pytest.approx(1.75)
    assert seg["breakeven"] == pytest.approx(100 / 1.75)
    # profit = (2.0-1) + (-1) = 0 -> roi = 0/2*100 = 0
    assert seg["roi"] == pytest.approx(0.0)


def test_compute_segment_excludes_settled_without_usable_odds():
    entries = [
        _entry(result_over25="WIN", odds_live=None),
        _entry(result_over25="LOSS", odds_live=1.8),
    ]
    seg = compute_segment(entries)
    assert seg["n"] == 1
    assert seg["excluded"] == 1


def test_compute_segment_only_counts_settled():
    entries = [_entry(result_over25=""), _entry(result_over25="WIN", odds_live=2.0)]
    seg = compute_segment(entries)
    assert seg["n"] == 1


def test_format_segment_always_pairs_wr_with_roi_and_breakeven():
    seg = compute_segment([_entry(result_over25="WIN", odds_live=2.0)])
    text = format_segment("Resultado", seg)
    assert "WR" in text and "ROI" in text and "break-even" in text and "odd média" in text


def test_format_segment_handles_zero_n_without_crashing():
    seg = compute_segment([])
    text = format_segment("Resultado", seg)
    assert "sem sinais resolvidos" in text
    assert "WR" not in text  # nunca finge um WR quando n=0


# ── build_summary — janela 24h, contagem por filtro, acumulado ──────────


def test_build_summary_short_message_when_nothing_in_24h():
    old_entry = _entry(detected_at=_iso(NOW - timedelta(hours=48)))
    msg = build_summary([old_entry], NOW)
    assert "Sem sinais nas últimas 24h" in msg
    assert "Acumulado desde o início" in msg  # acumulado sempre presente


def test_build_summary_counts_sent_and_blocked_by_filter():
    entries = [
        _entry(id="a", blocked_by=None),
        _entry(id="b", blocked_by="xg_banda_morta"),
        _entry(id="c", blocked_by="xg_banda_morta"),
        _entry(id="d", blocked_by="minuto_tardio"),
    ]
    msg = build_summary(entries, NOW)
    assert "enviaria: 1" in msg
    assert "xg_banda_morta: 2" in msg
    assert "minuto_tardio: 1" in msg


def test_build_summary_lists_sent_signals_with_required_fields():
    e = _entry(casa="Sporting", fora="Benfica", liga="Primeira Liga",
               min=73, goals=2, score="2-0", pressao=91.0, odds_live=1.55)
    msg = build_summary([e], NOW)
    assert "Sporting vs Benfica (Primeira Liga)" in msg
    assert "73'" in msg
    assert "2 golos" in msg
    assert "2-0" in msg
    assert "Pressão 91" in msg
    assert "odd 1.55" in msg


def test_build_summary_never_lists_blocked_signals_individually():
    e = _entry(id="blocked1", blocked_by="minuto_tardio", casa="NuncaAparece")
    msg = build_summary([e], NOW)
    assert "NuncaAparece" not in msg


def test_build_summary_excludes_outside_window_from_24h_but_keeps_in_accumulated():
    recent = _entry(id="r", detected_at=_iso(NOW - timedelta(hours=2)), blocked_by=None)
    old = _entry(id="o", detected_at=_iso(NOW - timedelta(hours=30)), blocked_by=None)
    msg = build_summary([recent, old], NOW)
    assert "enviaria: 1" in msg  # só o recente na janela 24h
    assert "Acumulado desde o início do modo sombra — enviaria: 2" in msg


def test_build_summary_result_section_present_for_24h_and_accumulated():
    e = _entry(blocked_by=None, result_over25="WIN", odds_live=2.0,
                detected_at=_iso(NOW - timedelta(hours=1)))
    msg = build_summary([e], NOW)
    assert "Resultado 24h" in msg
    assert "Resultado acumulado" in msg


def test_build_summary_truncates_long_signal_list():
    entries = [
        _entry(id=f"e{i}", event_id=str(i), blocked_by=None,
               detected_at=_iso(NOW - timedelta(hours=1)))
        for i in range(30)
    ]
    msg = build_summary(entries, NOW)
    assert "… e mais 5" in msg


def test_build_summary_header_has_lisbon_local_time():
    msg = build_summary([_entry(detected_at=_iso(NOW - timedelta(hours=1)))], NOW)
    assert "Lisboa" in msg.splitlines()[0]


# ── main() — falha explícita, nunca silenciosa ───────────────────────────


def test_main_sends_error_and_returns_nonzero_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "SHADOW_FILE", tmp_path / "does_not_exist.json")
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1
    assert "falhou" in sent[0]


def test_main_sends_error_and_returns_nonzero_when_json_invalid(tmp_path, monkeypatch):
    f = tmp_path / "live_shadow_alerts.json"
    f.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(mod, "SHADOW_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1


def test_main_sends_error_and_returns_nonzero_when_not_a_list(tmp_path, monkeypatch):
    f = tmp_path / "live_shadow_alerts.json"
    f.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    monkeypatch.setattr(mod, "SHADOW_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 1
    assert len(sent) == 1


def test_main_treats_empty_list_as_valid_no_signals_not_error(tmp_path, monkeypatch):
    """Ficheiro presente com [] é um histórico legítimo (ainda) vazio —
    diferente de ausente/inválido, não deve ser tratado como erro."""
    f = tmp_path / "live_shadow_alerts.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "SHADOW_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    code = main()

    assert code == 0
    assert len(sent) == 1
    assert "Sem sinais nas últimas 24h" in sent[0]


def test_main_returns_nonzero_when_telegram_send_fails(tmp_path, monkeypatch):
    f = tmp_path / "live_shadow_alerts.json"
    f.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(mod, "SHADOW_FILE", f)
    monkeypatch.setattr(mod, "send_telegram", lambda text: False)

    assert main() == 1


def test_main_returns_zero_on_success(tmp_path, monkeypatch):
    entries = [{
        "id": "1_shadow_send_1", "event_id": "1", "casa": "A", "fora": "B",
        "liga": "Serie A", "detected_at": _iso(datetime.now(timezone.utc)),
        "min": 10, "score": "0-0", "goals": 0, "xg_total": 1.0,
        "pattern_score": 10, "pressao": 60.0, "odds_live": 1.9,
        "odds_status": "VALID", "blocked_by": None,
        "result_over25": "", "final_score": "", "result_at_min": "",
        "closing_odds_over25": None,
    }]
    f = tmp_path / "live_shadow_alerts.json"
    f.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(mod, "SHADOW_FILE", f)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: (sent.append(text), True)[1])

    assert main() == 0
    assert len(sent) == 1  # mensagem única por corrida — nunca alertas por jogo
