"""
tests/pipeline/test_update_closing_odds_over25.py
----------------------------------------------------
Testes para pipeline/update_closing_odds_over25.py (Bloco I, 10 ago 2026).

Duas diferenças deliberadas em relação a test_update_closing_odds.py
(Sharp 1X2), ambas por decisão explícita do Nuno nesta sessão:

1. A janela ancora no KO (campo "data"), NUNCA em settled_at — o Over 2.5
   não tem settle_over25.py, e captureClosingOdds() (browser) também não
   depende de settlement, só de estar perto do kickoff.
2. A fórmula do CLV usa devig (CLV_DEVIG=1.01), igual ao calcCLV() do
   browser — divergente, de propósito, da fórmula sem devig do
   update_closing_odds.py (Sharp 1X2). Ver docstring do módulo.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


def _iso(hours_from_now: float) -> str:
    """hours_from_now > 0 → KO no futuro; < 0 → KO no passado."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pick(**kwargs) -> dict:
    base = {
        "id": "999_btts",
        "casa": "Arsenal", "fora": "Chelsea", "liga": "Premier League",
        "data": _iso(0.0),  # KO agora, por omissão — dentro da janela
        "odds_over": "1.85",
        "odds_over_close": "",
        "clv": "",
    }
    return {**base, **kwargs}


def _run(picks: list[dict], fetch_side_effect):
    import pipeline.update_closing_odds_over25 as mod

    with tempfile.TemporaryDirectory() as tmp:
        picks_file = Path(tmp) / "picks.json"
        picks_file.write_text(json.dumps(picks), encoding="utf-8")

        with (
            patch.object(mod, "PICKS_FILE", picks_file),
            patch.object(mod, "BSD_API_KEY", "fake_key"),
            patch.object(mod, "fetch_closing_odds_over25", side_effect=fetch_side_effect),
            patch.object(mod, "git_commit_push", return_value=True),
        ):
            mod.update()

        return json.loads(picks_file.read_text())


def _no_fetch(eid):
    raise AssertionError(f"não devia tentar fetch para event_id={eid}")


class TestUpdateClosingOddsOver25:
    def test_already_filled_skipped(self):
        picks = [_pick(id="1_btts", odds_over_close="1.9500")]
        out = _run(picks, fetch_side_effect=_no_fetch)
        assert out[0]["odds_over_close"] == "1.9500"

    def test_pick_without_ko_untouched_no_error(self):
        picks = [_pick(id="2_btts", data="")]
        out = _run(picks, fetch_side_effect=_no_fetch)
        assert out[0]["odds_over_close"] == ""
        assert "fetch_error" not in out[0]

    def test_too_far_before_ko_no_error_yet(self):
        """KO em 5h — fora da janela (WINDOW_BEFORE_KO_H=2.0), sem erro."""
        picks = [_pick(id="3_btts", data=_iso(5.0))]
        out = _run(picks, fetch_side_effect=_no_fetch)
        assert out[0]["odds_over_close"] == ""
        assert "fetch_error" not in out[0]

    def test_successful_fetch_sets_odds_and_clv_with_devig(self):
        """CLV usa devig (CLV_DEVIG=1.01) — igual ao calcCLV() do browser,
        deliberadamente diferente da fórmula sem devig do Sharp 1X2."""
        picks = [_pick(id="4_btts", odds_over="1.85", data=_iso(-0.5))]
        out = _run(picks, fetch_side_effect=lambda eid: 2.00)
        assert out[0]["odds_over_close"] == "2.0000"
        expected_clv_with_devig = round((1.85 / (2.00 * 1.01) - 1) * 100, 2)
        expected_clv_without_devig = round((1.85 / 2.00 - 1) * 100, 2)
        assert float(out[0]["clv"]) == expected_clv_with_devig
        assert float(out[0]["clv"]) != expected_clv_without_devig
        assert "fetch_error" not in out[0]

    def test_fetch_failure_sets_explicit_error(self):
        picks = [_pick(id="5_btts", data=_iso(0.0))]
        out = _run(picks, fetch_side_effect=lambda eid: None)
        assert out[0]["odds_over_close"] == ""
        assert out[0]["fetch_error"] == "bsd_sem_odds_pinnacle_over25"
        assert out[0]["fetch_error_at"]

    def test_window_closed_marks_terminal_error_once(self):
        """KO há 3h (> WINDOW_AFTER_KO_H=1.5h) sem nunca ter conseguido
        odds_over_close → erro terminal, marcado uma única vez."""
        picks = [_pick(id="6_btts", data=_iso(-3.0))]
        out = _run(picks, fetch_side_effect=_no_fetch)
        assert out[0]["fetch_error"] == "janela_fechada_sem_odds_over_close"
        assert out[0]["fetch_error_at"]

        first_error_at = out[0]["fetch_error_at"]
        out2 = _run(out, fetch_side_effect=_no_fetch)
        assert out2[0]["fetch_error_at"] == first_error_at

    def test_success_clears_previous_fetch_error(self):
        picks = [_pick(
            id="7_btts", data=_iso(0.0),
            fetch_error="bsd_sem_odds_pinnacle_over25", fetch_error_at="2026-01-01T00:00:00Z",
        )]
        out = _run(picks, fetch_side_effect=lambda eid: 2.10)
        assert out[0]["odds_over_close"] == "2.1000"
        assert "fetch_error" not in out[0]
        assert "fetch_error_at" not in out[0]

    def test_invalid_odds_over_sets_explicit_error(self):
        picks = [_pick(id="8_btts", odds_over="0", data=_iso(0.0))]
        out = _run(picks, fetch_side_effect=lambda eid: 2.00)
        assert out[0]["fetch_error"] == "odds_over_invalida"
        assert out[0]["odds_over_close"] == ""

    def test_eligibility_uses_ko_not_settlement(self):
        """Ao contrário do Sharp 1X2, a elegibilidade aqui nunca depende de
        resultado_outcome/settled_at — só da proximidade do KO. Um pick sem
        qualquer campo de settlement ainda é elegível dentro da janela."""
        picks = [_pick(id="9_btts", data=_iso(-1.0))]
        assert "resultado_outcome" not in picks[0]
        assert "settled_at" not in picks[0]
        out = _run(picks, fetch_side_effect=lambda eid: 1.95)
        assert out[0]["odds_over_close"] == "1.9500"

    def test_malformed_ko_treated_as_absent_no_error(self):
        picks = [_pick(id="10_btts", data="não-é-uma-data")]
        out = _run(picks, fetch_side_effect=_no_fetch)
        assert out[0]["odds_over_close"] == ""
        assert "fetch_error" not in out[0]

    def test_event_id_extracted_from_composite_pick_id(self):
        """id '209508_btts' → event_id '209508' passado ao fetch."""
        picks = [_pick(id="209508_btts", data=_iso(0.0))]
        seen = {}

        def fake_fetch(eid):
            seen["eid"] = eid
            return 2.00

        _run(picks, fetch_side_effect=fake_fetch)
        assert seen["eid"] == "209508"
