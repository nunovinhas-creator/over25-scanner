"""
tests/pipeline/test_1x2_filters.py
------------------------------------
Testes para pipeline.etl.filter_1x2_alert_candidates.

Inclui um teste sentinel (xfail) que falha enquanto odds_fecho não for
implementado — serve de lembrete permanente da TAREFA 1.4 (Prompt 3).

Correr com:
    pytest tests/pipeline/test_1x2_filters.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from pipeline.etl import filter_1x2_alert_candidates
from pipeline.config import MAX_TIMING_H_1X2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WHITELIST = [
    "Premier League", "La Liga", "Bundesliga", "Serie A", "Ligue 1",
    "Primeira Liga", "Eredivisie", "Belgian Pro League",
    "Championship", "La Liga 2", "Bundesliga 2", "Serie B",
]

_PICKS1X2_PATH = Path(__file__).resolve().parents[2] / "data" / "picks_1x2.json"


def _pick(**kwargs: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "id": "1_sh",
        "casa": "Team A",
        "fora": "Team B",
        "liga": "Premier League",
        "outcome": "HOME",
        "timing_h": "3.0",
        "resultado_outcome": "",
        "odds_fecho": "",
    }
    return {**base, **kwargs}


# ---------------------------------------------------------------------------
# Tarefa 1.1 — Liga whitelist
# ---------------------------------------------------------------------------


class TestLigaWhitelist1x2:
    def test_empty_liga_rejected_to_1x2_file(self, tmp_path: Path) -> None:
        """Pick 1X2 com liga vazia → rejected_picks_1x2.json, não alerta TG."""
        pick = _pick(id="1_sh", liga="")
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0
        assert stats["n_rejected_liga"] == 1

    def test_empty_liga_written_to_rejected_1x2(self, tmp_path: Path) -> None:
        """Liga vazia escreve reject_reason='liga_vazia' em rejected_picks_1x2.json."""
        rejected_path = tmp_path / "rejected_picks_1x2.json"
        filter_1x2_alert_candidates(
            [_pick(id="2_sh", liga="")], _WHITELIST, rejected_path
        )
        rejected = json.loads(rejected_path.read_text())
        assert len(rejected) == 1
        assert rejected[0]["reject_reason"] == "liga_vazia"

    def test_unknown_liga_rejected(self, tmp_path: Path) -> None:
        """Liga fora da whitelist (ex: Brasileirao) → rejeitada."""
        pick = _pick(id="3_sh", liga="Brasileirao")
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0
        assert stats["n_rejected_liga"] == 1

    def test_known_liga_passes(self, tmp_path: Path) -> None:
        """Liga whitelisted → candidato a alerta (se outros gates também passarem)."""
        pick = _pick(id="4_sh", liga="Bundesliga", timing_h="2.0")
        candidates, _ = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 1

    def test_rejected_file_uses_1x2_suffix(self, tmp_path: Path) -> None:
        """Verifica que o ficheiro de rejeição é separado do Over 2.5."""
        rejected_path = tmp_path / "rejected_picks_1x2.json"
        filter_1x2_alert_candidates(
            [_pick(id="5_sh", liga="")], _WHITELIST, rejected_path
        )
        assert rejected_path.exists()
        assert not (tmp_path / "rejected_picks.json").exists()


# ---------------------------------------------------------------------------
# Tarefa 1.2 — DRAW bloqueado
# ---------------------------------------------------------------------------


class TestDraw1x2:
    def test_draw_is_blocked_from_alert(self, tmp_path: Path) -> None:
        """outcome=DRAW nunca gera alerta, mesmo com liga e timing válidos."""
        pick = _pick(id="10_sh", outcome="DRAW", liga="La Liga", timing_h="2.0")
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0
        assert stats["n_draw_blocked"] == 1

    def test_draw_not_written_to_rejected_liga(self, tmp_path: Path) -> None:
        """DRAW é bloqueado por gate próprio, não pelo gate de liga."""
        rejected_path = tmp_path / "rejected_picks_1x2.json"
        pick = _pick(id="11_sh", outcome="DRAW", liga="Serie A", timing_h="1.0")
        _, stats = filter_1x2_alert_candidates([pick], _WHITELIST, rejected_path)
        # Não deve aparecer em rejected_picks_1x2.json (liga é válida)
        assert stats["n_rejected_liga"] == 0
        assert stats["n_draw_blocked"] == 1

    def test_home_and_away_pass_draw_gate(self, tmp_path: Path) -> None:
        """HOME e AWAY passam o gate DRAW sem problemas."""
        picks = [
            _pick(id="12_sh", outcome="HOME", timing_h="2.0"),
            _pick(id="13_sh", outcome="AWAY", timing_h="2.0"),
        ]
        candidates, stats = filter_1x2_alert_candidates(
            picks, _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 2
        assert stats["n_draw_blocked"] == 0


# ---------------------------------------------------------------------------
# Tarefa 1.3 — Gate de timing
# ---------------------------------------------------------------------------


class TestTimingGate1x2:
    def test_timing_above_max_blocked(self, tmp_path: Path) -> None:
        """Pick com timing_h > MAX_TIMING_H_1X2 (6h) → gravado mas sem alerta."""
        pick = _pick(id="20_sh", timing_h=str(MAX_TIMING_H_1X2 + 1.0))
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0
        assert stats["n_timing_blocked"] == 1

    def test_timing_at_max_passes(self, tmp_path: Path) -> None:
        """timing_h == MAX_TIMING_H_1X2 (6h exato) → passa."""
        pick = _pick(id="21_sh", timing_h=str(MAX_TIMING_H_1X2))
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 1
        assert stats["n_timing_blocked"] == 0

    def test_timing_below_max_passes(self, tmp_path: Path) -> None:
        """timing_h < 6h → passa (zona de melhor ROI histórico)."""
        for h in ["0.5", "1.0", "3.0", "5.9"]:
            pick = _pick(id=f"22_sh_{h}", timing_h=h)
            candidates, _ = filter_1x2_alert_candidates(
                [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
            )
            assert len(candidates) == 1, f"timing_h={h} deve passar o gate"

    def test_timing_large_value_blocked(self, tmp_path: Path) -> None:
        """Picks com 17h+ ao KO (média histórica) → bloqueados."""
        pick = _pick(id="23_sh", timing_h="17.4")
        candidates, _ = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0

    def test_timing_missing_blocked(self, tmp_path: Path) -> None:
        """timing_h vazio → bloqueado (precaução contra dados em falta)."""
        pick = _pick(id="24_sh", timing_h="")
        candidates, stats = filter_1x2_alert_candidates(
            [pick], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 0
        assert stats["n_timing_blocked"] == 1

    def test_all_three_gates_combined(self, tmp_path: Path) -> None:
        """Só o pick que passa os 3 gates gera alerta."""
        picks = [
            _pick(id="30_sh", liga="", timing_h="2.0"),          # liga vazia → rejeito
            _pick(id="31_sh", outcome="DRAW", timing_h="2.0"),   # DRAW → bloqueado
            _pick(id="32_sh", timing_h="20.0"),                   # timing > 6h → bloqueado
            _pick(id="33_sh", timing_h="3.0"),                    # OK → alerta
        ]
        candidates, stats = filter_1x2_alert_candidates(
            picks, _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert len(candidates) == 1
        assert candidates[0]["id"] == "33_sh"
        assert stats["n_rejected_liga"] == 1
        assert stats["n_draw_blocked"] == 1
        assert stats["n_timing_blocked"] == 1


# ---------------------------------------------------------------------------
# Tarefa 1.4 — CLV tracking (sentinel xfail)
# ---------------------------------------------------------------------------


class TestCLVTracking1x2:
    def test_stats_include_settled_sem_clv_count(self, tmp_path: Path) -> None:
        """filter_1x2_alert_candidates reporta n_settled_sem_clv nas stats."""
        settled_sem_close = _pick(
            id="40_sh", resultado_outcome="WIN", odds_fecho=""
        )
        _, stats = filter_1x2_alert_candidates(
            [settled_sem_close], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert "n_settled_sem_clv" in stats
        assert stats["n_settled_sem_clv"] == 1

    def test_settled_with_odds_fecho_not_counted(self, tmp_path: Path) -> None:
        """Picks settled COM odds_fecho não entram na contagem sem_clv."""
        settled_ok = _pick(
            id="41_sh", resultado_outcome="WIN", odds_fecho="2.34"
        )
        _, stats = filter_1x2_alert_candidates(
            [settled_ok], _WHITELIST, tmp_path / "rejected_picks_1x2.json"
        )
        assert stats["n_settled_sem_clv"] == 0

    @pytest.mark.xfail(
        reason=(
            "SENTINEL — odds_fecho não implementado no syncResults1x2(). "
            "Este teste falha intencionalmente enquanto todos os picks settled "
            "tiverem odds_fecho vazio. "
            "Implementar captura de odds Pinnacle de fecho (Prompt 3). "
            "Remove este xfail quando o teste começar a passar."
        ),
        strict=True,
    )
    def test_all_settled_picks_have_odds_fecho(self) -> None:
        """
        SENTINEL — falha enquanto odds_fecho não for preenchido em picks_1x2.json.

        Todos os picks settled (WIN/LOSS) devem ter odds_fecho preenchido para
        o CLV poder ser calculado: clv = odds_entrada / odds_fecho - 1.

        Estado actual: 269/269 picks settled sem odds_fecho.
        Implementação pendente: ver TAREFA 1.4 (Prompt 3).
        """
        if not _PICKS1X2_PATH.exists():
            pytest.skip("data/picks_1x2.json não encontrado")

        picks = json.loads(_PICKS1X2_PATH.read_text())
        settled = [p for p in picks if p.get("resultado_outcome") in ("WIN", "LOSS")]
        settled_sem_close = [
            p for p in settled if not str(p.get("odds_fecho", "")).strip()
        ]

        assert len(settled_sem_close) == 0, (
            f"{len(settled_sem_close)}/{len(settled)} picks settled sem odds_fecho — "
            "CLV não calculado. Implementar captura de odds_fecho (Prompt 3)."
        )
