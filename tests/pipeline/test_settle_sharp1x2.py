"""
tests/pipeline/test_settle_sharp1x2.py
-----------------------------------------
Testes para pipeline/settle_sharp1x2.py (Ponto 2, sessão data-quality-fixes).

Cobre a causa raiz corrigida: nada definia resultado_outcome para os picks
Sharp 1X2 do scanner de produção, por isso update_closing_odds.py nunca
tinha picks elegíveis para o fetch de odds_fecho. Usa mocks do BSD Sports
API — sem rede, sem secrets.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.settle_sharp1x2 import resolve_outcome, settle


def _iso(hours_ago: float) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(**kwargs) -> dict:
    base = {
        "id": "12345_home_sh",
        "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": _iso(3.0),
        "outcome": "HOME",
        "resultado_outcome": "",
        "odds_fecho": "",
    }
    return {**base, **kwargs}


class TestResolveOutcome:
    def test_home_win(self):
        assert resolve_outcome(2, 1) == "HOME"

    def test_away_win(self):
        assert resolve_outcome(0, 3) == "AWAY"

    def test_draw(self):
        assert resolve_outcome(1, 1) == "DRAW"

    def test_invalid_scores_return_none(self):
        assert resolve_outcome(None, None) is None
        assert resolve_outcome("?", "?") is None


class TestSettle:
    def _run(self, picks: list[dict], fetch_result_side_effect=None):
        import pipeline.settle_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text(json.dumps(picks), encoding="utf-8")

            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", "fake_key"),
                patch.object(mod, "fetch_event_result", side_effect=fetch_result_side_effect),
                patch.object(mod, "git_commit_push"),
            ):
                mod.settle()

            return json.loads(picks_file.read_text())

    def test_too_early_left_untouched(self):
        """Pick com KO há < 2.5h não é tocado — ainda a decorrer/cedo demais."""
        picks = [_pick(id="1_home_sh", data=_iso(1.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: pytest.fail("não devia chamar fetch"))
        assert out[0]["resultado_outcome"] == ""
        assert "settlement_error" not in out[0]

    def test_already_settled_pick_skipped(self):
        """Pick já settled (WIN/LOSS/VOID) nunca gera novo fetch."""
        picks = [_pick(id="2_home_sh", resultado_outcome="WIN", data=_iso(10.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: pytest.fail("não devia chamar fetch"))
        assert out[0]["resultado_outcome"] == "WIN"

    def test_home_win_resolves_to_win(self):
        """Pick outcome=HOME, resultado real HOME → WIN."""
        picks = [_pick(id="3_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 2, "away_score": 0},
        )
        assert out[0]["resultado_outcome"] == "WIN"
        assert out[0]["resultado_jogo"] == "2-0"
        assert "settlement_error" not in out[0]

    def test_home_pick_loses_when_away_wins(self):
        """Pick outcome=HOME, resultado real AWAY → LOSS."""
        picks = [_pick(id="4_home_sh", outcome="HOME", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "finished", "home_score": 0, "away_score": 1},
        )
        assert out[0]["resultado_outcome"] == "LOSS"

    def test_cancelled_event_marks_void(self):
        """Evento cancelado/adiado → VOID, nunca WIN/LOSS forçado."""
        picks = [_pick(id="5_home_sh", data=_iso(3.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "postponed", "home_score": None, "away_score": None},
        )
        assert out[0]["resultado_outcome"] == "VOID"

    def test_fetch_failure_within_window_stays_pending_no_error(self):
        """Falha de fetch dentro da janela (< 48h) — ainda tenta na próxima corrida,
        sem marcar erro definitivo."""
        picks = [_pick(id="6_home_sh", data=_iso(3.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: None)
        assert out[0]["resultado_outcome"] == ""
        assert "settlement_error" not in out[0]

    def test_fetch_failure_past_deadline_gets_explicit_error(self):
        """Falha de fetch além das 48h → settlement_error explícito com timestamp
        (nunca pendente silenciosamente para sempre)."""
        picks = [_pick(id="7_home_sh", data=_iso(50.0))]
        out = self._run(picks, fetch_result_side_effect=lambda eid: None)
        assert out[0]["resultado_outcome"] == ""
        assert out[0]["settlement_error"] == "bsd_fetch_falhou_apos_48h"
        assert out[0]["settlement_error_at"]

    def test_not_finished_past_deadline_gets_explicit_error(self):
        """Evento sem estado finished mesmo 48h depois → erro explícito, não WIN/LOSS inventado."""
        picks = [_pick(id="8_home_sh", data=_iso(50.0))]
        out = self._run(
            picks,
            fetch_result_side_effect=lambda eid: {"status": "notstarted", "home_score": None, "away_score": None},
        )
        assert out[0]["resultado_outcome"] == ""
        assert "nao_finalizado_apos_48h" in out[0]["settlement_error"]

    def test_no_bsd_api_key_aborts_cleanly(self):
        import pipeline.settle_sharp1x2 as mod

        with tempfile.TemporaryDirectory() as tmp:
            picks_file = Path(tmp) / "picks_1x2.json"
            picks_file.write_text("[]", encoding="utf-8")
            with (
                patch.object(mod, "PICKS_FILE", picks_file),
                patch.object(mod, "BSD_API_KEY", ""),
                patch.object(mod, "git_commit_push"),
            ):
                with pytest.raises(SystemExit) as ctx:
                    mod.settle()
                assert ctx.value.code == 0
