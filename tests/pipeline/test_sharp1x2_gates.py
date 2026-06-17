"""
tests/pipeline/test_sharp1x2_gates.py
--------------------------------------
Testes de regressão para a lógica de gates Sharp 1X2.

Verifica que _applySharp1x2Gates (equivalente Python da função JS)
aplica corretamente os critérios de filtragem:
  - liga na whitelist
  - outcome ≠ DRAW (excepto N1 tracking)
  - HOME N1 bloqueado
  - timing 0–6h ao KO
  - div_b365_pin > 3%

Também testa que picks com data_quality_flag são excluídos
dos alertados no relatório semanal.
"""

from __future__ import annotations

import pytest
from backtesting.send_sharp1x2_weekly import compute_stats

# ── equivalente Python de _applySharp1x2Gates (JS) ──────────────────────────
WHITELIST = [
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2",
    # Bundesliga 2 e Serie B removidas — ausentes da BSD API.
]


def apply_sharp1x2_gates(out: str, liga: str, div, timing_h: float) -> str:
    """Python mirror of _applySharp1x2Gates() in index.html."""
    out = (out or "").upper()
    liga = (liga or "").strip()
    is_n1 = liga == "Eredivisie"
    if not liga or liga not in WHITELIST:
        return "liga_fora_whitelist"
    if out == "DRAW" and is_n1 and div is not None and div >= 0.03:
        return "draw_observacao_n1"
    if out == "DRAW":
        return "draw_suspenso"
    if out == "HOME" and is_n1:
        return "n1_home_negativo"
    if not (0 <= timing_h <= 6):
        return "timing_apos_6h"
    if div is None or div < 0.03:
        return "div_baixa"
    return ""


# ── testes de gate ────────────────────────────────────────────────────────────

class TestApplySharp1x2Gates:
    def test_timing_30h_blocked(self):
        """Pick com timing_h=30 deve ser bloqueado com timing_apos_6h."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="Premier League", div=0.05, timing_h=30
        )
        assert reason == "timing_apos_6h"

    def test_timing_5h_passes(self):
        """Pick com timing_h=5 e div>3% numa liga válida deve passar."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="Premier League", div=0.04, timing_h=5
        )
        assert reason == ""

    def test_liga_vazia_blocked(self):
        """Liga vazia deve ser bloqueada — liga_fora_whitelist."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="", div=0.05, timing_h=4
        )
        assert reason == "liga_fora_whitelist"

    def test_liga_fora_whitelist_blocked(self):
        """Liga fora da whitelist deve ser bloqueada."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="MLS", div=0.05, timing_h=4
        )
        assert reason == "liga_fora_whitelist"

    def test_draw_suspenso(self):
        """DRAW numa liga válida (não N1) deve ser bloqueado."""
        reason = apply_sharp1x2_gates(
            out="DRAW", liga="La Liga", div=0.05, timing_h=4
        )
        assert reason == "draw_suspenso"

    def test_draw_n1_tracking(self):
        """DRAW N1 com div≥3% vai para tracking, não alerta TG."""
        reason = apply_sharp1x2_gates(
            out="DRAW", liga="Eredivisie", div=0.05, timing_h=4
        )
        assert reason == "draw_observacao_n1"

    def test_home_n1_blocked(self):
        """HOME Eredivisie deve ser bloqueado — n1_home_negativo."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="Eredivisie", div=0.05, timing_h=4
        )
        assert reason == "n1_home_negativo"

    def test_div_baixa_blocked(self):
        """div < 3% deve ser bloqueado."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="Serie A", div=0.02, timing_h=4
        )
        assert reason == "div_baixa"

    def test_div_none_blocked(self):
        """div=None (B365 indisponível) deve ser bloqueado."""
        reason = apply_sharp1x2_gates(
            out="HOME", liga="Bundesliga", div=None, timing_h=4
        )
        assert reason == "div_baixa"

    def test_timing_exactly_6h_passes(self):
        """timing_h=6.0 está no limite — deve passar."""
        reason = apply_sharp1x2_gates(
            out="AWAY", liga="Ligue 1", div=0.04, timing_h=6.0
        )
        assert reason == ""

    def test_away_valid_passes(self):
        """AWAY numa liga válida, timing OK, div>3% deve passar."""
        reason = apply_sharp1x2_gates(
            out="AWAY", liga="Bundesliga", div=0.035, timing_h=2
        )
        assert reason == ""


# ── testes de data_quality_flag ───────────────────────────────────────────────

class TestDataQualityFlagExclusion:
    def _make_pick(self, **kwargs):
        base = {
            "id": "1_sh", "data": "2026-06-12T10:00:00+00:00",
            "outcome": "HOME", "gate_blocked_reason": "",
            "resultado_outcome": "", "clv": "", "div_b365_pin": "",
        }
        base.update(kwargs)
        return base

    def test_flagged_pick_excluded_from_alertados(self):
        """Qualquer data_quality_flag exclui o pick dos alertados."""
        picks = [
            self._make_pick(id="1_sh", data_quality_flag="pre_bugfix_timing_v1"),
            self._make_pick(id="2_sh", data_quality_flag="pre_bugfix_liga_vazia"),
            self._make_pick(id="3_sh", data_quality_flag="liga_fora_whitelist_pre_fix"),
            self._make_pick(id="4_sh"),  # pick limpo
        ]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 1

    def test_liga_fora_whitelist_flag_excluded(self):
        """Picks com data_quality_flag=liga_fora_whitelist_pre_fix excluídos dos alertados."""
        picks = [
            self._make_pick(id="1_sh", data_quality_flag="liga_fora_whitelist_pre_fix"),
            self._make_pick(id="2_sh"),
        ]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 1

    def test_post_fix_flag_excluded(self):
        """Picks com data_quality_flag=liga_fora_whitelist_post_fix excluídos dos alertados."""
        picks = [
            self._make_pick(id="1_sh", data_quality_flag="liga_fora_whitelist_post_fix"),
            self._make_pick(id="2_sh"),
        ]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 1

    def test_flagged_pick_not_in_settled(self):
        """Picks flagged com resultado não entram em settled."""
        picks = [
            self._make_pick(
                id="1_sh",
                data_quality_flag="pre_bugfix_timing_v1",
                resultado_outcome="WIN",
            ),
            self._make_pick(id="2_sh", resultado_outcome="WIN"),
        ]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 1
        assert stats["total_settled"] == 1

    def test_unflagged_pick_counted_normally(self):
        """Picks sem flag mas com gate_blocked_reason vazio são alertados normais."""
        picks = [self._make_pick(id=f"{i}_sh") for i in range(5)]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 5

    def test_blocked_pick_not_alertado(self):
        """Picks com gate_blocked_reason não são alertados."""
        picks = [
            self._make_pick(id="1_sh", gate_blocked_reason="timing_apos_6h"),
            self._make_pick(id="2_sh"),
        ]
        stats = compute_stats(picks)
        assert stats["total_alertados"] == 1
