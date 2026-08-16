"""
tests/pipeline/test_send_sharp1x2_weekly.py
----------------------------------------------
Testes do Bloco M para backtesting/send_sharp1x2_weekly.py::compute_stats()
— confirma que a dedup de registos "_update" (dedup_sharp1x2_picks(), ver
pipeline/scan_common.py) corre antes de qualquer contagem/KPI, para que a
mesma aposta real (jogo+outcome) gravada duas vezes por scan_sharp1x2.py
nunca conte como dois settlements independentes.

Todos os dados aqui são sintéticos (# synthetic).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backtesting.send_sharp1x2_weekly import compute_stats

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=1)).isoformat()


def _pick(**over):  # synthetic
    base = {
        "id": "1_home_sh",
        "outcome": "HOME",
        "data": RECENT,
        "gate_blocked_reason": "",
        "data_quality_flag": False,
        "resultado_outcome": "",
        "clv": "",
        "div_b365_pin": "5.0",
        "saved_at": "2026-08-14T12:00:00Z",
    }
    base.update(over)
    return base


def test_update_pair_counted_once_in_total_alertados():
    original = _pick(id="214775_home_sh", saved_at="2026-08-14T12:50:29Z")
    update = _pick(id="214775_home_sh_update", saved_at="2026-08-14T15:14:42Z")

    stats = compute_stats([original, update])

    assert stats["total_alertados"] == 1


def test_update_pair_counted_once_in_settled():
    original = _pick(
        id="1_home_sh", resultado_outcome="WIN", clv="-15.7303",
        saved_at="2026-08-14T12:00:00Z",
    )
    update = _pick(
        id="1_home_sh_update", resultado_outcome="LOSS", clv="-5.618",
        saved_at="2026-08-14T15:00:00Z",
    )

    stats = compute_stats([original, update])

    assert stats["total_settled"] == 1


def test_clv_rolling30_uses_only_the_most_recent_record_of_the_pair():
    """A perda real de 214775 não pode entrar duas vezes na média de CLV."""
    original = _pick(
        id="1_home_sh", resultado_outcome="LOSS", clv="-15.7303",
        saved_at="2026-08-14T12:00:00Z",
    )
    update = _pick(
        id="1_home_sh_update", resultado_outcome="LOSS", clv="-5.618",
        saved_at="2026-08-14T15:00:00Z",
    )

    stats = compute_stats([original, update])

    assert stats["n_rolling30"] == 1
    assert stats["clv_mean30"] == -5.618  # só o registo mais recente


def test_outcome_stats_home_away_do_not_double_count_pair():
    original = _pick(id="1_home_sh", outcome="HOME", saved_at="2026-08-14T12:00:00Z")
    update = _pick(id="1_home_sh_update", outcome="HOME", saved_at="2026-08-14T15:00:00Z")

    stats = compute_stats([original, update])

    assert stats["home"]["n"] == 1


def test_picks_without_update_pair_unaffected():
    picks = [
        _pick(id="1_home_sh", outcome="HOME"),
        _pick(id="2_away_sh", outcome="AWAY"),
    ]
    stats = compute_stats(picks)
    assert stats["total_alertados"] == 2


def test_dedup_does_not_mutate_caller_list():
    original = _pick(id="1_home_sh", saved_at="2026-08-14T12:00:00Z")
    update = _pick(id="1_home_sh_update", saved_at="2026-08-14T15:00:00Z")
    picks = [original, update]
    compute_stats(picks)
    assert picks == [original, update]
